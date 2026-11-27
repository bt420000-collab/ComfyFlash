"""engine.core —— ComfyFlash 的公共控制、I/O 与执行生命周期。

主要入口为 ``tone_list()``、``tone_info()``、``preflight()`` 和 ``play()``。

Engine 负责的"标准控制面板"：
    prompt / image_in / video_in / audio_in / resolution / duration / seed / output
Tone 只负责它自己的配方（模型、LoRA、采样器、条件拼装）。

零服务：本模块不监听端口、不连 HTTP/WebSocket，也不要求 ComfyUI 在运行。
"""

from __future__ import annotations

import contextlib
from functools import lru_cache
import hashlib
import io as _io
import json
import logging
import os
import random
import threading
import time
from pathlib import Path

from . import artifact, contracts, h3, media, project_policy, telemetry
from .contracts import ParamError
from .bank import TONES
from .runtime import Runtime

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = _ROOT / "models" / "config.json"

_MODEL_KINDS = {
    "diffusion": "diffusion_models",
    "text_encoder": "text_encoders",
    "vae_video": "vae",
    "vae_audio": "vae",
    "vae_image": "vae",
    "lora": "loras",
    "lora_turbo": "loras",
}

# 同进程热复用，但不许把整套模型都攒在内存里：每类最多留几个。
_CACHE_LIMITS = {"unet": 1, "patched_unet": 1, "clip": 1, "vae": 2, "lora": 2}
_EXECUTION_LOCK = threading.RLock()


def _request_spec(spec, request):
    overrides = spec.get("models_by_scheduler", {}).get(request["advanced"].get("scheduler"))
    if overrides:
        return {**spec, "models": {**spec["models"], **overrides}}
    return spec


# --------------------------------------------------------------------- config
def load_config(path=None) -> dict:
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG
    if not cfg_path.is_file():
        raise FileNotFoundError("找不到模型配置：{}".format(cfg_path))
    return resolve_config(json.loads(cfg_path.read_text(encoding="utf-8-sig")), cfg_path)


def resolve_config(settings, path=None) -> dict:
    cfg = dict(settings)
    root = cfg.get("comfyui_root")
    if not root:
        raise ValueError("models/config.json 缺少 comfyui_root（ComfyUI 根目录）")
    base = cfg.get("comfyui_models_path") or str(Path(root) / "models")
    dirs = cfg.get("model_dirs") or {}
    cfg["_dirs"] = {
        key: str(dirs.get(key) or Path(base) / key)
        for key in ("diffusion_models", "text_encoders", "vae", "loras")
    }
    cfg["_path"] = str(path) if path else None
    rsi_path = cfg.get("rsi_db_path") or os.getenv("COMFYFLASH_RSI_DB") or "RSI/data/comfyflash.sqlite3"
    try:
        cfg["_rsi_db_path"] = str(_ROOT / Path(rsi_path).expanduser())
    except (TypeError, ValueError, RuntimeError, OSError):
        cfg["_rsi_db_path"] = rsi_path  # Storage reports invalid config at write time.
    return cfg


def _normalize_images(value, field, planned_images):
    if isinstance(value, (str, Path, dict)):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ParamError("BAD_INPUT", "image_in requires a path, an image Artifact, or a list of them")

    minimum, maximum = field["min_items"], field["max_items"]
    if not minimum <= len(items) <= maximum:
        raise ParamError("BAD_INPUT_COUNT", f"image_in received {len(items)} images; allowed {minimum}..{maximum}")
    paths, lineage = [], []
    for item in items:
        metadata = None
        if isinstance(item, dict):
            if item.get("media_type") != "image":
                raise ParamError("BAD_INPUT", "image_in requires an image Artifact")
            inspection = item.get("inspection")
            if isinstance(inspection, dict) and isinstance(inspection.get("metadata"), str):
                metadata = inspection["metadata"]
            item = item.get("path")
        if not isinstance(item, (str, Path)):
            raise ParamError("BAD_INPUT", "image_in requires path strings")
        p = Path(str(item)).expanduser()
        if not p.is_absolute():
            raise ParamError("BAD_INPUT", "image_in 必须是绝对路径：{}".format(p))
        if p.resolve() not in planned_images and not p.is_file():
            raise ParamError("BAD_INPUT", "找不到输入图片：{}".format(p))
        paths.append(str(p))
        lineage.append(metadata)
    return paths, lineage


def _normalize_video(value, minimum_frames, fps, planned_videos):
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\0" in str(value):
        raise ParamError("BAD_INPUT", "video_in requires an absolute path to one local video file")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ParamError("BAD_INPUT", f"video_in requires an absolute path: {path}")
    try:
        info = planned_videos.get(path.resolve())
        if info is None:
            if not path.is_file():
                raise FileNotFoundError(f"No such file: {path}")
            info = media.probe_video(path, source_geometry=fps is None)
        duration = info.get("duration_s")
        if fps is not None and duration is not None and duration <= (minimum_frames - 1) / fps:
            raise ValueError(f"Video must provide at least {minimum_frames} frames at {fps} fps")
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        raise ParamError("BAD_INPUT", f"Unreadable reference video {path}: {exc}") from exc
    return str(path), info


def _source_video_geometry(info, scale):
    rate = info.get("fps")
    if not rate or rate <= 0:
        raise ParamError("BAD_INPUT", "Cannot determine the source video's frame rate")
    width, height = info["width"] * scale, info["height"] * scale
    count = info.get("frame_count")
    duration = count / rate if count else info.get("duration_s")
    estimate = count or (round(duration * rate) if duration else 0)
    if estimate:
        # The native decoder holds both the source and 2× output as RGB tensors.
        frame_bytes = (info["width"] * info["height"] + width * height) * 3 * 4
        try:
            import psutil
            available = psutil.virtual_memory().available
        except (ImportError, OSError, RuntimeError):
            available = None
        if available is not None and estimate * frame_bytes > available:
            raise ParamError("INSUFFICIENT_RAM", "RelayVSR 的输入和输出帧超出当前可用内存，请缩短源视频或使用较小画布。")
    return width, height, {"fps": rate, "requested_duration_s": duration,
                           "requested_frame_count": count, "aligned_frame_count": count}


@lru_cache(maxsize=64)
def _weight_signature(path, size, modified_ns):
    """Sample model bytes without reading the full checkpoint on each cold run."""
    digest = hashlib.sha256(str(size).encode("ascii") + b"\0")
    with path.open("rb") as stream:
        digest.update(stream.read(65536))
        if size > 65536:
            stream.seek(max(65536, size - 65536))
            digest.update(stream.read(65536))
    return digest.hexdigest()


# ------------------------------------------------------------------ run context
class _Run:
    """一次生成请求的上下文：模型取用 + 采样 + 解码 + 落盘。"""

    def __init__(self, engine: "Flash", spec: dict, req: dict, attempt=None):
        self.engine = engine
        self.rt: Runtime = engine.runtime()
        self.spec = spec
        self.req = req
        self.prompt = req["prompt"]
        self.width = req["width"]
        self.height = req["height"]
        self.frames = req["frames"]
        self.seed = req["seed"]
        self.output = req["output"]
        self.image_paths = req["images"]
        self.video_path = req.get("video_path")
        self.params = req["advanced"]
        self.meta: dict = {}
        self.video = spec["output"] == "video"
        self.attempt = attempt or telemetry.Attempt(spec["id"])
        self._model_details = None

    @contextlib.contextmanager
    def timed_stage(self, name):
        with self.attempt.measure(name):
            yield
        if self.video:
            self.meta.setdefault("timing_s", {})[name] = round(self.attempt.timings[name], 2)

    # ---- 模型
    def _path(self, key: str) -> str:
        name = (self.spec.get("models") or {}).get(key)
        if not name:
            raise ParamError("MISSING_MODEL", "Tone {} 未声明模型 {}".format(self.spec["id"], key))
        return self.engine.model_path(name, _MODEL_KINDS[key])

    def unet(self):
        path = self._path("diffusion")
        with self.attempt.measure("model_load"):
            return self.engine.cached(("unet", path), self.rt.load_unet, path)

    def clip(self):
        path = self._path("text_encoder")
        with self.attempt.measure("model_load"):
            return self.engine.cached(("clip", path), self.rt.load_clip, path)

    def vae(self, key: str):
        path = self._path(key)
        with self.attempt.measure("model_load"):
            return self.engine.cached(("vae", path), self.rt.load_vae, path)

    def with_lora(self, model, *, role="lora", cache_patch=True):
        strength = float(self.params.get("lora_strength", 1.0))
        if strength == 0.0:
            return model
        path = self._path(role)
        with self.attempt.measure("model_load"):
            lora = self.engine.cached(("lora", path), self.rt.load_lora, path)
            if not cache_patch:
                return self.rt.apply_model_lora(model, lora, strength)
            return self.engine.cached(
                ("patched_unet", self._path("diffusion"), path, strength),
                self.rt.apply_model_lora, model, lora, strength,
            )

    def condition(self, build, *args, **kwargs):
        with self.timed_stage("conditioning"), self.rt.stage():
            return build(self.rt, *args, **kwargs)

    # ---- 采样
    def sample(self, model, cond, latent, *, negative=None):
        p = self.params
        if self.video:
            self.meta["sampling"] = {key: p[key] for key in ("steps", "sampler", "scheduler", "cfg", "lora_strength")
                                     if key in p}
            self.meta["sampling"]["lora"] = self.spec["models"].get("lora") if p.get("lora_strength", 1.0) else None
            if "lora_turbo" in self.spec["models"]:
                self.meta["sampling"]["lora_turbo"] = self.spec["models"]["lora_turbo"]
        noise = self.rt.prepare_noise(latent["samples"], self.seed)
        sigmas = h3.PIKA_SIGMAS if p["scheduler"] == "pika" else None
        if negative is None and (sigmas is None or p["cfg"] != 1.0):
            negative = h3.zero_out(cond)
        with self.timed_stage("sampling"), self.rt.stage():
            out = self.rt.sample_latent(
                model,
                cond,
                negative,
                latent["samples"],
                noise,
                cfg=float(p["cfg"]),
                steps=int(p["steps"]),
                sampler_name=p["sampler"],
                scheduler=p["scheduler"],
                seed=self.seed,
                sigmas=sigmas,
            )
        return {"samples": out}

    # ---- 解码 / 落盘
    def _offload_for_decode(self, *, force=False):
        """Release model residency before the first decoder while keeping its latents."""
        if not force and self.rt.dynamic_vram and not self.engine._cfg.get("video_decode_offload", False):
            return
        if getattr(self, "_offloaded", False):
            return
        with self.attempt.measure("decode_offload"):
            self.rt.offload_all()
        self._offloaded = True

    def decode_frames(self, vae, samples):
        if self.video:
            self._offload_for_decode()
        with self.timed_stage("video_decode" if self.video else "image_decode"):
            with self.rt.stage():
                frames = self.rt.decode(vae, samples["samples"])
        if frames.dim() == 5:
            frames = frames.reshape(-1, frames.shape[-3], frames.shape[-2], frames.shape[-1])
        decoded = int(frames.shape[0])
        if self.frames and frames.shape[0] > self.frames:
            frames = frames[: self.frames]
        if self.video:
            actual = int(frames.shape[0])
            self.meta.update(decoded_frame_count=decoded, trimmed_frame_count=decoded - actual,
                             actual_frame_count=actual)
            if decoded > actual:
                self.meta["frames_note"] = f"VAE 解出 {decoded} 帧，截断 {decoded - actual} 帧"
        return frames

    def decode_audio(self, vae_audio, samples):
        if self.video:
            self._offload_for_decode()
        with self.timed_stage("audio_decode"):
            with self.rt.stage():
                return self.rt.decode_audio(vae_audio, samples["samples"])

    def model_details(self):
        if self._model_details is not None:
            return self._model_details
        models = []
        for role in self.spec.get("models", {}):
            path = Path(self._path(role)).resolve()
            stat = path.stat()
            models.append({"role": role, "file": path.name, "path": str(path), "size_bytes": stat.st_size,
                           "fingerprint_algorithm": "sha256-size-and-edges-64k-v1",
                           "fingerprint": _weight_signature(path, stat.st_size, stat.st_mtime_ns),
                           "applied": role != "lora" or self.params.get("lora_strength", 1.0) != 0})
        self._model_details = models
        return models

    def _artifact_details(self):
        models = self.model_details()
        role = self.spec["inputs"].get("fields", {}).get("image_in", {}).get("role")
        sources = [{"role": role, "path": path, "metadata": meta}
                   for path, meta in zip(self.image_paths, self.req["input_artifacts"])]
        if self.video_path:
            role = self.spec["inputs"]["fields"]["video_in"]["role"]
            sources.append({"role": role, "path": self.video_path, "metadata": None})
        runtime = self.rt.versions()
        if self.video:
            runtime.update(self.rt.video_acceleration(), decode_offloaded=getattr(self, "_offloaded", False))
        sampling = self.meta.get("sampling", {key: self.params[key] for key in ("steps", "sampler", "scheduler", "cfg")
                                             if key in self.params})
        provenance = {"inputs": sources, "requested_resolution": self.req["resolution"]}
        if approval := self.req.get("production_approval"):
            provenance["production_approval"] = approval
        return {**self.meta, "tone": self.spec["id"], "tone_version": self.spec.get("version"),
                "seed": self.seed, "request_id": self.req.get("request_id"), "models": models,
                "runtime": runtime, "sampling": sampling,
                "effective_controls": {"prompt": self.prompt, "width": self.width, "height": self.height,
                                       **self.params},
                "provenance": provenance}

    def save_video(self, frames, audio=None):
        total = int(frames.shape[0])
        if total < 1:
            raise ParamError("BAD_OUTPUT", "Video decoder returned zero frames")
        rate = self.req["fps"]
        decoded = self.meta.get("decoded_frame_count", total)
        self.meta.update({
            "width": int(frames.shape[2]), "height": int(frames.shape[1]),
            "frames": total, "duration": round(total / rate, 3), "fps": rate,
            "requested_duration_s": self.req["requested_duration_s"],
            "requested_frame_count": self.req["requested_frame_count"],
            "aligned_frame_count": self.frames, "decoded_frame_count": decoded,
            "trimmed_frame_count": max(0, decoded - total),
            "actual_frame_count": total, "actual_duration_s": total / rate,
        })
        details = self._artifact_details()
        legacy_audio = media.audio_summary(audio, total, rate)
        started = time.perf_counter()
        with self.attempt.measure("delivery"):
            delivered = artifact.deliver_video(
                frames, self.output, details, fps=rate, audio=audio,
                audio_required=self.spec["outputs"].get("audio_required", False),
                inspection_frames=self.req["inspection_frames"], overwrite=self.req["overwrite"],
            )
        self.meta["timing_s"] = {**delivered["timing_s"], "delivery": round(time.perf_counter() - started, 2)}
        self.meta["artifact"] = delivered
        self.meta["validation"] = delivered["validation"]
        self.meta["audio_report"] = legacy_audio
        self.meta["inspection"] = {**delivered["inspection"], "source": "decoded_frames_tensor",
                                   "frame_samples": len(delivered["inspection"]["samples"])}
        return delivered["path"]

    def save_image(self, tensor):
        with self.attempt.measure("delivery"):
            delivered = artifact.deliver_image(tensor, self.output, self._artifact_details(), overwrite=self.req["overwrite"])
        self.meta.update(width=delivered["width"], height=delivered["height"], artifact=delivered)
        return delivered["path"]


# --------------------------------------------------------------------- Flash
class Flash:
    """四个 Tone 的弹奏入口。同步、单进程、串行。"""

    def __init__(self, config=None, verbose: bool = False, *, _persist_history: bool = True):
        self._cfg = resolve_config(config) if isinstance(config, dict) else load_config(config)
        self.verbose = bool(verbose)
        self._persist_history = _persist_history
        self._rt: Runtime | None = None
        self._cache: dict = {}
        self._tick: dict = {}
        self._clock = 0
        self._lock = _EXECUTION_LOCK
        self._closed = False
        self._rsi_store = None

    # ------------------------------------------------------------- 生命周期
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cache.clear()
            self._tick.clear()
            rt, self._rt = self._rt, None
            if rt is not None:
                rt.unload()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def rsi(self):
        """Access the same local notebook used by automatic run summaries."""
        if self._rsi_store is None:
            from RSI import RSIStore
            self._rsi_store = RSIStore(self._cfg["_rsi_db_path"])
        return self._rsi_store

    def runtime(self) -> Runtime:
        if self._closed:
            raise ParamError("CLOSED", "Flash 已关闭，请重新 with Flash() 后再调用")
        if self._rt is None:
            cfg = self._cfg
            self._rt = Runtime(
                cfg["comfyui_root"], cfg.get("device", "cuda:0"),
                dynamic_vram=cfg.get("dynamic_vram", True),
                vram_headroom_gb=cfg.get("vram_headroom_gb", 0.1),
                sage_attention=cfg.get("sage_attention", False),
                fp16_accumulation=cfg.get("fp16_accumulation", False),
                disable_pinned_memory=cfg.get("disable_pinned_memory", False),
                disable_comfy_compiler=cfg.get("disable_comfy_compiler", False),
            )
        return self._rt

    # ------------------------------------------------------------- 模型路径
    def model_path(self, name: str, kind: str) -> str:
        p = Path(str(name)).expanduser()
        if not p.is_absolute():
            p = Path(self._cfg["_dirs"][kind]) / p
        if not p.is_file():
            raise ParamError(
                "MISSING_MODEL",
                "缺少模型文件：{}\n（依赖类型 {}，目录 {})".format(p, kind, self._cfg["_dirs"][kind]),
            )
        return str(p)

    def cached(self, key, loader, *args):
        """按模型和参数热复用；同类超出上限时淘汰最久未用的那个。"""
        group = key[0]
        obj = self._cache.get(key)
        if obj is None:
            limit = _CACHE_LIMITS[group]
            holders = [k for k in self._cache if k[0] == group]
            while len(holders) >= limit:
                oldest = min(holders, key=self._tick.__getitem__)
                del self._cache[oldest]
                del self._tick[oldest]
                holders.remove(oldest)
                if group == "unet":
                    for patched in [k for k in self._cache if k[0] == "patched_unet"]:
                        del self._cache[patched]
                        del self._tick[patched]
            obj = loader(*args)
            self._cache[key] = obj
        self._tick[key] = self._clock = self._clock + 1
        if group != "lora":
            self.runtime().track(obj)
        return obj

    # ------------------------------------------------------------- 发现音色
    def tone_list(self):
        out = []
        for tone_id, module in TONES.items():
            spec = module.TONE
            fields = spec["inputs"].get("fields", {})
            row = {k: spec.get(k) for k in ("name", "purpose", "output", "inputs", "schema_version",
                                             "version", "tags", "capabilities", "hardware_requirements", "latency_class",
                                             "prompt_format")}
            row.update(id=tone_id, tone=tone_id, schema_hash=contracts.schema_hash(contracts.request_schema(spec)),
                       media_inputs=sorted({field["media_type"] for field in fields.values()}),
                       media_outputs=[spec["output"]] + (["audio"] if spec["outputs"].get("audio_required") else []))
            out.append(row)
        return out

    def tone_info(self, tone: str):
        spec = self._spec(tone)
        info = json.loads(json.dumps(spec, ensure_ascii=False))
        info["schema"] = contracts.request_schema(info)
        info["schema_hash"] = contracts.schema_hash(info["schema"])
        deps = []
        for key, name in (spec.get("models") or {}).items():
            kind = _MODEL_KINDS[key]
            path = Path(str(name))
            if not path.is_absolute():
                path = Path(self._cfg["_dirs"][kind]) / path
            exists = path.is_file()
            deps.append({
                "role": key,
                "dir": kind,
                "file": path.name,
                "path": str(path),
                "exists": exists,
                "status": "available" if exists else "missing",
            })
        info["models"] = deps
        info["models_ready"] = all(item["exists"] for item in deps)
        if spec.get("plugins"):
            info["plugins"] = self._plugin_dependencies(spec)
            info["plugins_ready"] = all(item["exists"] for item in info["plugins"])
        info["device"] = self._cfg.get("device", "cuda:0")
        return info

    # ------------------------------------------------------------- 演奏
    def _plugin_dependencies(self, spec):
        root = Path(self._cfg["comfyui_root"]).expanduser() / "custom_nodes"
        return [{**item, "path": str(root / item["file"]), "exists": (root / item["file"]).is_file()}
                for item in spec.get("plugins", ())]

    def _check_models(self, spec):
        models = {key: self.model_path(name, _MODEL_KINDS[key]) for key, name in spec.get("models", {}).items()}
        for item in self._plugin_dependencies(spec):
            if not item["exists"]:
                raise ParamError("MISSING_PLUGIN", f"缺少 {item['name']}：{item['path']}。来源 {item['source']}")
        return models

    def validate_request(self, tone=None, prompt=None, image_in=None, audio_in=None, resolution=None,
                         duration=None, seed=None, output=None, verbose=None, *, video_in=None,
                         _planned_images=None, _planned_videos=None, **advanced):
        """Validate without importing the runtime, including known earlier batch outputs."""
        if self._closed:
            raise ParamError("CLOSED", "Flash 已关闭，请重新 with Flash() 后再调用")
        spec = self._spec(tone)
        req = self._resolve(spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced,
                            planned_images=_planned_images, video_in=video_in, planned_videos=_planned_videos)
        paths = self._check_models(_request_spec(spec, req))
        summary = {key: req.get(key) for key in ("output", "overwrite", "resolution", "width", "height", "seed",
                                                 "requested_duration_s", "requested_frame_count", "aligned_frame_count",
                                                 "fps")}
        if approval := req.get("production_approval"):
            summary["production_approval"] = approval
        return {"ok": True, "tone": tone, "model_paths": paths, **summary}

    def preflight(self, tone, prompt=None, image_in=None, audio_in=None, resolution=None,
                  duration=None, seed=None, output=None, verbose=None, *, video_in=None, **advanced):
        """Resolve a request and check local dependencies without starting inference."""
        started = time.time()
        seed_value = None
        try:
            spec = self._spec(tone)
            req = self._resolve(spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced,
                                video_in=video_in)
            seed_value = req["seed"]
            self._check_models(_request_spec(spec, req))
            return {"ok": True, "tone": tone, "resolved": req}
        except ParamError as exc:
            return self._fail(tone, seed_value, exc, None, started, exc.code)

    def play(
        self,
        tone: str,
        prompt=None,
        image_in=None,
        audio_in=None,
        resolution=None,
        duration=None,
        seed=None,
        output=None,
        verbose=None,
        *,
        video_in=None,
        **advanced,
    ):
        attempt = telemetry.Attempt(tone, advanced.get("request_id"))
        result = self._play(attempt, tone, prompt, image_in, audio_in, resolution, duration,
                            seed, output, verbose, advanced, video_in=video_in)
        result.setdefault("device", os.getenv("COMFYFLASH_DEVICE_LABEL") or self._cfg.get("device", "cuda:0"))
        try:
            record = result["telemetry"] = attempt.finish(result)
            if self._persist_history:
                module = TONES.get(str(tone))
                request = {"prompt": prompt, "image_in": image_in, "output": output}
                legacy = telemetry.legacy_execution(record, result, request, module.TONE if module else None)
                path, warnings, linked = telemetry.persist(self._cfg, [record], [legacy])
                if record["attempt_id"] in linked:
                    result["run_id"] = record["attempt_id"]
                for warning in warnings:
                    if warning["code"] == "RSI_STORAGE_UNAVAILABLE":
                        logging.getLogger(__name__).warning("Unable to persist RSI run: %s", warning["message"])
                return telemetry.add_receipt(result, path, warnings)
        except (OSError, ValueError, TypeError) as exc:
            telemetry.add_receipt(result, None, [{"code": "TELEMETRY_UNAVAILABLE", "message": str(exc)}])
        return result

    def _play(self, attempt, tone, prompt, image_in, audio_in, resolution, duration, seed, output,
              verbose, advanced, *, video_in=None):
        started = time.time()
        seed_value = None
        verbose = self.verbose if verbose is None else bool(verbose)
        try:
            with attempt.measure("preflight"):
                spec = self._spec(tone)
                req = self._resolve(spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced,
                                    video_in=video_in)
                seed_value = req["seed"]
                spec = _request_spec(spec, req)
                attempt.configure(spec, req, self._cfg)
                self._check_models(spec)

            with self._lock:
                if self._closed:
                    raise ParamError("CLOSED", "Flash 已关闭，请重新 with Flash() 后再调用")
                buf_out, buf_err = _io.StringIO(), _io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                        with attempt.measure("runtime_init"):
                            rt = self.runtime()
                            attempt.enter_runtime(rt)
                        with rt.generation():
                            if not rt.dynamic_vram:
                                reserve = self._cfg.get(
                                    "reserve_vram_gb", spec.get("reserve_vram_gb", 0.6),
                                )
                                rt.set_reserve_vram(reserve)
                                if spec["output"] == "video":
                                    rt.hard_reset()
                                else:
                                    rt.offload_all()
                            run = _Run(self, spec, req, attempt)
                            try:
                                attempt.set_models(run.model_details())
                            except (OSError, ParamError):
                                pass  # Path and role remain available when a model can't be signed.
                            with attempt.measure("generation"):
                                path = TONES[tone].play(run)
                            meta = dict(run.meta)
                            if run.video:
                                meta["runtime"] = {**rt.video_acceleration(),
                                                   "decode_offloaded": getattr(run, "_offloaded", False)}
                except Exception as exc:  # 运行时失败：保留可定位的诊断信息
                    delivery_failed = isinstance(exc, artifact.DeliveryError)
                    cause = (exc.__cause__ or exc) if delivery_failed else exc
                    oom = ((self._rt is not None and self._rt.is_oom(cause))
                           or "out of memory" in str(cause).lower())
                    if delivery_failed and not oom:
                        raise
                    detail = (buf_err.getvalue() or "") + (buf_out.getvalue() or "")
                    result = self._fail(tone, seed_value, cause, detail, started, "OOM" if oom else "RUNTIME_ERROR")
                    if result["error"]["code"] == "OOM" and self._rt is not None:
                        exc.__traceback__ = None
                        cause.__traceback__ = None
                        try:
                            self._rt.hard_reset()
                        except Exception:
                            logging.getLogger(__name__).warning("Error unloading models after OOM", exc_info=True)
                        finally:
                            self._cache.clear()
                            self._tick.clear()
                    return result
                finally:
                    attempt.snapshot("end")

            result = {
                "ok": True,
                "file": str(path),
                "tone": tone,
                "seed": seed_value,
                "elapsed_s": round(time.time() - started, 2),
            }
            result.update(meta)
            if "request_id" in req:
                result["request_id"] = req["request_id"]
            if verbose:
                result["log"] = (buf_err.getvalue() + buf_out.getvalue())[-4000:]
            return result

        except artifact.DeliveryError as exc:
            result = self._fail(tone, seed_value, exc, None, started, exc.code)
            result["validation"] = exc.validation
            if exc.recoverable_paths:
                result["recoverable_paths"] = exc.recoverable_paths
            return result
        except ParamError as exc:
            return self._fail(tone, seed_value, exc, None, started, exc.code)
        except Exception as exc:
            return self._fail(tone, seed_value, exc, None, started, "ENGINE_ERROR")

    # ------------------------------------------------------------- 内部
    def _spec(self, tone: str) -> dict:
        if tone is None:
            raise ParamError("MISSING_INPUT", "请求缺少 tone")
        module = TONES.get(str(tone))
        if module is None:
            raise ParamError(
                "UNKNOWN_TONE",
                "没有音色 {!r}；可用：{}".format(tone, ", ".join(TONES)),
            )
        return module.TONE

    def _fail(self, tone, seed, exc, detail, started, code):
        message = str(exc)
        err = {"code": code, "message": message}
        if code == "OOM" or "out of memory" in message.lower():
            err["code"] = "OOM"
            module = TONES.get(str(tone))
            if module and module.TONE.get("upscale_factor"):
                err["hint"] = "显存不足。可先截取较短片段或使用较小画布的源视频，再执行超分。"
            else:
                err["hint"] = (
                    "显存不足。可缩小 resolution，视频可缩短 duration。"
                    "16GB 单卡短视频对照设置为 768x448、1.6 秒，图像对照设置为 1k。"
                )
        if detail:
            text = detail.strip()
            if text:
                err["detail"] = text[-4000:]
        if not isinstance(exc, ParamError) and self.verbose:
            err["type"] = type(exc).__name__
        return {
            "ok": False,
            "file": None,
            "tone": tone,
            "seed": seed,
            "error": err,
            "elapsed_s": round(time.time() - started, 2),
        }

    def _resolve(self, spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced,
                 *, planned_images=None, video_in=None, planned_videos=None) -> dict:
        inputs = spec.get("inputs", {})
        required = list(inputs.get("required", []))
        accepted = required + list(inputs.get("optional", []))
        controls = spec.get("controls", {})
        scale = spec.get("upscale_factor")

        if prompt is None and "prompt" in required:
            raise ParamError("MISSING_INPUT", f"{spec['name']} requires prompt")
        if prompt is not None:
            if "prompt" not in accepted:
                raise ParamError("UNSUPPORTED_INPUT", f"{spec['name']} does not accept prompt")
            prompt = contracts.scalar_value("prompt", prompt, controls["prompt"], "MISSING_INPUT").strip()
        if audio_in is not None:
            raise ParamError("UNSUPPORTED_INPUT", f"{spec['name']} does not accept audio_in")

        images, input_artifacts = [], []
        image_size = None
        planned_images = planned_images or {}
        if image_in is not None:
            if "image_in" not in accepted:
                raise ParamError("UNSUPPORTED_INPUT", f"{spec['name']} does not accept image_in")
            images, input_artifacts = _normalize_images(image_in, inputs["fields"]["image_in"], planned_images)
            for path in images:
                try:
                    size = planned_images.get(Path(path).resolve()) or media.image_size(path)
                except (OSError, ValueError, SyntaxError) as exc:
                    raise ParamError("BAD_INPUT", f"Unreadable image {path}: {exc}") from exc
                if image_size is None:
                    image_size = size
        elif "image_in" in required:
            raise ParamError("MISSING_INPUT", f"{spec['name']} requires image_in")

        video = {}
        if video_in is not None:
            if "video_in" not in accepted:
                raise ParamError("UNSUPPORTED_INPUT", f"{spec['name']} does not accept video_in")
            video["video_path"], video_info = _normalize_video(
                video_in, controls["video_in"]["min_frames"], spec.get("temporal", {}).get("fps"), planned_videos or {},
            )
        elif "video_in" in required:
            raise ParamError("MISSING_INPUT", f"{spec['name']} requires video_in")

        if not isinstance(output, (str, Path)) or not str(output).strip():
            raise ParamError("MISSING_OUTPUT", "output must be an absolute file path")
        out_path = Path(output).expanduser()
        if not out_path.is_absolute():
            raise ParamError("MISSING_OUTPUT", f"output must be absolute: {out_path}")
        if "\0" in str(out_path):
            raise ParamError("BAD_OUTPUT", "Output path contains a NUL character")
        if os.name == "nt":
            for part in out_path.parts[1:]:
                if hasattr(os.path, "isreserved"):
                    reserved = os.path.isreserved(part)
                else:  # Windows Python 3.10–3.12
                    reserved = (Path(part).is_reserved() or part.endswith((" ", ".")) or
                                any(ord(c) < 32 or c in '<>:"|?*' for c in part))
                if reserved and part != "..":
                    raise ParamError("BAD_OUTPUT", f"Reserved Windows path component: {part!r}")
        formats = spec["outputs"]["formats"]
        if out_path.suffix.lower() not in formats or out_path.is_dir():
            raise ParamError("BAD_OUTPUT", f"Expected an output file with extension in {formats}: {out_path}")
        parent = next((p for p in out_path.parents if p.exists()), None)
        if parent is None or not parent.is_dir() or not os.access(parent, os.W_OK):
            raise ParamError("BAD_OUTPUT", f"Output parent cannot be created or written: {out_path.parent}")

        advanced = dict(advanced)
        proof = {}
        if spec["output"] == "video" and not scale:
            approval = project_policy.check_video_approval(
                out_path, spec["id"], prompt, images, video.get("video_path"), advanced.pop("production_approval", None),
            )
            if approval:
                proof["production_approval"] = approval
        timing = contracts.resolve_time(spec, duration, advanced.pop("fps", None))
        if spec.get("temporal", {}).get("cap_to_reference"):
            if video_info.get("duration_s") is not None:
                contracts.cap_reference_frames(timing, spec["temporal"],
                                               round(video_info["duration_s"] * timing["fps"]))
            video["source_audio"] = video_info.get("audio_present", False)
        delivery = contracts.resolve_delivery(spec, advanced)
        for destination in artifact.output_paths(out_path, spec["output"]).values():
            if destination.is_dir() or delivery["overwrite"] and destination.is_symlink():
                raise ParamError("BAD_OUTPUT", f"Output destination cannot be replaced: {destination}")
            if not delivery["overwrite"] and (destination.exists() or destination.is_symlink()):
                raise ParamError("OUTPUT_EXISTS", f"Output already exists: {destination}; set overwrite=true to replace it")
        if seed is None:
            seed_value = controls.get("seed", {}).get("default")
            if seed_value is None:
                seed_value = random.SystemRandom().randrange(0, 2 ** 31)
        else:
            seed_value = contracts.scalar_value("seed", seed, controls.get("seed", {"type": "integer"}), "BAD_SEED") % (2 ** 63)

        params = contracts.resolve_advanced(spec, advanced)
        if scale:
            if resolution is not None:
                raise ParamError("UNSUPPORTED_CONTROL", f"{spec['name']} determines the output size from the source video")
            res_value = None
            if params.get("upscale_preset") == "default":
                params["upscale_preset"] = spec["advanced"]["upscale_preset"]["default"]
            width, height, timing = _source_video_geometry(video_info, scale)
            video["source_audio"] = video_info.get("audio_present", False)
        else:
            decl = controls["resolution"]
            res_value = decl["default"] if resolution is None else resolution
            res_value = contracts.scalar_value("resolution", res_value, decl, "BAD_RESOLUTION")
            width, height = contracts.resolve_canvas(spec, res_value, image_size)
        return {
            "prompt": prompt, "images": images, "input_artifacts": input_artifacts, "output": str(out_path),
            "width": width, "height": height, "frames": timing.get("aligned_frame_count"),
            "duration": timing.get("requested_duration_s"), "seed": seed_value,
            "resolution": res_value, "advanced": params, **timing, **delivery, **video, **proof,
        }
