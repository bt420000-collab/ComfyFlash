"""Small, local SQLite store for ComfyFlash runs and Agent feedback.

No connection is kept open between calls. Each operation opens a short-lived
SQLite connection, writes/reads, and closes it. WAL + a busy timeout allow
separate GPU worker processes to append records without a database service.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "RSI" / "data" / "comfyflash.sqlite3"
SCHEMA_VERSION = 1

FEEDBACK_CATEGORIES = {
    "bug", "optimization", "performance", "hardware", "quality",
    "workflow", "api", "compatibility", "idea", "other",
}
FEEDBACK_SEVERITIES = {"info", "low", "medium", "high", "critical"}
FEEDBACK_STATUSES = {
    "open", "triaged", "planned", "in_progress", "resolved", "wont_fix", "duplicate",
}
FEEDBACK_SOURCES = {"agent", "human", "system"}


class RSIStoreError(ValueError):
    """Invalid RSI data or unsupported database state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value, fallback=None):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _bounded_text(value, name, *, required=False, limit=12000):
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if required and not value:
        raise RSIStoreError(f"{name} 不能为空")
    if len(value) > limit:
        raise RSIStoreError(f"{name} 超过长度限制（{limit} 字符）")
    return value


def _choice(value, allowed, name):
    value = str(value or "").strip().lower()
    if value not in allowed:
        raise RSIStoreError(f"{name} 必须是以下值之一：{', '.join(sorted(allowed))}")
    return value


class RSIStore:
    """Short-lived SQLite access for execution history and Agent commentary."""

    def __init__(self, db_path=None, *, timeout_s=5.0):
        self.path = Path(db_path or os.getenv("COMFYFLASH_RSI_DB") or DEFAULT_DB_PATH).expanduser().resolve()
        self.timeout_s = max(0.1, float(timeout_s))
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=self.timeout_s)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_s * 1000)}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute("PRAGMA journal_mode = WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RSIStoreError(
                    f"RSI 数据库版本 {version} 高于当前程序支持的版本 {SCHEMA_VERSION}"
                )
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tone TEXT,
                    output_type TEXT,
                    device TEXT,
                    seed INTEGER,
                    resolution TEXT,
                    width INTEGER,
                    height INTEGER,
                    requested_duration_s REAL,
                    actual_duration_s REAL,
                    requested_frame_count INTEGER,
                    actual_frame_count INTEGER,
                    elapsed_s REAL,
                    output_path TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    prompt_sha256 TEXT,
                    prompt_chars INTEGER,
                    input_image_count INTEGER NOT NULL DEFAULT 0,
                    models_json TEXT NOT NULL DEFAULT '{}',
                    params_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    environment_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS agent_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    author TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    observation TEXT NOT NULL,
                    impact TEXT NOT NULL DEFAULT '',
                    suggestion TEXT NOT NULL DEFAULT '',
                    run_id TEXT,
                    task_id TEXT NOT NULL DEFAULT '',
                    tone TEXT NOT NULL DEFAULT '',
                    artifact_path TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    resolution TEXT NOT NULL DEFAULT '',
                    resolved_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_tone_created ON runs(tone, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_feedback_status_created
                    ON agent_feedback(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_feedback_tone_category
                    ON agent_feedback(tone, category, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_feedback_run ON agent_feedback(run_id);
                """
            )
            if version < 1:
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @property
    def schema_version(self):
        with self._session() as db:
            return int(db.execute("PRAGMA user_version").fetchone()[0])

    def record_run(self, result, *, tone=None, spec=None, request=None, device=None):
        """Persist one completed/failed generation. Prompt text is never stored."""
        if not isinstance(result, dict):
            raise RSIStoreError("result 必须是字典")
        request = request if isinstance(request, dict) else {}
        spec = spec if isinstance(spec, dict) else {}
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        prompt = request.get("prompt")
        prompt_text = str(prompt) if prompt is not None else ""
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else None
        metrics = {
            key: result[key]
            for key in (
                "width", "height", "frames", "duration", "fps", "sampling",
                "runtime", "timing_s", "audio", "validation", "inspection",
            )
            if key in result
        }
        models = spec.get("models") or {}
        params = request.get("advanced") or {}
        environment = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "device_label": device or result.get("device"),
        }
        run_id = str(uuid.uuid4())
        values = (
            run_id,
            _now(),
            "success" if result.get("ok") else "failed",
            str(tone or result.get("tone") or ""),
            str(spec.get("output") or ""),
            str(device or result.get("device") or ""),
            result.get("seed", request.get("seed")),
            str(request.get("resolution") or ""),
            result.get("width", request.get("width")),
            result.get("height", request.get("height")),
            request.get("duration"),
            result.get("duration"),
            request.get("frames"),
            result.get("frames"),
            result.get("elapsed_s"),
            str(result.get("file") or request.get("output") or ""),
            str(error.get("code") or ""),
            str(error.get("message") or "")[:2000],
            prompt_hash,
            len(prompt_text),
            len(request.get("images") or []),
            _json(models),
            _json(params),
            _json(metrics),
            _json(environment),
        )
        with self._session() as db:
            db.execute(
                """INSERT INTO runs (
                    run_id, created_at, status, tone, output_type, device, seed,
                    resolution, width, height, requested_duration_s, actual_duration_s,
                    requested_frame_count, actual_frame_count, elapsed_s, output_path,
                    error_code, error_message, prompt_sha256, prompt_chars, input_image_count,
                    models_json, params_json, metrics_json, environment_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        return run_id

    def add_feedback(
        self, *, title, observation, author="agent", source="agent", category="optimization",
        severity="medium", status="open", impact="", suggestion="", run_id=None,
        task_id="", tone="", artifact_path="", tags=None, context=None,
    ):
        """Add an Agent/Human observation for later targeted improvement."""
        feedback_id = str(uuid.uuid4())
        created = _now()
        source = _choice(source, FEEDBACK_SOURCES, "source")
        category = _choice(category, FEEDBACK_CATEGORIES, "category")
        severity = _choice(severity, FEEDBACK_SEVERITIES, "severity")
        status = _choice(status, FEEDBACK_STATUSES, "status")
        title = _bounded_text(title, "title", required=True, limit=240)
        observation = _bounded_text(observation, "observation", required=True)
        author = _bounded_text(author or "agent", "author", required=True, limit=120)
        impact = _bounded_text(impact, "impact", limit=4000)
        suggestion = _bounded_text(suggestion, "suggestion", limit=8000)
        task_id = _bounded_text(task_id, "task_id", limit=240)
        tone = _bounded_text(tone, "tone", limit=80)
        artifact_path = _bounded_text(artifact_path, "artifact_path", limit=2000)
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(",")]
        tags = list(dict.fromkeys(str(tag).strip() for tag in (tags or []) if str(tag).strip()))
        if len(tags) > 30:
            raise RSIStoreError("tags 最多 30 个")
        context = context or {}
        if not isinstance(context, (dict, list)):
            raise RSIStoreError("context 必须是 JSON 对象或数组")
        context_json = _json(context)
        if len(context_json) > 32000:
            raise RSIStoreError("context 序列化后不能超过 32 KB")
        try:
            with self._session() as db:
                db.execute(
                    """INSERT INTO agent_feedback (
                        feedback_id, created_at, updated_at, source, author, category,
                        severity, status, title, observation, impact, suggestion, run_id,
                        task_id, tone, artifact_path, tags_json, context_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        feedback_id, created, created, source, author, category, severity,
                        status, title, observation, impact, suggestion, run_id, task_id, tone,
                        artifact_path, _json(tags), context_json,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RSIStoreError(f"点评关联的 run_id 不存在：{run_id}") from exc
        return self.get_feedback(feedback_id)

    # Friendly alias for agents that think in terms of leaving a note/comment.
    note = add_feedback

    def get_feedback(self, feedback_id):
        with self._session() as db:
            row = db.execute(
                "SELECT * FROM agent_feedback WHERE feedback_id = ?", (str(feedback_id),)
            ).fetchone()
        return self._feedback_row(row) if row else None

    def list_feedback(
        self, *, status=None, category=None, severity=None, tone=None, run_id=None,
        search=None, limit=30,
    ):
        clauses, params = [], []
        for column, value, allowed, label in (
            ("status", status, FEEDBACK_STATUSES, "status"),
            ("category", category, FEEDBACK_CATEGORIES, "category"),
            ("severity", severity, FEEDBACK_SEVERITIES, "severity"),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(_choice(value, allowed, label))
        for column, value in (("tone", tone), ("run_id", run_id)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(str(value))
        if search:
            clauses.append("(title LIKE ? OR observation LIKE ? OR suggestion LIKE ?)")
            needle = f"%{str(search).strip()}%"
            params.extend((needle, needle, needle))
        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 30
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._session() as db:
            rows = db.execute(
                f"SELECT * FROM agent_feedback{where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._feedback_row(row) for row in rows]

    def update_feedback(self, feedback_id, *, status=None, resolution=None, severity=None):
        updates, params = [], []
        if status is not None:
            status = _choice(status, FEEDBACK_STATUSES, "status")
            updates.extend(("status = ?", "resolved_at = ?"))
            params.extend((status, _now() if status in {"resolved", "wont_fix", "duplicate"} else None))
        if severity is not None:
            updates.append("severity = ?")
            params.append(_choice(severity, FEEDBACK_SEVERITIES, "severity"))
        if resolution is not None:
            updates.append("resolution = ?")
            params.append(_bounded_text(resolution, "resolution", limit=8000))
        if not updates:
            raise RSIStoreError("至少提供 status、severity 或 resolution 之一")
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(str(feedback_id))
        with self._session() as db:
            cursor = db.execute(
                f"UPDATE agent_feedback SET {', '.join(updates)} WHERE feedback_id = ?", params
            )
            if cursor.rowcount == 0:
                raise RSIStoreError(f"找不到点评记录：{feedback_id}")
        return self.get_feedback(feedback_id)

    def list_runs(self, *, tone=None, status=None, limit=30):
        clauses, params = [], []
        if tone:
            clauses.append("tone = ?")
            params.append(str(tone))
        if status:
            if status not in {"success", "failed"}:
                raise RSIStoreError("run status 必须是 success 或 failed")
            clauses.append("status = ?")
            params.append(status)
        limit = max(1, min(200, int(limit)))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._session() as db:
            rows = db.execute(
                f"SELECT * FROM runs{where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def summary(self):
        with self._session() as db:
            run_counts = {
                row["status"]: row["count"]
                for row in db.execute("SELECT status, COUNT(*) AS count FROM runs GROUP BY status")
            }
            feedback_counts = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status, COUNT(*) AS count FROM agent_feedback GROUP BY status"
                )
            }
            open_notes = db.execute(
                "SELECT COUNT(*) FROM agent_feedback WHERE status = 'open'"
            ).fetchone()[0]
        return {
            "db_path": str(self.path),
            "schema_version": self.schema_version,
            "runs": run_counts,
            "feedback": feedback_counts,
            "open_feedback": open_notes,
        }

    @staticmethod
    def _feedback_row(row):
        item = dict(row)
        item["tags"] = _json_load(item.pop("tags_json", None), [])
        item["context"] = _json_load(item.pop("context_json", None), {})
        return item

    @staticmethod
    def _run_row(row):
        item = dict(row)
        for source, target, fallback in (
            ("models_json", "models", {}),
            ("params_json", "params", {}),
            ("metrics_json", "metrics", {}),
            ("environment_json", "environment", {}),
        ):
            item[target] = _json_load(item.pop(source, None), fallback)
        return item
