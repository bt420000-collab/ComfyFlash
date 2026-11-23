import json
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from RSI import RSIStore
from RSI.store import SCHEMA_VERSION


def run_summary(attempt_id, **fields):
    return {
        "attempt_id": attempt_id,
        "request_id": "request-1",
        "schema_version": 1,
        "tone": "t2",
        "model": "H3-Medium.safetensors",
        "resolution": "640x384",
        "duration_s": 2.5,
        "device": "GPU-00000000-0000-0000-0000-000000000000",
        "workload_fingerprint": "a" * 64,
        "device_fingerprint": "b" * 64,
        "status": "success",
        "elapsed_s": 1.25,
        "artifact_path": "output/镜头.json",
        "telemetry_path": "output/execution.jsonl",
        **fields,
    }


class RSIStoreTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.database = self.root / "记录" / "rsi.sqlite3"
        self.store = RSIStore(self.database)

    def test_lazy_creation_projection_and_filtered_history(self):
        self.assertFalse(self.database.parent.exists())
        self.assertEqual(self.store.record_run_summaries([]), 0)
        self.assertFalse(self.database.exists())
        excluded = "private-prompt-must-not-be-written"
        self.assertEqual(self.store.record_run_summaries([
            run_summary(
                "one", prompt=excluded, media=b"audio-latent", peak_memory_bytes=24,
                timings={"sample_s": 0.75, "extra": excluded},
                validation={"ok": True, "details": excluded},
            ),
            run_summary("two", status="oom", device="unavailable", error_code="CUDA_OOM"),
            run_summary("three", tone="t3", model="z-image", resolution="512x512", duration_s=None),
        ]), 3)
        self.assertEqual(self.store.record_run_summary(run_summary("two")), 0)
        rows = self.store.query_history(
            tone="t2", model="H3-Medium.safetensors", resolution="640x384",
            duration_s=2.5, device="GPU-00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual([row["attempt_id"] for row in rows], ["one"])
        self.assertEqual(rows[0]["artifact_path"], "output/镜头.json")
        self.assertEqual(rows[0]["peak_memory_bytes"], 24)
        self.assertEqual(rows[0]["timings"], {"sample_s": 0.75})
        self.assertEqual(rows[0]["validation"], {"ok": True})
        self.assertNotIn(excluded.encode(), self.database.read_bytes())
        self.assertNotIn(b"audio-latent", self.database.read_bytes())
        self.assertEqual(self.store.query_history(device="unavailable")[0]["status"], "oom")
        self.assertEqual([row["attempt_id"] for row in self.store.query_history(limit=2)], ["three", "two"])

    def test_malformed_known_field_rejects_entire_batch(self):
        self.store.initialize()
        for field in ({"elapsed_s": float("nan")}, {"timings": {"sample_s": [1, 2]}},
                      {"validation": {"ok": "yes"}}, {"schema_version": True},
                      {"artifact_path": b"raw-media"}, {"error_code": "verbose error message"}):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.record_run_summaries([run_summary("good"), run_summary("bad", **field)])
        self.assertEqual(self.store.query_history(), [])

    def test_records_typed_retry_metadata_for_preflight_failures(self):
        self.store.record_run_summary(run_summary(
            "retry", status="retry", tone=None, request_id=None, attempt_index=0,
            failure_stage="preflight", recovered_by_retry=True,
            peak_memory={"vram_used_bytes": 1024, "system_ram_used_bytes": 2048, "samples": [0, 1]},
            timings={"preflight": 0.1, "model_load": 0.2, "sampling": 0.3},
        ))
        row = self.store.query_history()[0]
        self.assertIsNone(row["tone"])
        self.assertIsNone(row["request_id"])
        self.assertEqual(row["attempt_index"], 0)
        self.assertEqual(row["failure_stage"], "preflight")
        self.assertIs(row["recovered_by_retry"], True)
        self.assertEqual(row["peak_memory"], {"vram_used_bytes": 1024, "system_ram_used_bytes": 2048})
        self.assertEqual(row["timings"], {"preflight": 0.1, "model_load": 0.2, "sampling": 0.3})

    def test_migration_upgrades_a_populated_v1_database_and_is_idempotent(self):
        from RSI.store import _MIGRATIONS
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            for statement in _MIGRATIONS[1]:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            conn.execute("PRAGMA user_version = 1")
            conn.execute(
                """INSERT INTO run_summaries
                   (attempt_id, request_id, schema_version, tone, status)
                   VALUES ('old-attempt', 'old-request', 1, 't2', 'oom')"""
            )
        self.assertEqual(self.store.initialize(), SCHEMA_VERSION)
        self.assertEqual(self.store.initialize(), SCHEMA_VERSION)
        old = self.store.query_history(tone="t2")[0]
        self.assertEqual((old["attempt_id"], old["status"]), ("old-attempt", "oom"))
        self.assertIsNone(old["policy_version"])
        self.assertEqual(self.store.record_run_summary(run_summary("new", error_code="CUDA_OOM")), 1)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall(),
                             [(version,) for version in range(1, SCHEMA_VERSION + 1)])

    def test_migration_preserves_main_run_and_feedback_through_backup_restore(self):
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript("""
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    tone TEXT, output_type TEXT, device TEXT, seed INTEGER, resolution TEXT,
                    width INTEGER, height INTEGER, requested_duration_s REAL, actual_duration_s REAL,
                    requested_frame_count INTEGER, actual_frame_count INTEGER, elapsed_s REAL,
                    output_path TEXT, error_code TEXT, error_message TEXT, prompt_sha256 TEXT,
                    prompt_chars INTEGER, input_image_count INTEGER NOT NULL DEFAULT 0,
                    models_json TEXT NOT NULL DEFAULT '{}', params_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}', environment_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE agent_feedback (
                    feedback_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    source TEXT NOT NULL, author TEXT NOT NULL, category TEXT NOT NULL,
                    severity TEXT NOT NULL, status TEXT NOT NULL, title TEXT NOT NULL,
                    observation TEXT NOT NULL, impact TEXT NOT NULL DEFAULT '',
                    suggestion TEXT NOT NULL DEFAULT '', run_id TEXT, task_id TEXT NOT NULL DEFAULT '',
                    tone TEXT NOT NULL DEFAULT '', artifact_path TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]', context_json TEXT NOT NULL DEFAULT '{}',
                    resolution TEXT NOT NULL DEFAULT '', resolved_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
                );
                INSERT INTO runs (run_id, created_at, status, tone, prompt_sha256, metrics_json)
                    VALUES ('legacy-run', '2026-01-01T00:00:00+00:00', 'success', 't2',
                            'legacy-hash', '{"actual_frame_count": 24}');
                INSERT INTO agent_feedback (
                    feedback_id, created_at, updated_at, source, author, category, severity,
                    status, title, observation, run_id, tags_json
                ) VALUES (
                    'legacy-note', '2026-01-01', '2026-01-01', 'agent', 'codex', 'bug',
                    'medium', 'open', 'Prior finding', 'Observation', 'legacy-run', '["review"]'
                );
                PRAGMA user_version = 1;
            """)

        self.assertEqual(self.store.check(), 1)
        self.assertEqual(self.store.initialize(), SCHEMA_VERSION)
        records = [run_summary("new-attempt")]
        legacy_runs = [{
            "run_id": "new-attempt", "result": {"ok": True, "file": "new.mp4"},
            "tone": "t2", "request": {"prompt": "private prompt"},
        }]
        self.assertEqual(self.store.record_run_summaries(records, legacy_runs=legacy_runs), 1)
        self.assertEqual(self.store.record_run_summaries(records, legacy_runs=legacy_runs), 0)
        new_feedback = self.store.add_feedback(
            title="New finding", observation="Observation", run_id="new-attempt",
        )
        backup = self.store.backup(self.root / "backup.sqlite3")
        restored = RSIStore.restore(backup, self.root / "restored.sqlite3")
        self.assertEqual(restored.check(), SCHEMA_VERSION)
        self.assertEqual(restored.query_history(tone="t2")[0]["attempt_id"], "new-attempt")
        runs = {run["run_id"]: run for run in restored.list_runs(tone="t2")}
        self.assertEqual(runs["legacy-run"]["metrics"]["actual_frame_count"], 24)
        self.assertEqual(runs["legacy-run"]["prompt_sha256"], "legacy-hash")
        self.assertEqual(runs["new-attempt"]["output_path"], "new.mp4")
        self.assertNotIn("private prompt", str(runs["new-attempt"]))
        self.assertEqual(restored.get_feedback("legacy-note")["tags"], ["review"])
        self.assertEqual(restored.list_feedback(run_id="legacy-run")[0]["feedback_id"], "legacy-note")
        self.assertEqual(restored.get_feedback(new_feedback["feedback_id"])["run_id"], "new-attempt")

    def test_run_summary_and_linkable_run_are_committed_together(self):
        self.store.initialize()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript("""
                CREATE TRIGGER reject_run BEFORE INSERT ON runs
                BEGIN
                    SELECT RAISE(ABORT, 'could not save run details');
                END;
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "could not save run details"):
            self.store.record_run_summaries(
                [run_summary("attempt")],
                legacy_runs=[{"run_id": "attempt", "result": {"ok": True}}],
            )
        self.assertEqual(self.store.query_history(), [])
        self.assertEqual(self.store.list_runs(), [])

    def test_future_schema_is_rejected_without_modification(self):
        self.store.initialize()
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("PRAGMA user_version = 900")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "900"):
            self.store.query_history()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 900)

    def test_create_refuses_to_claim_an_existing_unrelated_database(self):
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("CREATE TABLE sqlitex (value TEXT)")
        original = self.database.read_bytes()
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.initialize()
        self.assertEqual(self.database.read_bytes(), original)

    def test_check_rejects_a_database_with_a_missing_record_table(self):
        self.store.initialize()
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("DROP TABLE experiments")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.check()

    def test_policy_proposal_experiment_and_rollback_links(self):
        self.store.save_policy_revision(version="base-v1", target="gpu-scheduling")
        self.store.save_policy_revision(
            version="candidate-v2", target="gpu-scheduling", parent_version="base-v1",
            rollback_to="base-v1", model="H3", evidence_path="bench/notes.json",
        )
        self.store.add_proposal(
            proposal_id="prop-1", target="gpu-scheduling", baseline_policy_version="base-v1",
            candidate_policy_version="candidate-v2", hypothesis="Reduce load time",
            test_set="bench-v1", metrics=["elapsed_s", "validity_rate"], rollback_to="base-v1",
        )
        with self.assertRaises(ValueError):
            self.store.record_proposal_decision("prop-1", decision="adopted", evidence="bench/review.json")
        self.assertEqual(self.store.query_proposals(policy_version="base-v1")[0]["decision"], "pending")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_proposal(
                proposal_id="bad", target="gpu-scheduling", baseline_policy_version="base-v1",
                candidate_policy_version="candidate-v2", hypothesis="Reduce load time",
                test_set="bench-v1", metrics=["elapsed_s"], rollback_to="candidate-v2",
            )
        self.store.record_experiment(
            experiment_id="exp-1", proposal_id="prop-1", test_set="bench-v1",
            baseline_metrics={"elapsed_s": 12.0, "validity_rate": 1.0},
            candidate_metrics={"elapsed_s": 10.0, "validity_rate": 1.0},
            evidence="bench/run.json", conclusion="Candidate reduced warm-up time",
        )
        self.store.record_proposal_decision(
            "prop-1", decision="adopted", evidence="bench/review.json",
        )
        self.assertEqual(self.store.query_proposals(policy_version="base-v1")[0]["decision"], "adopted")
        self.store.record_proposal_decision(
            "prop-1", decision="rolled_back", evidence="bench/rollback.json",
        )
        proposal = self.store.query_proposals(policy_version="candidate-v2")[0]
        self.assertEqual(
            (proposal["decision"], proposal["rollback_to"], proposal["decision_evidence"]),
            ("rolled_back", "base-v1", "bench/rollback.json"),
        )
        self.assertEqual(proposal["adoption_evidence"], "bench/review.json")
        experiment = self.store.query_experiments(policy_version="base-v1")[0]
        self.assertEqual((experiment["proposal_id"], experiment["candidate_policy_version"]),
                         ("prop-1", "candidate-v2"))
        self.assertEqual(experiment["candidate_metrics"]["elapsed_s"], 10.0)
        revision = self.store.query_policy_versions(policy_version="candidate-v2")[0]
        self.assertEqual((revision["parent_version"], revision["rollback_to"]), ("base-v1", "base-v1"))
        self.store.record_run_summary(run_summary("run-under-test", policy_version="candidate-v2"))
        self.assertEqual(self.store.query_history(policy_version="candidate-v2")[0]["attempt_id"], "run-under-test")
        self.assertEqual(self.store.query_history(policy_version="base-v1"), [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_policy_revision(version="bad", target="gpu-scheduling", rollback_to="missing")

    def test_online_backup_and_restore_to_new_paths(self):
        self.store.record_run_summary(run_summary("before-backup"))
        self.store.save_policy_revision(version="base-v1", target="gpu-scheduling")
        backup = self.root / "copies" / "rsi.backup.sqlite3"
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute(
                "INSERT INTO run_summaries (attempt_id, schema_version, status) VALUES ('uncommitted', 1, 'success')",
            )
            self.assertEqual(self.store.backup(backup), backup)
            writer.rollback()
        self.store.record_run_summary(run_summary("after-backup"))
        with self.assertRaises(FileExistsError):
            self.store.backup(backup)
        restored_path = self.root / "restored" / "rsi.sqlite3"
        restored = RSIStore.restore(backup, restored_path)
        self.assertEqual(restored.check(), SCHEMA_VERSION)
        self.assertEqual([row["attempt_id"] for row in restored.query_history()], ["before-backup"])
        self.assertEqual(restored.query_policy_versions()[0]["version"], "base-v1")
        with self.assertRaises(FileExistsError):
            RSIStore.restore(backup, self.database)
        self.assertEqual([row["attempt_id"] for row in self.store.query_history()],
                         ["after-backup", "before-backup"])
        corrupt = self.root / "invalid.sqlite3"
        corrupt.write_text("not sqlite", encoding="utf-8")
        unused = self.root / "invalid-copy.sqlite3"
        with self.assertRaises(sqlite3.DatabaseError):
            RSIStore.restore(corrupt, unused)
        self.assertFalse(unused.exists())

    def test_busy_timeout_and_concurrent_writes_from_four_independent_processes(self):
        self.store.initialize()
        code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from RSI import RSIStore
database, marker, prefix = sys.argv[2:]
print('ready', flush=True)
assert sys.stdin.readline().strip() == 'start'
Path(marker).touch()
store = RSIStore(database, timeout_s=5)
print(store.record_run_summaries([
    {'attempt_id': f'{prefix}-{i}', 'request_id': prefix, 'tone': 't2',
     'schema_version': 1, 'status': 'success', 'elapsed_s': 0.5} for i in range(30)
]), flush=True)
"""
        repo = Path(__file__).resolve().parents[2]
        children = []
        try:
            with closing(sqlite3.connect(self.database)) as lock:
                lock.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    RSIStore(self.database, timeout_s=0.08).record_run_summary(run_summary("timed-out"))
                self.assertGreaterEqual(time.monotonic() - started, 0.06)
                markers = [self.root / f"worker-{i}.ready" for i in range(4)]
                for i, marker in enumerate(markers):
                    child = subprocess.Popen(
                        [sys.executable, "-B", "-c", code, str(repo), str(self.database), str(marker), str(i)],
                        cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                    )
                    children.append(child)
                    self.assertEqual(child.stdout.readline().strip(), "ready")
                for child in children:
                    child.stdin.write("start\n")
                    child.stdin.flush()
                deadline = time.monotonic() + 5
                while not all(marker.exists() for marker in markers) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(all(marker.exists() for marker in markers))
                self.assertTrue(all(child.poll() is None for child in children))
                lock.rollback()
            for child in children:
                stdout, stderr = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(stdout.strip(), "30")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)
        rows = self.store.query_history(limit=200)
        self.assertEqual(len(rows), 120)
        self.assertEqual({row["request_id"] for row in rows}, {"0", "1", "2", "3"})

    def test_cli_reads_history_and_checks_backup(self):
        self.store.record_run_summary(run_summary("cli-attempt"))
        backup = self.root / "cli-backup.sqlite3"
        repo = Path(__file__).resolve().parents[2]

        def call(*args):
            result = subprocess.run(
                [sys.executable, "-B", str(repo / "RSI" / "__main__.py"), "--db", str(self.database), *args],
                cwd=repo, check=True, capture_output=True, encoding="utf-8",
            )
            return json.loads(result.stdout)

        self.assertEqual(call("history", "--tone", "t2")[0]["attempt_id"], "cli-attempt")
        self.assertEqual(call("backup", str(backup))["path"], str(backup))
        self.assertEqual(call("check")["schema_version"], SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
