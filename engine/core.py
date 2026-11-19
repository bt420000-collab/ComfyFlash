"""engine.core —— Comfy Flash H3 v0.1 的公共控制、I/O 与执行生命周期。

对外只承诺三个方法：``tone_list()`` / ``tone_info()`` / ``play()``。

Engine 负责的"标准控制面板"：
    prompt / image_in / audio_in / resolution / duration / seed / output
Tone 只负责它自己的配方（模型、LoRA、采样器、条件拼装）。

零服务：本模块不监听端口、不起子进程、不连 HTTP/WebSocket，也不要求 ComfyUI 在运行。
"""

from __future__ import annotations

import contextlib
import io as _io
import json
import logging
import random
import threading
import time
from pathlib import Path

from . import h3, media
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


class ParamError(Exception):
    """参数/依赖层面的失败：可预期、可读、不打印堆栈。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


def _normalize_images(value) -> list[str]:
    if isinstance(value, (str, Path)):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ParamError("BAD_INPUT", "image_in 只接受路径字符串或路径列表，收到 {}".format(type(value).__name__))

    paths = []
    for item in items:
        p = Path(str(item)).expanduser()
        if not p.is_absolute():
            raise ParamError("BAD_INPUT", "image_in 必须是绝对路径：{}".format(p))
        if not p.is_file():
            raise ParamError("BAD_INPUT", "找不到输入图片：{}".format(p))
        paths.append(str(p))
    if not paths:
        raise ParamError("BAD_INPUT", "image_in 是空的")
    return paths


# ------------------------------------------------------------------ run context
class _Run:
    """一次生成请求的上下文：模型取用 + 采样 + 解码 + 落盘。"""

    def __init__(self, engine: "Flash", spec: dict, req: dict):
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

    @contextlib.contextmanager
    def timed_video_stage(self, name):
        if not self.video:
            yield
            return
        started = time.perf_counter()
        yield
        self.meta.setdefault("timing_s", {})[name] = round(time.perf_counter() - started, 2)

    # ---- 模型
    def _path(self, key: str) -> str:
        name = (self.spec.get("models") or {}).get(key)
        if not name:
            raise ParamError("MISSING_MODEL", "Tone {} 未声明模型 {}".format(self.spec["id"], key))
        return self.engine.model_path(name, _MODEL_KINDS[key])

    def unet(self):
        path = self._path("diffusion")
        return self.engine.cached(("unet", path), self.rt.load_unet, path)

    def clip(self):
        path = self._path("text_encoder")
        return self.engine.cached(("clip", path), self.rt.load_clip, path)

    def vae(self, key: str):
        path = self._path(key)
        return self.engine.cached(("vae", path), self.rt.load_vae, path)

    def with_lora(self, model):
        strength = float(self.params.get("lora_strength", 1.0))
        if strength == 0.0:
            return model
        path = self._path("lora")
        lora = self.engine.cached(("lora", path), self.rt.load_lora, path)
        return self.engine.cached(
            ("patched_unet", self._path("diffusion"), path, strength),
            self.rt.apply_model_lora, model, lora, strength,
        )

    def condition(self, build, *args, **kwargs):
        with self.timed_video_stage("conditioning"), self.rt.stage():
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
        with self.timed_video_stage("sampling"), self.rt.stage():
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
        """视频解码前先把 20GB 底盘请出显存：解码器只要几 GB，混在一起必 OOM。"""
        if self.rt.dynamic_vram or getattr(self, "_offloaded", False):
            return
        self.rt.offload_all()
        self._offloaded = True

    def decode_frames(self, vae, samples):
        if self.video:
            self._offload_for_decode()
        with self.timed_video_stage("video_decode"), self.rt.stage():
            frames = self.rt.decode(vae, samples["samples"])
        if frames.dim() == 5:
            frames = frames.reshape(-1, frames.shape[-3], frames.shape[-2], frames.shape[-1])
        if self.frames and frames.shape[0] > self.frames:
            frames = frames[: self.frames]
        return frames

    def decode_audio(self, vae_audio, samples):
        if self.video:
            self._offload_for_decode()
        with self.timed_video_stage("audio_decode"), self.rt.stage():
            return self.rt.decode_audio(vae_audio, samples["samples"])

    def save_video(self, frames, audio=None):
        if frames.dim() == 4 and frames.shape[0] == 1 and self.frames == 1:
            raise ParamError("BAD_OUTPUT", "t1/t2 产出的帧数异常（只有 1 帧），请检查模型")
        with self.timed_video_stage("encoding"):
            path = media.save_video(frames, self.output, fps=h3.FPS, audio=audio)
        self.meta.update({
            "width": int(frames.shape[2]),
            "height": int(frames.shape[1]),
            "frames": int(frames.shape[0]),
            "duration": round(frames.shape[0] / h3.FPS, 3),
            "fps": h3.FPS,
        })
        return path

    def save_image(self, tensor):
        path = media.save_image(tensor, self.output)
        self.meta.update({"width": int(tensor.shape[-2]), "height": int(tensor.shape[-3])})
        return path


# --------------------------------------------------------------------- Flash
class Flash:
    """四个 Tone 的弹奏入口。同步、单进程、串行。"""

    def __init__(self, config=None, verbose: bool = False):
        self._cfg = resolve_config(config) if isinstance(config, dict) else load_config(config)
        self.verbose = bool(verbose)
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
            out.append({
                "tone": tone_id,
                "name": spec["name"],
                "purpose": spec["purpose"],
                "output": spec["output"],
                "inputs": spec["inputs"],
            })
        return out

    def tone_info(self, tone: str):
        spec = self._spec(tone)
        info = json.loads(json.dumps(spec, ensure_ascii=False))
        deps = []
        for key, name in (spec.get("models") or {}).items():
            kind = _MODEL_KINDS[key]
            path = Path(str(name))
            if not path.is_absolute():
                path = Path(self._cfg["_dirs"][kind]) / path
            deps.append({
                "role": key,
                "dir": kind,
                "file": path.name,
                "path": str(path),
                "exists": path.is_file(),
            })
        info["models"] = deps
        info["models_ready"] = all(item["exists"] for item in deps)
        info["device"] = self._cfg.get("device", "cuda:0")
        return info

    # ------------------------------------------------------------- 演奏
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
        started = time.time()
        seed_value = None
        verbose = self.verbose if verbose is None else bool(verbose)
        try:
            spec = self._spec(tone)
            req = self._resolve(spec, prompt, image_in, audio_in, resolution, duration, seed, output, advanced)
            seed_value = req["seed"]

            for key, name in (spec.get("models") or {}).items():
                self.model_path(name, _MODEL_KINDS[key])

            with self._lock:
                if self._closed:
                    raise ParamError("CLOSED", "Flash 已关闭，请重新 with Flash() 后再调用")
                buf_out, buf_err = _io.StringIO(), _io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                        rt = self.runtime()
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
                            run = _Run(self, spec, req)
                            path = TONES[tone].play(run)
                            meta = dict(run.meta)
                            if run.video:
                                meta["runtime"] = rt.video_acceleration()
                except Exception as exc:  # 运行时失败：保留可定位的诊断信息
                    detail = (buf_err.getvalue() or "") + (buf_out.getvalue() or "")
                    oom = self._rt is not None and self._rt.is_oom(exc)
                    result = self._fail(tone, seed_value, exc, detail, started, "OOM" if oom else "RUNTIME_ERROR")
                    if result["error"]["code"] == "OOM" and self._rt is not None:
                        exc.__traceback__ = None
                        try:
                            self._rt.hard_reset()
                        except Exception:
                            logging.getLogger(__name__).warning("Error unloading models after OOM", exc_info=True)
                        finally:
                            self._cache.clear()
                            self._tick.clear()
                    return result

            result = {
                "ok": True,
                "file": str(path),
                "tone": tone,
                "seed": seed_value,
                "elapsed_s": round(time.time() - started, 2),
            }
            result.update(meta)
            if verbose:
                result["log"] = (buf_err.getvalue() + buf_out.getvalue())[-4000:]
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

        # prompt
        if "prompt" in accepted:
            if prompt is None or not str(prompt).strip():
                raise ParamError("MISSING_INPUT", "{} 需要非空 prompt".format(spec["name"]))
            prompt = str(prompt).strip()

        # image_in
        images: list[str] = []
        if image_in is not None:
            if "image_in" not in accepted:
                raise ParamError(
                    "UNSUPPORTED_INPUT",
                    "{} 不接受 image_in；需要参考图的音色是 t2 / t4".format(spec["name"]),
                )
            images = _normalize_images(image_in)
        elif "image_in" in required:
            raise ParamError("MISSING_INPUT", "{} 需要 image_in（参考图/待编辑图）".format(spec["name"]))

        # audio_in：v0.1 一律不实现，避免虚假能力
        if audio_in is not None:
            raise ParamError(
                "UNSUPPORTED_INPUT",
                "v0.1 的四个 Tone 都不接受 audio_in；H3 的同期声音由模型自身生成，"
                "这里不提供音频输入接口",
            )

        # output
        if output is None or not str(output).strip():
            raise ParamError("MISSING_OUTPUT", "output 必须是完整输出路径，例如 D:/MV/shot01.mp4")
        out_path = Path(str(output)).expanduser()
        if not out_path.is_absolute():
            raise ParamError("MISSING_OUTPUT", "output 必须是绝对路径，收到 {}".format(out_path))
        ext = out_path.suffix.lower()
        if spec["output"] == "video" and ext not in media.VIDEO_EXT:
            raise ParamError(
                "BAD_OUTPUT", "{} 产出视频，扩展名需为 {}，收到 {!r}".format(
                    spec["name"], "/".join(sorted(media.VIDEO_EXT)), ext
                ),
            )
        if spec["output"] == "image" and ext not in media.IMAGE_EXT:
            raise ParamError(
                "BAD_OUTPUT", "{} 产出图片，扩展名需为 {}，收到 {!r}".format(
                    spec["name"], "/".join(sorted(media.IMAGE_EXT)), ext
                ),
            )

        # duration
        frames = None
        duration_value = None
        if duration is not None and "duration" not in controls:
            raise ParamError(
                "UNSUPPORTED_CONTROL",
                "{} 是图像 Tone，不使用 duration".format(spec["name"]),
            )
        if "duration" in controls:
            decl = controls["duration"]
            duration_value = decl["default"] if duration is None else float(duration)
            if not (decl["min"] <= duration_value <= decl["max"]):
                raise ParamError(
                    "BAD_DURATION",
                    "duration 需在 {}–{} 秒之间，收到 {}".format(decl["min"], decl["max"], duration_value),
                )
            frames = h3.align_frame_count(round(duration_value * h3.FPS))

        # seed
        if seed is None:
            seed_value = random.SystemRandom().randrange(0, 2 ** 31)
        else:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ParamError("BAD_SEED", "seed 必须是整数或 None，收到 {!r}".format(seed))
            seed_value = seed % (2 ** 63)

        # resolution
        decl = controls.get("resolution", {})
        res_value = decl.get("default") if resolution is None else resolution
        image_size = media.image_size(images[0]) if images and str(res_value).lower() == "match" else None
        try:
            width, height = h3.resolve_canvas(spec["output"], res_value, image_size)
        except ValueError as exc:
            raise ParamError("BAD_RESOLUTION", str(exc))

        provided = set(advanced)
        params = {}
        for key, decl in (spec.get("advanced") or {}).items():
            value = advanced.pop(key, decl.get("default"))
            if "choices" in decl and value is not None and value not in decl["choices"]:
                raise ParamError(
                    "BAD_PARAM",
                    "{} 的 {} 只支持 {}，收到 {!r}".format(
                        spec["name"], key, "/".join(map(str, decl["choices"])), value
                    ),
                )
            if "min" in decl:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ParamError("BAD_PARAM", "{} 必须是数字".format(key))
                if not (decl["min"] <= value <= decl["max"]):
                    raise ParamError(
                        "BAD_PARAM",
                        "{} 需在 {}–{} 之间，收到 {}".format(key, decl["min"], decl["max"], value),
                    )
                if isinstance(decl.get("default"), int):
                    value = int(round(value))
            params[key] = value
        if spec["output"] == "video":
            # Omitted knobs retain their v0.1 defaults for custom sampling recipes.
            if "scheduler" not in provided and provided & {"steps", "sampler", "cfg"}:
                uses_pika = "sampler" in provided and params["sampler"] == "lcm" and params["steps"] == 6
                if not uses_pika:
                    params["scheduler"] = "simple"
            if params["scheduler"] != "pika":
                if "steps" not in provided:
                    params["steps"] = 20
                if "sampler" not in provided:
                    params["sampler"] = "res_multistep"
            elif params["sampler"] != "lcm" or params["steps"] != len(h3.PIKA_SIGMAS) - 1:
                raise ParamError("BAD_PARAM", "pika 调度需要 lcm 和 6 步；自定义采样请使用其他调度器")
        if advanced:
            raise ParamError(
                "UNSUPPORTED_CONTROL",
                "{} 不认识这些参数：{}（可用：{}）".format(
                    spec["name"], ", ".join(sorted(advanced)), ", ".join(sorted(spec.get("advanced") or {})) or "无"
                ),
            )

        return {
            "prompt": prompt,
            "images": images,
            "output": str(out_path),
            "width": width,
            "height": height,
            "frames": frames,
            "duration": duration_value,
            "seed": seed_value,
            "resolution": res_value,
            "advanced": params,
        }
