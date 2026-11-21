"""Measure available device resources and explain conservative GPU choices."""

import csv
import subprocess


def _nonnegative_integer(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def nvidia_telemetry():
    """Read optional NVIDIA measurements without starting a CUDA runtime."""
    try:
        run = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,memory.free,utilization.gpu,utilization.memory,driver_version",
             "--format=csv,noheader,nounits"],
            check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    rows = []
    for row in csv.reader(run.stdout.splitlines(), skipinitialspace=True):
        if len(row) != 5:
            continue
        uuid, free, load, memory_load, driver = (field.strip() for field in row)
        if uuid.startswith("GPU-"):
            rows.append({"uuid": uuid, "free_mib": _nonnegative_integer(free),
                         "utilization_pct": _nonnegative_integer(load),
                         "memory_utilization_pct": _nonnegative_integer(memory_load),
                         "driver_version": driver if driver[:1].isdigit() else None})
    return rows


def _device_uuid(value):
    return value.lower().removeprefix("gpu-") if value else None


def build_device_profiles(hardware, *, inventory=()):
    """Normalize a hardware report, joining physical samples by GPU UUID."""
    samples = {_device_uuid(gpu["uuid"]): gpu for gpu in inventory if gpu.get("uuid")}
    profiles = []
    for gpu in sorted(hardware.get("gpus", ()), key=lambda gpu: gpu["index"]):
        index = gpu["index"]
        uuid = _device_uuid(gpu.get("uuid"))
        sample = samples.get(uuid, {})
        free = (value for value in (gpu.get("free_mib"), sample.get("free_mib")) if value is not None)
        profile = {
            "index": index,
            "device": f"cuda:{index}",
            "uuid": f"GPU-{uuid}" if uuid else None,
            "name": gpu.get("name"),
            "ready": bool(gpu.get("ready")),
            "compute_capability": gpu.get("compute_capability"),
            "total_mib": gpu.get("total_mib"),
            "free_mib": min(free, default=None),
            "utilization_pct": sample.get("utilization_pct", gpu.get("utilization_pct")),
            "memory_utilization_pct": sample.get("memory_utilization_pct", gpu.get("memory_utilization_pct")),
            "driver_version": sample.get("driver_version", gpu.get("driver_version")),
            "ram_mib": hardware.get("ram_mib"),
            "free_ram_mib": hardware.get("free_ram_mib"),
            "model_cache": gpu.get("model_cache"),
        }
        profile["unavailable_fields"] = [field for field, value in profile.items() if value is None]
        profiles.append(profile)
    return profiles


def _admission_reasons(profile, min_free_vram_mib, min_free_ram_mib):
    reasons = []
    if not profile["ready"]:
        reasons.append("cuda_unavailable")
    for resource, field, minimum in (("vram", "free_mib", min_free_vram_mib),
                                     ("ram", "free_ram_mib", min_free_ram_mib)):
        if minimum > 0:
            free = profile[field]
            if free is None:
                reasons.append(f"free_{resource}_unavailable")
            elif free < minimum:
                reasons.append(f"insufficient_free_{resource}")
    return reasons


def _score(profile):
    free = profile["free_mib"]
    total = profile["total_mib"]
    load = profile["utilization_pct"]
    return (free if free is not None else -1, total if total is not None else -1,
            -load if load is not None else -101, -profile["index"])


def decide_device(profiles, *, preferred=None, auto_schedule=False, min_free_vram_mib=0,
                  min_free_ram_mib=0, candidate_indices=None):
    """Apply the caller's hard limits before ranking admitted CUDA devices."""
    allowed = None if candidate_indices is None else set(candidate_indices)
    profiles = sorted(profiles, key=lambda profile: profile["index"])
    candidates = []
    rejected = {}
    for profile in profiles:
        device = profile["device"]
        reasons = _admission_reasons(profile, min_free_vram_mib, min_free_ram_mib)
        if allowed is not None and profile["index"] not in allowed:
            reasons.append("outside_candidate_set")
        if not auto_schedule and device != preferred:
            reasons.append("device_pinned" if preferred else "auto_schedule_disabled")
        if reasons:
            rejected[device] = reasons
        else:
            candidates.append(profile)

    if preferred and all(profile["device"] != preferred for profile in profiles):
        rejected[preferred] = ["device_not_visible"]
    selected = max(candidates, key=_score, default=None)
    device = selected["device"] if selected else None
    for profile in candidates:
        if profile["device"] != device:
            rejected[profile["device"]] = ["lower_score"]

    if selected:
        reason = "ranked_by_free_vram_total_vram_and_load" if auto_schedule else "explicit_device"
        confidence = "medium" if selected["free_mib"] is not None and selected["free_ram_mib"] is not None else "low"
    elif not auto_schedule:
        reason, confidence = ("explicit_device_unavailable" if preferred else "auto_schedule_disabled"), "low"
    else:
        reason, confidence = "no_admitted_device", "low"

    return {"selected_device": device,
            "rejected_devices": [{"device": key, "reasons": value} for key, value in rejected.items()],
            "decision_reason": reason, "profile_confidence": confidence,
            "fallback_used": bool(device and preferred and device != preferred)}
