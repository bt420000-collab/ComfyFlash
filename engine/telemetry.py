"""Task-end execution records. SQLite consumes only a small projection."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4


SCHEMA_VERSION = 1
POLICY_VERSION = "engine-v0.2-p1"
DATA_ROOT = Path(__file__).resolve().parents[1] / "RSI" / "data"
RUNTIME_SETTINGS = {
    "dynamic_vram": True, "vram_headroom_gb": 0.1, "reserve_vram_gb": None,
    "sage_attention": False, "fp16_accumulation": False, "video_decode_offload": False,
    "disable_pinned_memory": False, "disable_comfy_compiler": False,
}


def fingerprint(data):
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def request_identifier(value):
    if isinstance(value, str) and value.strip() and len(value) <= 256 and all(c.isprintable() for c in value):
        return value
    return None


class Attempt:
    def __init__(self, tone, identifier=None):
        self.attempt_id = uuid4().hex
        self.started = perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.tone = tone if isinstance(tone, str) and re.fullmatch(r"[\w.:-]{1,128}", tone) else None
        self.request_id = request_identifier(identifier)
        self.failed = None
        self.timings = {}
        self.workload = {}
        self.device = {}
        self.samples = []
        self._cuda = self._gpu = self._psutil = self._process = None
        self._allocator_peak = False

    def configure(self, spec, req, config):
        self.workload = {
            "tone_version": spec.get("version"), "tone_schema_version": spec.get("schema_version"),
            "models": {role: {"file": Path(name).name} for role, name in spec.get("models", {}).items()},
            "width": req["width"], "height": req["height"], "fps": req.get("fps"), "batch_size": 1,
            "requested_duration_s": req.get("requested_duration_s"),
            "aligned_frame_count": req.get("aligned_frame_count"), "sampling": req["advanced"],
            "runtime_settings": {key: config.get(key, default) for key, default in RUNTIME_SETTINGS.items()},
        }

    def set_models(self, details):
        self.workload["models"] = {m["role"]: {key: m[key] for key in ("file", "fingerprint", "applied")}
                                   for m in details}

    def enter_runtime(self, runtime):
        try:
            import psutil
            self._psutil = psutil
            self._process = psutil.Process()
        except (ImportError, OSError, RuntimeError):
            pass
        try:
            versions = runtime.versions()
            self.device.update({key: versions.get(key) for key in ("comfyui", "torch", "cuda")})
            self.workload["runtime_settings"].update(dynamic_vram=runtime.dynamic_vram)
            if runtime.device.startswith("cuda"):
                self._cuda = runtime.torch.cuda
                self._gpu = runtime.mm.get_torch_device()
                props = self._cuda.get_device_properties(self._gpu)
                uuid = getattr(props, "uuid", None)
                self.device.update(uuid=str(uuid) if uuid is not None else None, name=props.name,
                                   total_vram_bytes=props.total_memory, compute_capability=[props.major, props.minor])
                self._cuda.reset_peak_memory_stats(self._gpu)
                self._allocator_peak = True
            else:
                self.device["name"] = runtime.device
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        self.snapshot("start")

    def snapshot(self, stage):
        row = {"stage": stage, "since_start_s": round(perf_counter() - self.started, 4),
               "vram_free_bytes": None, "vram_used_bytes": None, "torch_peak_allocated_bytes": None,
               "process_rss_bytes": None, "system_ram_available_bytes": None, "system_ram_used_bytes": None}
        if self._gpu is not None:
            try:
                free, total = self._cuda.mem_get_info(self._gpu)
                row.update(vram_free_bytes=free, vram_used_bytes=total - free)
                if self._allocator_peak:
                    row["torch_peak_allocated_bytes"] = self._cuda.max_memory_allocated(self._gpu)
            except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
                pass
        if self._process is not None:
            try:
                system = self._psutil.virtual_memory()
                row.update(process_rss_bytes=self._process.memory_info().rss,
                           system_ram_available_bytes=system.available,
                           system_ram_used_bytes=system.total - system.available)
            except (self._psutil.Error, OSError, RuntimeError):
                pass
        self.samples.append(row)

    @contextmanager
    def measure(self, stage):
        started = perf_counter()
        try:
            yield
        except Exception:
            self.failed = self.failed or stage
            raise
        finally:
            self.timings[stage] = self.timings.get(stage, 0) + perf_counter() - started
            if self._process is not None or self._gpu is not None:
                self.snapshot(stage)

    def finish(self, result):
        error = result.get("error", {}).get("code")
        status = "success" if result["ok"] else "oom" if error == "OOM" else "failed"
        artifact = result.get("artifact") or {}
        validation = artifact.get("validation") or result.get("validation") or {}
        timings = {**self.timings, **result.get("timing_s", {}), **artifact.get("timing_s", {})}
        timings["total"] = perf_counter() - self.started
        work = {"tone": self.tone, **self.workload}
        runtime = artifact.get("runtime") or result.get("runtime") or {}
        work["runtime_observed"] = {key: runtime.get(key) for key in ("dynamic_vram", "attention_default",
                                                                       "fp16_accumulation", "decode_offloaded")}
        for key in ("actual_duration_s", "actual_frame_count"):
            if key in result:
                work[key] = result[key]
        device = {"driver_version": None, **self.device}  # CLI can enrich by physical UUID.
        return {
            "schema_version": SCHEMA_VERSION, "attempt_id": self.attempt_id,
            "policy_version": POLICY_VERSION,
            "request_id": self.request_id, "started_at": self.started_at, "tone": self.tone,
            "status": status, "error_code": error, "failure_stage": None if result["ok"] else self.failed,
            "elapsed_s": round(timings["total"], 4), "workload": work, "device_info": device,
            "workload_fingerprint": fingerprint({"tone": self.tone, **self.workload}),
            "device_fingerprint": fingerprint(device) if self.device else None,
            "timings": {"queue_wait": None, **{key: round(value, 4) for key, value in timings.items()}},
            "resource_samples": self.samples,
            "resource_peaks": {key: max((r[key] for r in self.samples if r[key] is not None), default=None)
                               for key in ("vram_used_bytes", "torch_peak_allocated_bytes", "process_rss_bytes",
                                           "system_ram_used_bytes")},
            "artifact_path": artifact.get("inspection", {}).get("metadata"),
            "validation": {"status": validation.get("status"), "warning_count": len(validation.get("warnings", [])),
                           "has_audio": artifact.get("audio", {}).get("present"), "width": result.get("width"),
                           "height": result.get("height"), "frame_count": result.get("actual_frame_count"),
                           "duration_s": result.get("actual_duration_s")},
        }


def worker_attempt(request, result):
    attempt = Attempt(request.get("tone"), request.get("request_id"))
    if not result["ok"]:
        attempt.failed = "dependency" if result.get("error", {}).get("code") == "DEPENDENCY_FAILED" else "worker"
    record = attempt.finish(result)
    if record["error_code"] == "WORKER_MEMORY_ERROR":
        record["memory_related"] = True
    # There is no safe inference duration when a worker never returned a receipt.
    if not result.get("telemetry"):
        record["elapsed_s"] = record["timings"]["total"] = None
    return record


def summary(record, path):
    work = record["workload"]
    width, height = work.get("width"), work.get("height")
    device = record["device_info"]
    timings = {key: value for key, value in record["timings"].items() if value is not None}
    validation = dict(record["validation"])
    if status := validation.pop("status", None):
        validation["ok"] = status != "hard_error"
    return {
        **{key: record[key] for key in ("schema_version", "attempt_id", "tone",
                                       "workload_fingerprint", "device_fingerprint", "status", "elapsed_s", "policy_version",
                                       "artifact_path", "error_code", "failure_stage")},
        "attempt_index": record.get("attempt_index", 0), "recovered_by_retry": record.get("recovered_by_retry", False),
        "request_id": str(record["request_id"]) if record["request_id"] is not None else None,
        "model": work.get("models", {}).get("diffusion", {}).get("file"),
        "resolution": f"{width}x{height}" if width and height else None,
        "duration_s": work.get("requested_duration_s"), "device": device.get("uuid") or "unavailable",
        "telemetry_path": path, "timings": timings,
        "peak_memory": {key: value for key, value in record["resource_peaks"].items() if value is not None},
        "validation": {key: value for key, value in validation.items() if value is not None},
    }


def persist(config, records):
    """Write one unique JSONL file and one short SQLite transaction, after task completion."""
    if not records:
        return None, []
    path = None
    warnings = []
    if config.get("telemetry_enabled", True):
        try:
            directory = Path(config.get("telemetry_dir") or DATA_ROOT / "runs").expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            file = directory / f"{records[0]['attempt_id']}.jsonl"
            with file.open("x", encoding="utf-8", newline="\n") as stream:
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            path = str(file.resolve())
        except (OSError, ValueError, TypeError) as exc:
            warnings.append({"code": "TELEMETRY_UNAVAILABLE", "message": str(exc)})
    if config.get("rsi_enabled", True):
        try:
            from RSI import RSIStore
            store = RSIStore(config.get("rsi_db_path") or DATA_ROOT / "rsi.sqlite3", timeout_s=0.25)
            store.record_run_summaries([summary(record, path) for record in records], builtin_policy=POLICY_VERSION)
        except Exception as exc:  # Optional storage failure must never change the media result.
            warnings.append({"code": "RSI_STORAGE_UNAVAILABLE", "message": str(exc)})
    else:
        warnings.append({"code": "RSI_DISABLED", "message": "RSI history is disabled in the configuration."})
    return path, warnings


def add_receipt(result, path, warnings):
    if path:
        result["telemetry_path"] = path
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
    return result
