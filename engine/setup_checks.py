"""Offline registration checks and explicitly approved weight downloads."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

VIDEO_REVISION = "MiniMax-H3/resolve/4b53a35191437de05370da548e59aeabe2a5a09a"
IMAGE_REVISION = "MiniMax-H3-Image/resolve/0bb149f51e3156ed4b90dd45d5620346bfe1df8f"
INSTALL_GUIDE = "https://docs.comfy.org/installation"
MODEL_LICENSES = [
    "https://huggingface.co/Comfy-Org/MiniMax-H3",
    "https://huggingface.co/Comfy-Org/MiniMax-H3-Image",
]


def artifact(revision, file, size, digest):
    return {"bytes": size, "sha256": digest, "url": f"https://huggingface.co/Comfy-Org/{revision}/{file}"}


ARTIFACTS = {
    Path(path).name: artifact(revision, path, size, digest)
    for revision, path, size, digest in (
        (VIDEO_REVISION, "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
         20970379616, "e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a"),
        (VIDEO_REVISION, "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
         20970379616, "9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779"),
        (IMAGE_REVISION, "diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors",
         20979314802, "83eb140f1ff95c068f8a6886e6d3f3d0d4ab2e07bb2fe6d2ef992a51146ee622"),
        (VIDEO_REVISION, "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
         15687142551, "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6"),
        (VIDEO_REVISION, "vae/minimax_h3_audio_vae_fp32.safetensors",
         605254808, "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48"),
        (VIDEO_REVISION, "vae/minimax_h3_video_vae_int8_convrot.safetensors",
         2811065184, "52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e"),
        (IMAGE_REVISION, "vae/minimax_h3_image_vae_fp16.safetensors",
         1168505312, "d80e9c2e5cfc9844869961ff5a58c9a9e52182df6bfb30b26018dd7f348721af"),
        (VIDEO_REVISION, "loras/pika_h3_flex_fl2va_v1_bf16.safetensors",
         1956172304, "cc5453aeecd6189468409003fe4cf1b57e52ff3a830a0a154b87decbe07358b7"),
        (VIDEO_REVISION, "loras/pika_h3_flex_ref2va_v1_bf16.safetensors",
         1956172304, "de3745ceb7d8d5c20898dd8fa3a05f1c0b00089ccd55297b2a8bab95c766b0e0"),
    )
}

LIBRARIES = ("torch", "PIL.Image", "numpy", "av", "safetensors", "comfy_kitchen", "psutil")
VERIFIED_VRAM_MIB = 16000
RECOMMENDED_RAM_MIB = 32 * 1024
BATCH_FREE_VRAM_MIB = 14 * 1024
BATCH_FREE_RAM_MIB = 16 * 1024

PROBE = """
import contextlib
import importlib
import json
import sys

def probe_video_acceleration(torch, gpu):
    verified = {"sage_attention": False, "fp16_accumulation": False}
    with torch.inference_mode():
        try:
            from sageattention import sageattn
            q = torch.ones((1, 1, 128, 128), device="cuda", dtype=torch.bfloat16)
            output = sageattn(q, q, q, tensor_layout="HND", smooth_k=False)
            verified["sage_attention"] = bool(torch.isfinite(output).all().item())
        except Exception as exc:
            gpu["sage_attention_error"] = f"{type(exc).__name__}: {exc}"

        matmul = torch.backends.cuda.matmul
        previous = getattr(matmul, "allow_fp16_accumulation", None)
        if previous is not None:
            try:
                matmul.allow_fp16_accumulation = True
                matrix = torch.ones((128, 128), device="cuda", dtype=torch.float16)
                verified["fp16_accumulation"] = bool(torch.isfinite(matrix @ matrix).all().item())
            except (AttributeError, RuntimeError) as exc:
                gpu["fp16_accumulation_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                matmul.allow_fp16_accumulation = previous
    return verified

root, *deps = sys.argv[1:]
report = {"ready": False, "version": sys.version.split()[0], "missing": [], "errors": [], "gpus": []}
if root:
    sys.path.insert(0, root)
with contextlib.redirect_stdout(sys.stderr):
    if sys.version_info < (3, 10):
        report["errors"].append("Python 3.10+")
    for name in deps:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            report["missing"].append(exc.name or name)
        except Exception as exc:
            report["errors"].append(f"{name}: {exc}")
    try:
        import psutil
        mem = psutil.virtual_memory()
        report.update(ram_mib=mem.total // 2**20, free_ram_mib=mem.available // 2**20)
    except ImportError:
        pass
    if "torch" in sys.modules:
        import torch
        report.update(torch=str(torch.__version__), cuda=torch.version.cuda)
        for index in range(torch.cuda.device_count()):
            gpu = {"index": index, "ready": False}
            report["gpus"].append(gpu)
            try:
                with torch.cuda.device(index):
                    free, total = torch.cuda.mem_get_info()
                    capability = torch.cuda.get_device_capability(index)
                    gpu.update(name=torch.cuda.get_device_name(), total_mib=total // 2**20,
                               free_mib=free // 2**20, compute_capability=capability)
                    uuid = getattr(torch.cuda.get_device_properties(index), "uuid", None)
                    if uuid is not None:
                        gpu["uuid"] = str(uuid)
                    value = torch.ones(2, device="cuda", dtype=torch.bfloat16)
                    (value + value).sum().item()
                    gpu["ready"] = True
                    if root and capability[0] >= 8:
                        gpu["video_acceleration"] = probe_video_acceleration(torch, gpu)
            except Exception as exc:
                gpu["error"] = str(exc)
    if root and any(gpu["ready"] for gpu in report["gpus"]) and not (report["missing"] or report["errors"]):
        try:
            import av
            import comfy.sd
            import comfy.samplers
            from comfy.supported_models import MiniMaxH3, MiniMaxH3Image
            av.Codec("libx264", "w")
            av.Codec("aac", "w")
        except Exception as exc:
            report["errors"].append(f"ComfyUI / H3: {exc}")
    report["ready"] = not (report["missing"] or report["errors"])
print(json.dumps(report))
"""


def probe_python(python, comfy=None, *, hardware_only=False):
    deps = ("torch", "psutil") if hardware_only else LIBRARIES
    try:
        run = subprocess.run(
            [str(python), "-B", "-c", PROBE, str(comfy or ""), *deps],
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        if run.returncode:
            raise ValueError((run.stderr.strip().splitlines() or [f"exit {run.returncode}"])[-1])
        return {"path": str(python), **json.loads(run.stdout)}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"path": str(python), "ready": False, "errors": [str(exc)], "gpus": []}


def choose_python(comfy, requested):
    if requested:
        candidates = [shutil.which(requested) or requested]
    else:
        candidates = []
        if comfy:
            venv = Path("Scripts/python.exe") if sys.platform == "win32" else Path("bin/python")
            candidates.append(comfy.parent / "python_embeded" / "python.exe")
            candidates.extend(folder / name / venv for folder in (comfy, comfy.parent)
                              for name in (".venv", "venv"))
        candidates.append(sys.executable)
    attempts = []
    for item in dict.fromkeys(candidates):
        path = Path(item).expanduser().absolute()
        if not path.is_file():
            if requested:
                attempts.append({"path": str(path), "ready": False, "errors": ["找不到解释器"]})
            continue
        runtime = probe_python(path, comfy)
        if runtime["ready"] and any(gpu["ready"] for gpu in runtime["gpus"]):
            return runtime
        attempts.append(runtime)
    best = max(attempts, key=lambda item: (item["ready"], bool(item.get("gpus"))), default={"ready": False})
    return {**best, "attempts": attempts}


def physical_ram_mib():
    if os.name == "nt":
        installed_kib = ctypes.c_uint64()
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(installed_kib)):
            return installed_kib.value // 1024
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2**20
    except (AttributeError, OSError, ValueError):
        return None


def nvidia_inventory():
    try:
        run = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=8,
        )
        return [
            {"index": int(index), "uuid": uuid, "name": name,
             "total_mib": int(total), "free_mib": int(free), "ready": False}
            for index, uuid, name, total, free in (line.split(", ") for line in run.stdout.splitlines())
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def hardware_report(runtime, preferred=None, *, inventory=False, allow_parallel=True):
    cuda_gpus = runtime.get("gpus", [])
    detected = nvidia_inventory() if inventory or cuda_gpus else []
    physical_free = {
        gpu["uuid"].lower().removeprefix("gpu-"): gpu["free_mib"]
        for gpu in detected if gpu.get("uuid")
    }
    gpus = []
    for gpu in cuda_gpus or detected:
        device = dict(gpu)
        uuid = gpu.get("uuid", "").lower().removeprefix("gpu-")
        if uuid in physical_free and "free_mib" in gpu:
            device["free_mib"] = min(gpu["free_mib"], physical_free[uuid])
        gpus.append(device)
    ram = runtime.get("ram_mib") or (physical_ram_mib() if inventory else None)
    free_ram = runtime.get("free_ram_mib")
    usable = sorted((gpu for gpu in gpus if gpu["ready"]),
                    key=lambda gpu: (gpu["total_mib"], gpu["free_mib"]), reverse=True)
    device = next((gpu for gpu in usable if f"cuda:{gpu['index']}" == preferred), None)
    if device is None and usable:
        device = usable[0]
    qualified = [gpu["index"] for gpu in usable if gpu["total_mib"] >= VERIFIED_VRAM_MIB]
    batch_devices = qualified if len(qualified) > 1 and (ram or 0) >= RECOMMENDED_RAM_MIB else []
    active_devices = [gpu["index"] for gpu in usable
                      if gpu["index"] in batch_devices and gpu["free_mib"] >= BATCH_FREE_VRAM_MIB]
    if (free_ram or 0) < BATCH_FREE_RAM_MIB:
        active_devices = []
    meets_baseline = bool(qualified and (ram or 0) >= RECOMMENDED_RAM_MIB)
    if meets_baseline:
        status = "达到 ComfyFlash 已验证的显存级别和建议内存。"
    elif usable:
        status = "CUDA 可用，配置低于 ComfyFlash 的验证参考。建议先用较小规格实测。"
    else:
        status = "尚未通过 CUDA 检查。请核对 NVIDIA 驱动、CUDA 版 PyTorch 和可用显存。"
    count = max(len(gpus), len(detected))
    if count > 1:
        status += f"检测到 {count} 块 GPU，当前 CUDA 可用 {len(usable)} 块。"
        if batch_devices and allow_parallel:
            status += "生成多个独立素材时会按当时的空闲资源启用多卡并发，单个素材使用一块卡。"
        elif batch_devices:
            status += "可以启用自动选卡，在资源允许时用多卡并发生成独立素材。"
        else:
            status += "当前以单卡运行条件评估。"
    return {"gpus": gpus, "detected_gpu_count": count, "ram_mib": ram,
            "free_ram_mib": free_ram, "device": device,
            "batch_devices": batch_devices, "active_devices": active_devices,
            "meets_baseline": meets_baseline, "summary": status}


def model_issue(path, entry):
    try:
        with path.open("rb") as weight:
            if os.fstat(weight.fileno()).st_size != entry["bytes"]:
                return "size_mismatch"
            header_size = int.from_bytes(weight.read(8), "little")
            if not 0 < header_size <= min(entry["bytes"] - 8, 100_000_000):
                return "invalid_header"
            header = json.loads(weight.read(header_size))
        cursor = 0
        offsets = []
        for name, tensor in header.items():
            if name == "__metadata__":
                continue
            if not isinstance(tensor["dtype"], str) or not all(
                isinstance(extent, int) and extent >= 0 for extent in tensor["shape"]
            ):
                return "invalid_header"
            begin, end = tensor["data_offsets"]
            if not isinstance(begin, int) or not isinstance(end, int):
                return "invalid_header"
            offsets.append((begin, end))
        offsets.sort()
        for begin, end in offsets:
            if begin != cursor or end < begin:
                return "invalid_header"
            cursor = end
        if not offsets or cursor != entry["bytes"] - 8 - header_size:
            return "invalid_header"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    except (AttributeError, KeyError, ValueError, TypeError):
        return "invalid_header"
    return None


def download_space(entries):
    volumes = {}
    for entry in entries:
        destination = Path(entry["path"]).parent
        existing = next((p for p in (destination, *destination.parents) if p.is_dir()), None)
        try:
            if existing is None:
                continue
            drive = existing.drive or os.stat(existing).st_dev
            if drive not in volumes:
                volumes[drive] = {"path": str(existing), "free_bytes": shutil.disk_usage(existing).free,
                                  "download_bytes": 0}
            volumes[drive]["download_bytes"] += entry["bytes"]
        except OSError:
            continue
    return list(volumes.values())


def download_weight(entry):
    path = Path(entry["path"])
    if not model_issue(path, entry):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < entry["bytes"]:
        raise OSError(f"磁盘空间不足 | {path.parent} | 此文件需要 {entry['bytes']} 字节")
    digest = hashlib.sha256()
    size = 0
    with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as output:
        temporary = Path(output.name)
    try:
        with urlopen(entry["url"], timeout=60) as response, temporary.open("wb") as output:
            while data := response.read(4 * 1024 * 1024):
                size += len(data)
                if size > entry["bytes"]:
                    raise ValueError(f"模型下载尺寸超过清单 | {path.name}")
                digest.update(data)
                output.write(data)
        if size != entry["bytes"] or digest.hexdigest() != entry["sha256"] or model_issue(temporary, entry):
            raise ValueError(f"模型下载未通过校验 | {path.name}")
        if not model_issue(path, entry):
            return False
        temporary.replace(path)
        return True
    finally:
        temporary.unlink(missing_ok=True)
