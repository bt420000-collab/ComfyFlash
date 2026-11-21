"""engine.core —— Comfy Flash H3 v0.1 的公共控制、I/O 与执行生命周期。

对外只承诺三个方法：``tone_list()`` / ``tone_info()`` / ``play()``。

Engine 负责的"标准控制面板"：
    prompt / image_in / audio_in / resolution / duration / seed / output
Tone 只负责它自己的配方（模型、LoRA、采样器、条件拼装）。

零服务：本模块不监听端口、不起子进程、不连 HTTP/WebSocket，也不要求 ComfyUI 在运行。
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

from . import artifact, contracts, h3, media, telemetry
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
}

# 同进程热复用，但不许把整套模型都攒在内存里：每类最多留几个。
_CACHE_LIMITS = {"unet": 1, "patched_unet": 1, "clip": 1, "vae": 2, "lora": 2}
_EXECUTION_LOCK = threading.RLock()


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
    return cfg


def _normalize_images(value, field):
    if isinstance(value, (str, Path, dict)):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ParamError("BAD_INPUT", "image_in requires a path, an image Artifact, or a list of them")

    minimum, maximum = field["min_items"], field["max_items"]
    if not minimum <= len(items) <= maximum:
        raise ParamError("BAD_INPUT", f"image_in received {len(items)} images; allowed {minimum}..{maximum}")
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
        if not p.is_file():
            raise ParamError("BAD_INPUT", "找不到输入图片：{}".format(p))
        paths.append(str(p))
        lineage.append(metadata)
    return paths, lineage


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

    def with_lora(self, model):
        strength = float(self.params.get("lora_strength", 1.0))
        if strength == 0.0:
            return model
        path = self._path("lora")
        with self.attempt.measure("model_load"):
            lora = self.engine.cached(("lora", path), self.rt.load_lora, path)
            return self.engine.cached(
                ("patched_unet", self._path("diffusion"), path, strength),
                self.rt.apply_model_lora, model, lora, strength,
            )

    def condition(self, build, *args, **kwargs):
        with self.timed_stage("conditioning"), self.rt.stage():
            return build(self.rt, *args, **kwargs)

    # ---- 采样
    def sample(self, model, cond, latent):
        p = self.params
        if self.video:
            self.meta["sampling"] = {key: p[key] for key in ("steps", "sampler", "scheduler", "cfg", "lora_strength")}
            self.meta["sampling"]["lora"] = self.spec["models"]["lora"] if p["lora_strength"] else None
        noise = self.rt.prepare_noise(latent["samples"], self.seed)
        sigmas = h3.PIKA_SIGMAS if p["scheduler"] == "pika" else None
        negative = None if sigmas is not None and p["cfg"] == 1.0 else h3.zero_out(cond)
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
    def _offload_for_decode(self):
        """Release model residency before the first decoder while keeping its latents."""
        if self.rt.dynamic_vram and not self.engine._cfg.get("video_decode_offload", False):
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
        if self.video:
            self.meta["decoded_frame_count"] = int(frames.shape[0])
        if self.frames and frames.shape[0] > self.frames:
            frames = frames[: self.frames]
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
        runtime = self.rt.versions()
        if self.video:
            runtime.update(self.rt.video_acceleration(), decode_offloaded=getattr(self, "_offloaded", False))
        sampling = self.meta.get("sampling", {key: self.params[key] for key in ("steps", "sampler", "scheduler", "cfg")
                                             if key in self.params})
        return {**self.meta, "tone": self.spec["id"], "tone_version": self.spec.get("version"),
                "seed": self.seed, "request_id": self.req.get("request_id"), "models": models,
                "runtime": runtime, "sampling": sampling,
                "effective_controls": {"prompt": self.prompt, "width": self.width, "height": self.height,
                                       **self.params},
                "provenance": {"inputs": sources, "requested_resolution": self.req["resolution"]}}

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
        started = time.perf_counter()
        with self.attempt.measure("delivery"):
            delivered = artifact.deliver_video(
                frames, self.output, details, fps=rate, audio=audio,
                audio_required=self.spec["outputs"].get("audio_required", False),
                inspection_frames=self.req["inspection_frames"], overwrite=self.req["overwrite"],
            )
        self.meta["timing_s"] = {**delivered["timing_s"], "delivery": round(time.perf_counter() - started, 2)}
        self.meta["artifact"] = delivered
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
                                             "version", "tags", "capabilities", "hardware_requirements", "latency_class")}
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
        info["device"] = self._cfg.get("device", "cuda:0")
        return info

    # ------------------------------------------------------------- 演奏
    def _check_models(self, spec):
        for key, name in spec.get("models", {}).items():
            self.model_path(name, _MODEL_KINDS[key])

    def preflight(self, tone, prompt=None, image_in=None, audio_in=None, resolution=None,
                  duration=None, seed=None, output=None, verbose=None, **advanced):
        """Resolve a request and check local dependencies without starting inference."""
        started = time.time()
        seed_value = None
        try:
            spec = self._spec(tone)
            req = self._resolve(spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced)
            seed_value = req["seed"]
            self._check_models(spec)
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
        **advanced,
    ):
        attempt = telemetry.Attempt(tone, advanced.get("request_id"))
        result = self._play(attempt, tone, prompt, image_in, audio_in, resolution, duration,
                            seed, output, verbose, advanced)
        try:
            record = result["telemetry"] = attempt.finish(result)
            if self._persist_history:
                return telemetry.add_receipt(result, *telemetry.persist(self._cfg, [record]))
        except (OSError, ValueError, TypeError) as exc:
            telemetry.add_receipt(result, None, [{"code": "TELEMETRY_UNAVAILABLE", "message": str(exc)}])
        return result

    def _play(self, attempt, tone, prompt, image_in, audio_in, resolution, duration, seed, output,
              verbose, advanced):
        started = time.time()
        seed_value = None
        verbose = self.verbose if verbose is None else bool(verbose)
        try:
            with attempt.measure("preflight"):
                spec = self._spec(tone)
                req = self._resolve(spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced)
                seed_value = req["seed"]
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

    def _resolve(self, spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced) -> dict:
        inputs = spec.get("inputs", {})
        required = list(inputs.get("required", []))
        accepted = required + list(inputs.get("optional", []))
        controls = spec.get("controls", {})

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
        if image_in is not None:
            if "image_in" not in accepted:
                raise ParamError("UNSUPPORTED_INPUT", f"{spec['name']} does not accept image_in")
            images, input_artifacts = _normalize_images(image_in, inputs["fields"]["image_in"])
            for path in images:
                try:
                    size = media.image_size(path)
                except (OSError, ValueError, SyntaxError) as exc:
                    raise ParamError("BAD_INPUT", f"Unreadable image {path}: {exc}") from exc
                if image_size is None:
                    image_size = size
        elif "image_in" in required:
            raise ParamError("MISSING_INPUT", f"{spec['name']} requires image_in")

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
        timing = contracts.resolve_time(spec, duration, advanced.pop("fps", None))
        delivery = contracts.resolve_delivery(spec, advanced)
        for destination in artifact.output_paths(out_path, spec["output"]).values():
            if destination.is_dir() or delivery["overwrite"] and destination.is_symlink():
                raise ParamError("BAD_OUTPUT", f"Output destination cannot be replaced: {destination}")
            if not delivery["overwrite"] and (destination.exists() or destination.is_symlink()):
                raise ParamError("OUTPUT_EXISTS", f"Output already exists: {destination}; set overwrite=true to replace it")
        if seed is None:
            seed_value = random.SystemRandom().randrange(0, 2 ** 31)
        else:
            seed_value = contracts.scalar_value("seed", seed, controls.get("seed", {"type": "integer"}), "BAD_SEED") % (2 ** 63)

        decl = controls["resolution"]
        res_value = decl["default"] if resolution is None else resolution
        res_value = contracts.scalar_value("resolution", res_value, decl, "BAD_RESOLUTION")
        width, height = contracts.resolve_canvas(spec, res_value, image_size)
        params = contracts.resolve_advanced(spec, advanced)
        return {
            "prompt": prompt, "images": images, "input_artifacts": input_artifacts, "output": str(out_path),
            "width": width, "height": height, "frames": timing.get("aligned_frame_count"),
            "duration": timing.get("requested_duration_s"), "seed": seed_value,
            "resolution": res_value, "advanced": params, **timing, **delivery,
        }
