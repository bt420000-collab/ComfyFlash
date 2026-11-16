import argparse
from contextlib import nullcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine import Flash, agent, setup_checks
from engine.bank import TONES
from engine.runtime import Runtime


class MemoryTests(unittest.TestCase):
    def test_scheduler_uses_physical_free_memory_of_the_matching_gpu(self):
        devices = [
            {"index": 0, "uuid": "b", "total_mib": 16300, "free_mib": 15172, "ready": True},
            {"index": 1, "uuid": "a", "total_mib": 16300, "free_mib": 15172, "ready": True},
        ]
        physical = [
            {"index": 0, "uuid": "GPU-a", "total_mib": 16300, "free_mib": 16000, "ready": False},
            {"index": 1, "uuid": "GPU-b", "total_mib": 16300, "free_mib": 2300, "ready": False},
        ]
        runtime = {"gpus": devices, "ram_mib": 49000, "free_ram_mib": 30000}
        with patch.object(setup_checks, "nvidia_inventory", return_value=physical):
            report = setup_checks.hardware_report(runtime)
        self.assertEqual(report["active_devices"], [1])
        self.assertEqual(report["gpus"][0]["free_mib"], 2300)
        self.assertEqual(report["gpus"][1]["free_mib"], 15172)
        self.assertEqual(devices[0]["free_mib"], 15172)

    def test_typed_oom_unloads_models_and_the_next_request_can_run(self):
        failure = RuntimeError("allocation failed (code 2)")
        runtime = Mock()
        runtime.dynamic_vram = True
        runtime.generation.side_effect = nullcontext
        runtime.is_oom.side_effect = lambda exc: exc is failure
        with TemporaryDirectory() as folder:
            flash = Flash(config={"comfyui_root": folder})
            flash._rt = runtime
            output = str(Path(folder, "test.png"))
            model = ("unet", "oom-test")

            def generate(run):
                if not runtime.hard_reset.called:
                    flash._cache[model] = object()
                    flash._tick[model] = 1
                    raise failure
                return output

            with patch.object(flash, "model_path"), patch.object(TONES["t3"], "play", side_effect=generate):
                result = flash.play("t3", prompt="test", output=output)
                self.assertEqual(result["error"]["code"], "OOM")
                runtime.hard_reset.assert_called_once()
                self.assertFalse(flash._cache)
                self.assertFalse(flash._tick)
                self.assertTrue(flash.play("t3", prompt="test", output=output)["ok"])

    def test_cleanup_error_does_not_replace_the_original_stage_error(self):
        runtime = Runtime.__new__(Runtime)
        runtime.dynamic_vram = True
        prefetch = Mock()
        prefetch.cleanup_prefetch_queues.side_effect = RuntimeError("cleanup failed")
        with patch.object(runtime, "_mod", return_value=prefetch), self.assertLogs("engine.runtime", level="WARNING"):
            with self.assertRaisesRegex(ValueError, "original error"):
                with runtime.stage():
                    raise ValueError("original error")

    def test_worker_preserves_completed_calls_when_native_memory_cleanup_aborts(self):
        ok = {"ok": True, "file": "done.mp4"}
        run = Mock(stdout=json.dumps({"position": 0, "result": ok}) + "\n",
                   stderr="RuntimeError: HostBuffer.truncate failed\nC++ abort\n", returncode=-1073740791)
        args = argparse.Namespace(config=Path("config.json"))
        requests = [(0, Path("first.json")), (2, Path("second.json"))]
        with patch.object(agent.subprocess, "run", return_value=run):
            first, second = agent.worker_results(args, requests, 0)
        self.assertEqual(first, (0, {**ok, "device": "cuda:0"}))
        self.assertEqual(second[0], 2)
        self.assertEqual(second[1]["error"]["code"], "WORKER_MEMORY_ERROR")
        self.assertIn("HostBuffer.truncate failed", second[1]["error"]["message"])

    def test_batch_retries_only_memory_failures_after_other_workers_finish(self):
        finished = {"ok": True, "file": "already.mp4", "device": "cuda:0"}
        recovered = {"ok": True, "file": "recovered.mp4", "device": "cuda:0"}
        oom = {"ok": False, "error": {"code": "OOM"}}
        invalid = {"ok": False, "error": {"code": "BAD_INPUT"}}
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            agent.write_text(config, agent.json_text({"comfyui_root": folder}))
            paths = [root / f"{i}.json" for i in range(3)]
            for i, path in enumerate(paths):
                agent.write_text(path, agent.json_text({"output": str(root / f"{i}.mp4")}))
            hardware = {"active_devices": [0, 1], "gpus": [
                {"index": 0, "free_mib": 15000, "total_mib": 16000, "ready": True},
                {"index": 1, "free_mib": 14000, "total_mib": 16000, "ready": True},
            ]}
            def worker(args, requests, index):
                positions = [pos for pos, _ in requests]
                if positions == [0, 2]:
                    return [(0, finished), (2, invalid)]
                self.assertEqual(positions, [1])
                return [(1, oom if index == 1 else recovered)]

            with patch.object(agent, "probe_python"), patch.object(agent, "hardware_report", return_value=hardware), patch.object(
                agent, "worker_results", side_effect=worker
            ) as run:
                result = agent.play(argparse.Namespace(config=config, request=paths))
            self.assertEqual(run.call_count, 3)
        self.assertEqual(result["results"], [finished, recovered, invalid])
        self.assertEqual(result["serial_retries"], 1)
