"""Short-lived local SQLite access for execution and manually reviewed evidence."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
import time
from collections.abc import Iterable, Mapping
from contextlib import closing, contextmanager
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "rsi.sqlite3"
_APPLICATION_ID = 0x43465253  # CFRS
_STATUSES = {"success", "failed", "oom", "retry"}
_TOKEN = re.compile(r"[\w.:-]{1,128}")
_RESOLUTION = re.compile(r"[1-9][0-9]*x[1-9][0-9]*")
_TIMINGS = {
    "elapsed_s", "load_s", "encode_s", "sample_s", "decode_s", "write_s",
    "validate_s", "total_s", "preprocess_s", "postprocess_s",
    "model_load", "conditioning", "sampling", "video_decode", "image_decode",
    "audio_decode", "decode_offload", "encoding", "delivery", "total", "preflight", "runtime_init",
    "generation", "queue_wait",
}
_VALIDATION_FLAGS = {"ok", "has_audio", "visual_check_passed"}
_VALIDATION_COUNTS = {"width", "height", "frame_count", "warning_count"}
_PEAK_MEASUREMENTS = {
    "vram_used_bytes", "torch_peak_allocated_bytes", "process_rss_bytes", "system_ram_used_bytes",
}
_RUN_JSON_FIELDS = ("timings", "validation", "peak_memory")
_BACKUP_WAIT_CODES = {5, 6}  # SQLITE_BUSY, SQLITE_LOCKED

_MIGRATIONS = {
    1: (
        f"PRAGMA application_id = {_APPLICATION_ID}",
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE run_summaries (
            attempt_id TEXT PRIMARY KEY NOT NULL,
            request_id TEXT,
            schema_version INTEGER NOT NULL,
            tone TEXT,
            model TEXT,
            resolution TEXT,
            duration_s REAL,
            device TEXT,
            workload_fingerprint TEXT,
            device_fingerprint TEXT,
            status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'oom', 'retry')),
            elapsed_s REAL,
            artifact_path TEXT,
            telemetry_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX run_summaries_tone ON run_summaries(tone)",
        "CREATE INDEX run_summaries_device ON run_summaries(device)",
        "CREATE INDEX run_summaries_model ON run_summaries(model)",
        "CREATE INDEX run_summaries_resolution ON run_summaries(resolution, duration_s)",
    ),
    2: (
        """
        CREATE TABLE policy_versions (
            version TEXT PRIMARY KEY NOT NULL,
            target TEXT NOT NULL,
            parent_version TEXT REFERENCES policy_versions(version),
            rollback_to TEXT REFERENCES policy_versions(version),
            device TEXT,
            model TEXT,
            evidence_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (parent_version <> version AND rollback_to <> version)
        )
        """,
        """
        CREATE TABLE improvement_proposals (
            proposal_id TEXT PRIMARY KEY NOT NULL,
            target TEXT NOT NULL,
            baseline_policy_version TEXT NOT NULL REFERENCES policy_versions(version),
            candidate_policy_version TEXT NOT NULL REFERENCES policy_versions(version),
            hypothesis TEXT NOT NULL,
            test_set TEXT NOT NULL,
            metrics TEXT NOT NULL,
            proposer TEXT,
            evidence TEXT,
            rollback_to TEXT REFERENCES policy_versions(version),
            decision TEXT NOT NULL DEFAULT 'pending'
                CHECK (decision IN ('pending', 'adopted', 'rejected', 'rolled_back')),
            decision_evidence TEXT,
            adoption_evidence TEXT,
            decided_at TEXT,
            adopted_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (baseline_policy_version <> candidate_policy_version),
            CHECK (rollback_to <> candidate_policy_version)
        )
        """,
        """
        CREATE TABLE experiments (
            experiment_id TEXT PRIMARY KEY NOT NULL,
            proposal_id TEXT NOT NULL REFERENCES improvement_proposals(proposal_id),
            test_set TEXT NOT NULL,
            baseline_metrics TEXT NOT NULL,
            candidate_metrics TEXT NOT NULL,
            evidence TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX experiments_proposal ON experiments(proposal_id)",
        "ALTER TABLE run_summaries ADD COLUMN policy_version TEXT REFERENCES policy_versions(version)",
        "ALTER TABLE run_summaries ADD COLUMN error_code TEXT",
        "ALTER TABLE run_summaries ADD COLUMN peak_memory_bytes INTEGER",
        "ALTER TABLE run_summaries ADD COLUMN timings TEXT",
        "ALTER TABLE run_summaries ADD COLUMN validation TEXT",
        "ALTER TABLE run_summaries ADD COLUMN peak_memory TEXT",
        "ALTER TABLE run_summaries ADD COLUMN attempt_index INTEGER",
        "ALTER TABLE run_summaries ADD COLUMN failure_stage TEXT",
        "ALTER TABLE run_summaries ADD COLUMN recovered_by_retry INTEGER",
        "CREATE INDEX run_summaries_policy ON run_summaries(policy_version)",
    ),
}
SCHEMA_VERSION = max(_MIGRATIONS)


def _text(value, field, maximum, *, pattern=None):
    if (
        not isinstance(value, str) or not value.strip() or len(value) > maximum
        or not value.isprintable() or (pattern is not None and not pattern.fullmatch(value))
    ):
        raise ValueError(f"{field} must be a valid string of at most {maximum} characters")
    return value


def _token(value, field):
    return _text(value, field, 128, pattern=_TOKEN)


def _path_reference(value, field):
    if isinstance(value, Path):
        value = str(value)
    return _text(value, field, 4096)


def _integer(value, field, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError(f"{field} must be an integer of at least {minimum}")
    return value


def _number(value, field, *, nonnegative=True):
    if (
        type(value) not in (int, float) or abs(value) > sys.float_info.max
        or not math.isfinite(value) or (nonnegative and value < 0)
    ):
        raise ValueError(f"{field} must be a finite number in range")
    return float(value)


def _flag(value, field):
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def _optional(value, validator, field):
    return None if value is None else validator(value, field)


def _dump(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _run_projection(record):
    if not isinstance(record, Mapping):
        raise ValueError("Each run summary must be a mapping")
    status = _token(record.get("status"), "status")
    if status not in _STATUSES:
        raise ValueError("status must be success, failed, oom or retry")
    timings = record.get("timings")
    validation = record.get("validation")
    peak_memory = record.get("peak_memory")
    if timings is not None:
        if not isinstance(timings, Mapping):
            raise ValueError("timings must be a mapping")
        timings = _dump({
            key: _number(value, key) for key, value in timings.items()
            if key in _TIMINGS and value is not None
        })
    if validation is not None:
        if not isinstance(validation, Mapping):
            raise ValueError("validation must be a mapping")
        flags = {}
        for key, value in validation.items():
            if value is None:
                continue
            if key in _VALIDATION_FLAGS:
                flags[key] = _flag(value, key)
            elif key in _VALIDATION_COUNTS:
                flags[key] = _integer(value, key)
            elif key == "duration_s":
                flags[key] = _number(value, key)
        validation = _dump(flags)
    if peak_memory is not None:
        if not isinstance(peak_memory, Mapping):
            raise ValueError("peak_memory must be a mapping")
        peak_memory = _dump({
            key: _integer(value, key) for key, value in peak_memory.items()
            if key in _PEAK_MEASUREMENTS and value is not None
        })

    model = record.get("model")
    resolution = record.get("resolution")
    request_id = record.get("request_id")
    return {
        "attempt_id": _token(record.get("attempt_id"), "attempt_id"),
        "request_id": None if request_id is None else _text(request_id, "request_id", 256),
        "schema_version": _integer(record.get("schema_version", 1), "schema_version", minimum=1),
        "tone": _optional(record.get("tone"), _token, "tone"),
        "model": None if model is None else _text(model, "model", 512),
        "resolution": None if resolution is None else _text(resolution, "resolution", 32, pattern=_RESOLUTION),
        "duration_s": _optional(record.get("duration_s"), _number, "duration_s"),
        "device": _optional(record.get("device"), _token, "device"),
        "workload_fingerprint": _optional(record.get("workload_fingerprint"), _token, "workload_fingerprint"),
        "device_fingerprint": _optional(record.get("device_fingerprint"), _token, "device_fingerprint"),
        "status": status,
        "elapsed_s": _optional(record.get("elapsed_s"), _number, "elapsed_s"),
        "artifact_path": _optional(record.get("artifact_path"), _path_reference, "artifact_path"),
        "telemetry_path": _optional(record.get("telemetry_path"), _path_reference, "telemetry_path"),
        "policy_version": _optional(record.get("policy_version"), _token, "policy_version"),
        "error_code": _optional(record.get("error_code"), _token, "error_code"),
        "peak_memory_bytes": _optional(record.get("peak_memory_bytes"), _integer, "peak_memory_bytes"),
        "timings": timings,
        "validation": validation,
        "peak_memory": peak_memory,
        "attempt_index": _optional(record.get("attempt_index"), _integer, "attempt_index"),
        "failure_stage": _optional(record.get("failure_stage"), _token, "failure_stage"),
        "recovered_by_retry": _optional(record.get("recovered_by_retry"), _flag, "recovered_by_retry"),
    }


def _metrics(values, field):
    if not isinstance(values, Mapping) or not 1 <= len(values) <= 32:
        raise ValueError(f"{field} must contain between 1 and 32 named numeric metrics")
    return _dump({
        _token(key, field): _number(value, key, nonnegative=False)
        for key, value in values.items()
    })


def _rows(cursor, json_fields=()):
    result = []
    for row in cursor:
        decoded = dict(row)
        for key in json_fields:
            if decoded[key] is not None:
                decoded[key] = json.loads(decoded[key])
        result.append(decoded)
    return result


@contextmanager
def _transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _schema_version(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if not 0 <= version <= SCHEMA_VERSION:
        raise sqlite3.DatabaseError(f"Unsupported RSI schema version {version}")
    if version and conn.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID:
        raise sqlite3.DatabaseError("The SQLite database does not belong to RSI")
    return version


def _migrate(conn):
    if _schema_version(conn) == SCHEMA_VERSION:
        return SCHEMA_VERSION
    with _transaction(conn):
        # An independent process may have completed the migration while we waited for the lock.
        version = _schema_version(conn)
        if version == 0 and (
            conn.execute("PRAGMA application_id").fetchone()[0]
            or conn.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1").fetchone()
        ):
            raise sqlite3.DatabaseError("The SQLite database does not belong to RSI")
        for next_version in range(version + 1, SCHEMA_VERSION + 1):
            for statement in _MIGRATIONS[next_version]:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (next_version,))
            conn.execute(f"PRAGMA user_version = {next_version}")
    return SCHEMA_VERSION


def _check_database(conn):
    version = _schema_version(conn)
    if not version:
        raise sqlite3.DatabaseError("The SQLite database does not contain an initialized RSI schema")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    expected = {"run_summaries", "schema_migrations"}
    if version >= 2:
        expected.update({"policy_versions", "improvement_proposals", "experiments"})
    if not expected <= tables:
        raise sqlite3.DatabaseError("RSI schema is missing required record tables")
    integrity = conn.execute("PRAGMA integrity_check(1)").fetchone()[0]
    if integrity != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise sqlite3.DatabaseError("RSI database integrity check failed")
    applied = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    if applied != list(range(1, version + 1)):
        raise sqlite3.DatabaseError("RSI schema migration history does not match the database version")
    return version


class RSIStore:
    """An RSI database location. Constructing the store does not access SQLite."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH, *, timeout_s: float = 5.0):
        self.path = Path(path).expanduser().absolute()
        self.timeout_s = _number(timeout_s, "timeout_s")
        if self.timeout_s * 1000 > 2**31 - 1:
            raise ValueError("timeout_s exceeds the SQLite busy timeout range")

    @contextmanager
    def _connection(self, *, readonly=False, migrate=True):
        if readonly:
            name = f"{self.path.as_uri()}?mode=ro"
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            name = str(self.path)
        # sqlite3's connection timeout sets the per-connection SQLite busy handler.
        with closing(sqlite3.connect(
            name, uri=readonly, timeout=self.timeout_s, isolation_level=None,
        )) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            if migrate:
                _migrate(conn)
            yield conn

    def initialize(self) -> int:
        """Create or upgrade the database and return the schema version."""
        with self._connection():
            return SCHEMA_VERSION

    def record_run_summary(self, record: Mapping) -> int:
        return self.record_run_summaries((record,))

    def record_run_summaries(self, records: Iterable[Mapping], *, builtin_policy=None) -> int:
        """Persist a batch in one transaction, skipping already stored attempt IDs."""
        projected = [_run_projection(record) for record in records]
        if not projected:
            return 0
        columns = ", ".join(projected[0])
        values = ", ".join(f":{name}" for name in projected[0])
        with self._connection() as conn, _transaction(conn):
            if builtin_policy is not None:
                version = _token(builtin_policy, "policy_version")
                conn.execute(
                    "INSERT INTO policy_versions (version, target) VALUES (?, 'execution') "
                    "ON CONFLICT(version) DO NOTHING", (version,),
                )
            cursor = conn.executemany(
                f"INSERT INTO run_summaries ({columns}) VALUES ({values}) "
                "ON CONFLICT(attempt_id) DO NOTHING",
                projected,
            )
            return cursor.rowcount

    def query_history(
        self, *, tone=None, device=None, model=None, resolution=None,
        duration_s=None, policy_version=None, limit=100,
    ) -> list[dict]:
        """Load recent attempts matching the supplied exact filters, newest first."""
        filters = {
            "tone": tone, "device": device, "model": model, "resolution": resolution,
            "duration_s": _optional(duration_s, _number, "duration_s"), "policy_version": policy_version,
        }
        conditions = [f"{column} = :{column}" for column, value in filters.items() if value is not None]
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connection() as conn:
            history = _rows(conn.execute(
                "SELECT * FROM run_summaries" + where + " ORDER BY rowid DESC LIMIT :limit",
                {**filters, "limit": _integer(limit, "limit", minimum=1)},
            ), _RUN_JSON_FIELDS)
        for summary in history:
            if summary["recovered_by_retry"] is not None:
                summary["recovered_by_retry"] = bool(summary["recovered_by_retry"])
        return history

    def save_policy_revision(
        self, *, version, target, parent_version=None, rollback_to=None,
        device=None, model=None, evidence_path=None,
    ) -> None:
        """Register an immutable policy version and its optional parent and rollback links."""
        values = (
            _token(version, "version"), _token(target, "target"),
            _optional(parent_version, _token, "parent_version"),
            _optional(rollback_to, _token, "rollback_to"),
            _optional(device, _token, "device"),
            None if model is None else _text(model, "model", 512),
            _optional(evidence_path, _path_reference, "evidence_path"),
        )
        with self._connection() as conn, _transaction(conn):
            conn.execute(
                """
                INSERT INTO policy_versions
                    (version, target, parent_version, rollback_to, device, model, evidence_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, values,
            )

    def add_proposal(
        self, *, proposal_id, target, baseline_policy_version, candidate_policy_version,
        hypothesis, test_set, metrics, rollback_to=None, proposer=None, evidence=None,
    ) -> None:
        """Store an explicit proposal linked to two registered policy versions."""
        if not isinstance(metrics, (list, tuple)) or not 1 <= len(metrics) <= 32:
            raise ValueError("metrics must be a list of 1 to 32 names")
        values = (
            _token(proposal_id, "proposal_id"), _token(target, "target"),
            _token(baseline_policy_version, "baseline_policy_version"),
            _token(candidate_policy_version, "candidate_policy_version"),
            _text(hypothesis, "hypothesis", 512), _path_reference(test_set, "test_set"),
            _dump([_token(name, "metric name") for name in metrics]),
            _optional(rollback_to, _token, "rollback_to"),
            None if proposer is None else _text(proposer, "proposer", 128),
            _optional(evidence, _path_reference, "evidence"),
        )
        with self._connection() as conn, _transaction(conn):
            conn.execute(
                """
                INSERT INTO improvement_proposals (
                    proposal_id, target, baseline_policy_version, candidate_policy_version,
                    hypothesis, test_set, metrics, rollback_to, proposer, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, values,
            )

    def record_experiment(
        self, *, experiment_id, proposal_id, test_set, baseline_metrics, candidate_metrics,
        evidence, conclusion,
    ) -> None:
        """Record manually supplied paired metrics and the location of experiment evidence."""
        values = (
            _token(experiment_id, "experiment_id"), _token(proposal_id, "proposal_id"),
            _path_reference(test_set, "test_set"), _metrics(baseline_metrics, "baseline_metrics"),
            _metrics(candidate_metrics, "candidate_metrics"), _path_reference(evidence, "evidence"),
            _text(conclusion, "conclusion", 512),
        )
        with self._connection() as conn, _transaction(conn):
            conn.execute(
                """
                INSERT INTO experiments
                    (experiment_id, proposal_id, test_set, baseline_metrics, candidate_metrics, evidence, conclusion)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, values,
            )

    def record_proposal_decision(self, proposal_id, *, decision, evidence) -> None:
        """Record a manual decision and preserve the adoption evidence after rollback."""
        if decision not in ("adopted", "rejected", "rolled_back"):
            raise ValueError("decision must be adopted, rejected or rolled_back")
        proposal_id = _token(proposal_id, "proposal_id")
        evidence = _path_reference(evidence, "evidence")
        with self._connection() as conn, _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE improvement_proposals
                   SET decision = :decision, decision_evidence = :evidence,
                       decided_at = CURRENT_TIMESTAMP,
                       adoption_evidence = CASE WHEN :decision = 'adopted'
                           THEN :evidence ELSE adoption_evidence END,
                       adopted_at = CASE WHEN :decision = 'adopted'
                           THEN CURRENT_TIMESTAMP ELSE adopted_at END
                 WHERE proposal_id = :proposal_id AND (
                     (decision = 'pending' AND :decision = 'rejected') OR
                     (decision = 'pending' AND :decision = 'adopted' AND rollback_to IS NOT NULL
                         AND EXISTS (SELECT 1 FROM experiments WHERE proposal_id = :proposal_id)) OR
                     (decision = 'adopted' AND :decision = 'rolled_back')
                 )
                """, {"decision": decision, "proposal_id": proposal_id, "evidence": evidence},
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot record this decision for the proposal and its linked evidence")

    def query_policy_versions(self, *, policy_version=None, limit=100) -> list[dict]:
        with self._connection() as conn:
            return _rows(conn.execute(
                """
                SELECT * FROM policy_versions
                WHERE :version IS NULL OR version = :version
                ORDER BY rowid DESC LIMIT :limit
                """, {"version": policy_version, "limit": _integer(limit, "limit", minimum=1)},
            ))

    def query_proposals(self, *, policy_version=None, proposal_id=None, limit=100) -> list[dict]:
        with self._connection() as conn:
            return _rows(conn.execute(
                """
                SELECT * FROM improvement_proposals
                WHERE (:version IS NULL OR :version IN (
                    baseline_policy_version, candidate_policy_version, rollback_to
                )) AND (:proposal_id IS NULL OR proposal_id = :proposal_id)
                ORDER BY rowid DESC LIMIT :limit
                """,
                {"version": policy_version, "proposal_id": proposal_id, "limit": _integer(limit, "limit", minimum=1)},
            ), ("metrics",))

    def query_experiments(self, *, policy_version=None, proposal_id=None, limit=100) -> list[dict]:
        with self._connection() as conn:
            return _rows(conn.execute(
                """
                SELECT e.*, p.baseline_policy_version, p.candidate_policy_version
                FROM experiments AS e JOIN improvement_proposals AS p USING (proposal_id)
                WHERE (:version IS NULL OR :version IN (
                    p.baseline_policy_version, p.candidate_policy_version, p.rollback_to
                )) AND (:proposal_id IS NULL OR e.proposal_id = :proposal_id)
                ORDER BY e.rowid DESC LIMIT :limit
                """,
                {"version": policy_version, "proposal_id": proposal_id, "limit": _integer(limit, "limit", minimum=1)},
            ), ("baseline_metrics", "candidate_metrics"))

    def check(self) -> int:
        """Verify integrity, foreign keys and migration history without modifying the database."""
        with self._connection(readonly=True, migrate=False) as conn:
            conn.execute("BEGIN")
            return _check_database(conn)

    def backup(self, destination: str | Path) -> Path:
        """Use SQLite's online backup API to create a verified copy at a new location."""
        copy = RSIStore(destination, timeout_s=self.timeout_s)
        with self._connection(readonly=True, migrate=False) as source:
            if not _schema_version(source):
                raise sqlite3.DatabaseError("Initialize the RSI database before backing it up")
            copy.path.parent.mkdir(parents=True, exist_ok=True)
            copy.path.touch(exist_ok=False)
            try:
                deadline = time.monotonic() + self.timeout_s

                def wait_for_backup(status, remaining, total):
                    if status in _BACKUP_WAIT_CODES and time.monotonic() >= deadline:
                        raise sqlite3.OperationalError("RSI backup could not acquire the database lock")

                with copy._connection(migrate=False) as target:
                    source.backup(target, pages=128, progress=wait_for_backup, sleep=0.01)
                    _check_database(target)
            except BaseException:
                copy.path.unlink(missing_ok=True)
                raise
        return copy.path

    @classmethod
    def restore(cls, backup: str | Path, destination: str | Path, *, timeout_s: float = 5.0) -> RSIStore:
        """Verify and restore a snapshot to a new database path without overwriting files."""
        source = cls(backup, timeout_s=timeout_s)
        return cls(source.backup(destination), timeout_s=timeout_s)
