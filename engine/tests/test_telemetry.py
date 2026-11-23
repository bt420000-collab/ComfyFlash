import contextlib
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine import Flash
from engine import agent
from engine.bank import TONES


class TelemetryTests(unittest.TestCase):
    def test_failed_attempt_is_persisted_without_request_text(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"comfyui_root": folder, "telemetry_dir": str(root / "history"),
                      "rsi_enabled": False}
            with Flash(config) as flash:
                result = flash.play("t3", prompt="private text", output=root / "image.png")
            self.assertFalse(result["ok"])
            record = result["telemetry"]
            self.assertEqual(record["error_code"], "MISSING_MODEL")
            self.assertEqual(record["failure_stage"], "preflight")
            lines = Path(result["telemetry_path"]).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertNotIn("private text", lines[0])
            self.assertEqual(json.loads(lines[0])["attempt_id"], record["attempt_id"])

    def test_corrupt_database_keeps_success_and_reports_warning(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "broken.sqlite3"
            database.write_bytes(b"broken database")
            config = {"comfyui_root": folder, "rsi_db_path": str(database), "telemetry_enabled": False}
            with Flash(config) as flash:
                fake_rt = Mock(dynamic_vram=True, device="cpu")
                fake_rt.generation.side_effect = contextlib.nullcontext
                fake_rt.versions.return_value = {}
                flash._rt = fake_rt
                with patch.object(flash, "_check_models"), patch.object(TONES["t3"], "play", return_value="image.png"):
                    result = flash.play("t3", prompt="blue square", output=root / "image.png")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["telemetry"]["status"], "success")
            self.assertEqual(result["warnings"][0]["code"], "RSI_STORAGE_UNAVAILABLE")

    def test_delivered_image_history_can_be_queried_by_policy_and_resolution(self):
        import numpy as np
        from RSI import RSIStore

        with TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "rsi.sqlite3"
            config = {"comfyui_root": folder, "rsi_db_path": str(database), "telemetry_dir": str(root / "runs")}
            output = root / "image.png"
            with Flash(config) as flash:
                for dependency in flash.tone_info("t3")["models"]:
                    path = Path(dependency["path"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic test weight")
                fake_rt = Mock(dynamic_vram=False, device="cpu")
                fake_rt.generation.side_effect = contextlib.nullcontext
                fake_rt.versions.return_value = {"device": "cpu"}
                flash._rt = fake_rt
                with patch.object(TONES["t3"], "play", side_effect=lambda run: run.save_image(np.zeros((32, 32, 3)))):
                    result = flash.play("t3", prompt="private prompt", output=output, resolution="32x32")
            self.assertTrue(result["ok"], result)
            self.assertTrue(output.is_file())
            store = RSIStore(database)
            rows = store.query_history(tone="t3", resolution="32x32", policy_version="engine-v0.2-p1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["artifact_path"], result["artifact"]["inspection"]["metadata"])
            self.assertEqual(rows[0]["status"], "success")
            self.assertTrue(rows[0]["validation"]["ok"])
            self.assertIn("encoding", rows[0]["timings"])
            self.assertNotIn("private prompt", Path(result["telemetry_path"]).read_text(encoding="utf-8"))

    def test_preflight_does_not_create_local_history(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"comfyui_root": folder, "telemetry_dir": str(root / "history"),
                      "rsi_db_path": str(root / "rsi.sqlite3")}
            with Flash(config) as flash:
                flash.preflight("t3", prompt="test", output=root / "image.png")
            self.assertFalse((root / "history").exists())
            self.assertFalse((root / "rsi.sqlite3").exists())

    def test_batch_parent_persists_original_failed_attempt_before_retry(self):
        from RSI import RSIStore

        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            database = root / "rsi.sqlite3"
            agent.write_text(config, agent.json_text({"comfyui_root": folder, "device": "cuda:1",
                "rsi_db_path": str(database), "telemetry_dir": str(root / "history")}))
            paths = [root / f"{i}.json" for i in range(2)]
            for i, path in enumerate(paths):
                agent.write_text(path, agent.json_text({"tone": "t3", "prompt": "test", "output": str(root / f"{i}.png")}))
            hardware = {"active_devices": [0, 1], "free_ram_mib": 50000, "gpus": [
                {"index": n, "ready": True, "free_mib": 15000, "total_mib": 16000} for n in range(2)],
            }
            failure = {"ok": False, "error": {"code": "RUNTIME_ERROR", "message": "HostBuffer.truncate failed"}}
            with patch.object(Flash, "model_path", return_value="fixture"), patch.object(
                agent, "probe_python"
            ), patch.object(agent, "nvidia_telemetry", return_value=[]), patch.object(
                agent, "hardware_report", return_value=hardware
            ), patch.object(agent, "worker_results", side_effect=[[(0, failure), (1, {"ok": True})], [(0, {"ok": True})]]) as run:
                batch = agent.play(argparse.Namespace(config=config, request=paths))
            self.assertTrue(batch["ok"], batch)
            self.assertEqual([call.args[2] for call in run.call_args_list], [1, 1])
            self.assertEqual(batch["scheduling"][0]["selected_device"], "cuda:1")
            lines = Path(batch["telemetry_path"]).read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            self.assertEqual([(r["request_id"], r["attempt_index"], r["status"]) for r in records],
                             [(0, 0, "failed"), (0, 1, "success"), (1, 0, "success")])
            store = RSIStore(database)
            recovered_id = batch["results"][0]["run_id"]
            self.assertEqual(recovered_id, records[1]["attempt_id"])
            self.assertEqual(len(store.query_history(tone="t3")), 3)
            note = store.add_feedback(title="Batch retry", observation="Recovered", run_id=recovered_id)
            self.assertEqual(note["run_id"], recovered_id)
