from contextlib import closing, nullcontext
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from RSI import RSIStore, RSIStoreError
from engine import Flash
from engine.bank import TONES


class RSIStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "rsi.sqlite3"
        self.store = RSIStore(self.db_path)

    def test_feedback_can_be_added_filtered_and_updated(self):
        run_id = self.store.record_run(
            {"ok": True, "tone": "t2", "file": "shot.mp4", "seed": 42, "elapsed_s": 12.5,
             "width": 768, "height": 448, "frames": 39, "duration": 1.625, "fps": 24,
             "timing_s": {"sampling": 9.1}},
            tone="t2", spec={"output": "video", "models": {"diffusion": "h3.safetensors"}},
            request={"prompt": "a private prompt", "resolution": "768x448", "duration": 1.6,
                     "frames": 39, "seed": 42, "advanced": {"steps": 6}, "images": ["ref.png"]},
            device="cuda:1",
        )
        note = self.store.add_feedback(
            title="参考图处理有改进空间",
            observation="Agent 发现第二张输入图未参与本次生成。",
            author="codex",
            category="optimization",
            severity="medium",
            impact="用户可能误以为多张参考图都生效。",
            suggestion="在生成前校验 Tone 声明的最大输入数量。",
            run_id=run_id,
            tone="t2",
            artifact_path="shot.mp4",
            tags=["reference", "validation"],
            context={"step": "preflight", "observed_count": 2},
        )
        self.assertEqual(note["status"], "open")
        self.assertEqual(note["author"], "codex")
        self.assertEqual(note["context"]["observed_count"], 2)
        self.assertEqual(len(self.store.list_feedback(status="open", tone="t2")), 1)

        updated = self.store.update_feedback(
            note["feedback_id"], status="planned", resolution="安排在输入契约阶段修复。"
        )
        self.assertEqual(updated["status"], "planned")
        self.assertEqual(updated["resolution"], "安排在输入契约阶段修复。")
        self.assertEqual(self.store.list_feedback(status="open"), [])

    def test_run_record_stores_prompt_hash_not_prompt_text(self):
        prompt = "do not persist this private prompt"
        run_id = self.store.record_run(
            {"ok": False, "tone": "t1", "seed": 7, "elapsed_s": 3.0,
             "error": {"code": "OOM", "message": "out of memory"}},
            tone="t1", spec={"output": "video", "models": {"diffusion": "model.safetensors"}},
            request={"prompt": prompt, "resolution": "768x448", "duration": 5,
                     "frames": 124, "seed": 7, "advanced": {"steps": 6}, "images": []},
            device="cuda:0",
        )
        run = self.store.list_runs(status="failed")[0]
        self.assertEqual(run["run_id"], run_id)
        self.assertEqual(run["error_code"], "OOM")
        self.assertEqual(run["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        self.assertNotIn(prompt, str(run))
        self.assertEqual(run["requested_frame_count"], 124)

    def test_unknown_run_reference_is_rejected_clearly(self):
        with self.assertRaisesRegex(RSIStoreError, "run_id 不存在"):
            self.store.add_feedback(
                title="孤立点评", observation="没有对应的任务", run_id="missing-run"
            )

    def test_concurrent_agent_notes_are_persisted(self):
        def add(index):
            return self.store.add_feedback(
                title=f"note-{index}", observation="parallel note", author="agent",
                category="idea", severity="info",
            )["feedback_id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(add, range(32)))
        self.assertEqual(len(set(ids)), 32)
        self.assertEqual(len(self.store.list_feedback(limit=100)), 32)


    def test_agent_cli_can_record_and_list_an_agent_comment(self):
        root = Path(__file__).resolve().parents[2]
        script = root / "engine" / "agent.py"
        command = [
            sys.executable, "-B", str(script), "rsi", "--db", str(self.db_path), "note",
            "--title", "CLI 点评", "--observation", "Agent 发现一个可复现的问题",
            "--category", "bug", "--severity", "high", "--author", "codex",
            "--tone", "t2", "--tag", "cli",
        ]
        created = subprocess.run(command, check=True, capture_output=True, text=True)
        import json
        feedback_id = json.loads(created.stdout)["feedback"]["feedback_id"]
        listed = subprocess.run(
            [sys.executable, "-B", str(script), "rsi", "--db", str(self.db_path),
             "list", "--status", "open", "--tone", "t2"],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(listed.stdout)["feedback"][0]["feedback_id"], feedback_id)
        updated = subprocess.run(
            [sys.executable, "-B", str(script), "rsi", "--db", str(self.db_path),
             "update", feedback_id, "--status", "planned", "--resolution", "准备下一轮处理"],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(updated.stdout)["feedback"]["status"], "planned")
        shown = subprocess.run(
            [sys.executable, "-B", str(script), "rsi", "--db", str(self.db_path), "show", feedback_id],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(shown.stdout)["feedback"]["resolution"], "准备下一轮处理")

    def test_separate_agent_processes_can_append_to_the_same_database(self):
        root = Path(__file__).resolve().parents[2]
        script = root / "engine" / "agent.py"

        def append(index):
            return subprocess.run(
                [sys.executable, "-B", str(script), "rsi", "--db", str(self.db_path),
                 "note", "--title", f"worker-{index}", "--observation", "parallel process write",
                 "--author", f"agent-{index}"],
                check=True, capture_output=True, text=True,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(append, range(4)))
        self.assertEqual(len(results), 4)
        self.assertEqual(len(self.store.list_feedback(limit=20)), 4)

    def test_database_uses_wal_and_rejects_oversized_context(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        with self.assertRaisesRegex(RSIStoreError, "32 KB"):
            self.store.add_feedback(
                title="context 太大", observation="test", context={"payload": "x" * 33000}
            )

    def test_summary_reports_schema_and_open_feedback(self):
        self.store.add_feedback(title="one", observation="observation")
        summary = self.store.summary()
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["open_feedback"], 1)


class FlashRSIIntegrationTests(unittest.TestCase):
    def test_play_automatically_records_run_and_returns_run_id(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "history.sqlite3"
            output = root / "result.png"
            flash = Flash(config={
                "comfyui_root": str(root),
                "rsi_db_path": str(db_path),
            })
            runtime = Mock()
            runtime.dynamic_vram = True
            runtime.generation.side_effect = nullcontext
            flash._rt = runtime
            with patch.object(flash, "model_path"), patch.object(
                TONES["t3"], "play", return_value=str(output)
            ):
                result = flash.play(
                    "t3", prompt="private prompt", resolution="1k", seed=123,
                    output=str(output),
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["run_id"])
            self.assertEqual(result["device"], "cuda:0")
            run = RSIStore(db_path).list_runs()[0]
            self.assertEqual(run["run_id"], result["run_id"])
            self.assertEqual(run["tone"], "t3")
            self.assertNotIn("private prompt", str(run))
            flash.close()

    def test_rsi_database_failure_never_breaks_generation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            # A directory at the DB path forces SQLite initialization to fail.
            db_path = root / "not-a-database"
            db_path.mkdir()
            flash = Flash(config={"comfyui_root": str(root), "rsi_db_path": str(db_path)})
            runtime = Mock()
            runtime.dynamic_vram = True
            runtime.generation.side_effect = nullcontext
            flash._rt = runtime
            with patch.object(flash, "model_path"), patch.object(
                TONES["t3"], "play", return_value=str(root / "result.png")
            ), self.assertLogs("engine.core", level="WARNING"):
                result = flash.play("t3", prompt="test", output=str(root / "result.png"))
            self.assertTrue(result["ok"])
            self.assertNotIn("run_id", result)
            flash.close()

    def test_rsi_can_be_disabled_for_a_local_run(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "history.sqlite3"
            flash = Flash(config={
                "comfyui_root": str(root), "rsi_db_path": str(db_path), "rsi_enabled": False,
            })
            runtime = Mock()
            runtime.dynamic_vram = True
            runtime.generation.side_effect = nullcontext
            flash._rt = runtime
            with patch.object(flash, "model_path"), patch.object(
                TONES["t3"], "play", return_value=str(root / "result.png")
            ):
                result = flash.play("t3", prompt="test", output=str(root / "result.png"))
            self.assertTrue(result["ok"])
            self.assertNotIn("run_id", result)
            self.assertFalse(db_path.exists())
            manual = flash.rsi.add_feedback(
                title="手动点评仍可用", observation="关闭自动运行记录不关闭 RSI 点评。"
            )
            self.assertEqual(manual["status"], "open")
            flash.close()


if __name__ == "__main__":
    unittest.main()
