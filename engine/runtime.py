"""engine.runtime —— 把本机 ComfyUI 的推理模块当作底层库绑定（零服务）。

ComfyUI 的 ``comfy/`` 是一个 namespace package（目录里没有 ``__init__.py``），
因此 ``import comfy.sd`` / ``comfy.samplers`` 只会加载推理代码，
不会拉起 ``main.py``、``server.py``、PromptServer、WebSocket 或任务队列。

这里只做三件事：

1. 在 import torch 之前把设备固定下来（CUDA_VISIBLE_DEVICES）；
2. 把 ComfyUI 根目录挂进 ``sys.path``，按需惰性 import 推理模块；
3. 提供加载 / 采样 / 解码 / 卸载的薄封装，供 Bank 里的 Tone 调用。

原始代码来源：https://github.com/Comfy-Org/ComfyUI （GPL-3.0）。
本文件不复制其实现，仅在进程内调用；修改记录见 engine/README.md。
"""

from __future__ import annotations

import contextlib
import gc
import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path


def _bind_path(root: str) -> str:
    root = str(Path(root).expanduser().resolve())
    if not Path(root, "comfy").is_dir():
        raise FileNotFoundError(
            "comfyui_root 指向的目录里没有 comfy/ 包：{}\n"
            "请在 models/config.json 里填写真实的 ComfyUI 根目录。".format(root)
        )
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _pin_device(device: str) -> None:
    """必须在 import torch 之前调用：用 CUDA_VISIBLE_DEVICES 把目标卡设为 cuda:0。"""
    if not device or device == "cpu":
        return
    if not device.startswith("cuda:"):
        raise ValueError("device 只支持 cuda:N 或 cpu，收到：{!r}".format(device))
    index = device.split(":", 1)[1].strip()
    if not index.isdigit():
        raise ValueError("device 格式应为 cuda:0 / cuda:1，收到：{!r}".format(device))
    # 用户自己设过就不覆盖
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", index)


class Runtime:
    """ComfyUI 推理模块的惰性句柄。"""

    def __init__(
        self, comfyui_root: str, device: str = "cuda:0", *,
        dynamic_vram: bool = True, vram_headroom_gb: float = 0.1,
        sage_attention: bool = False, fp16_accumulation: bool = False,
    ):
        _pin_device(device)
        self.root = _bind_path(comfyui_root)
        self.device = device
        self._mods: dict[str, object] = {}
        self._tracker = None
        self.dynamic_vram = self._init_backend(
            dynamic_vram, vram_headroom_gb, sage_attention, fp16_accumulation,
        )

    def _init_backend(self, dynamic_vram, headroom_gb, sage_attention, fp16_accumulation):
        # ComfyUI owns one memory manager for the entire process.
        if "comfy.model_management" in sys.modules:
            return getattr(sys.modules.get("comfy.memory_management"), "aimdo_enabled", False)

        cli = self._mod("comfy.cli_args")
        cli.args.cpu = self.device == "cpu"
        if self.device == "cpu":
            return False

        if sage_attention and importlib.util.find_spec("sageattention") is not None:
            cli.args.use_sage_attention = True
        if fp16_accumulation:
            cli.args.fast.add(cli.PerformanceFeature.Fp16Accumulation)
        if os.name == "nt":
            os.environ.setdefault("MIMALLOC_PURGE_DELAY", "0")
        if "torch" not in sys.modules and not (
            os.environ.get("PYTORCH_CUDA_ALLOC_CONF") or os.environ.get("PYTORCH_ALLOC_CONF")
        ):
            try:
                self._mod("cuda_malloc")
            except ModuleNotFoundError as exc:
                if exc.name != "cuda_malloc":
                    raise

        if not dynamic_vram or not getattr(cli, "enables_dynamic_vram", lambda: False)():
            return False
        try:
            # AIMDO's buffers capture the native library at import time.
            control = self._mod("comfy_aimdo.control")
            try:
                loaded = control.init(nvml_pressure=True)
            except TypeError:
                loaded = control.init()
            if not loaded:
                return False
            mm = self.mm
            if not mm.is_nvidia() or mm.torch_version_numeric < (2, 8):
                return False
            patcher = self._mod("comfy.model_patcher")
            memory = self._mod("comfy.memory_management")
            prefetch = self._mod("comfy.model_prefetch")
            vbar = self._mod("comfy_aimdo.model_vbar")
            if not (
                hasattr(patcher, "PromptModelTracker")
                and hasattr(prefetch, "cleanup_prefetch_queues")
                and hasattr(mm, "reset_cast_buffers")
                and hasattr(vbar, "vbars_reset_watermark_limits")
                and hasattr(control, "set_log_info")
            ):
                return False
            indices = [device.index for device in mm.get_all_torch_devices()]
            headroom = int(float(headroom_gb) * 1024 ** 3)
            try:
                ready = control.init_devices((index, headroom) for index in indices)
            except TypeError:
                ready = control.init_devices(indices)
            if not ready:
                return False
            # Match ComfyUI's native logging threshold before running a model.
            control.set_log_info()
            patcher.CoreModelPatcher = patcher.ModelPatcherDynamic
            memory.aimdo_enabled = True
            return True
        except (ImportError, AttributeError, OSError) as exc:
            logging.getLogger(__name__).info("Dynamic VRAM unavailable: %s", exc)
            return False

    @contextlib.contextmanager
    def generation(self):
        if self.dynamic_vram:
            self._tracker = self._mod("comfy.model_patcher").PromptModelTracker()
        try:
            with self.torch.inference_mode():
                yield
        finally:
            if self._tracker is not None:
                self._tracker.end()
                self._tracker = None

    def track(self, model):
        if self._tracker is not None:
            self._tracker.add(model)

    @contextlib.contextmanager
    def stage(self):
        """Release temporary buffers at the same boundaries as ComfyUI's nodes."""
        try:
            yield
        finally:
            if self.dynamic_vram:
                self._mod("comfy.model_prefetch").cleanup_prefetch_queues()
                self.mm.reset_cast_buffers()
                self._mod("comfy_aimdo.model_vbar").vbars_reset_watermark_limits()

    # ---------------------------------------------------------------- imports
    def _mod(self, name: str):
        mod = self._mods.get(name)
        if mod is None:
            mod = importlib.import_module(name)
            self._mods[name] = mod
        return mod

    def video_acceleration(self):
        attention = getattr(self._mod("comfy.ldm.modules.attention"), "optimized_attention", None)
        matmul = getattr(self.torch.backends.cuda, "matmul", None)
        return {
            "dynamic_vram": self.dynamic_vram,
            "attention_default": getattr(attention, "__name__", None),
            "fp16_accumulation": bool(getattr(matmul, "allow_fp16_accumulation", False)),
        }

    @property
    def torch(self):
        return self._mod("torch")

    @property
    def sd(self):
        return self._mod("comfy.sd")

    @property
    def samplers(self):
        return self._mod("comfy.samplers")

    @property
    def mm(self):
        return self._mod("comfy.model_management")

    @property
    def utils(self):
        return self._mod("comfy.utils")

    @property
    def nested(self):
        return self._mod("comfy.nested_tensor")

    @property
    def sample(self):
        return self._mod("comfy.sample")

    # ------------------------------------------------------------- load / free
    def load_unet(self, path: str):
        return self.sd.load_diffusion_model(path, model_options={})

    def load_clip(self, path: str):
        """H3 系列统一用 minimax 文本编码器（Qwen3-VL-32B + 模态标记）。"""
        return self.sd.load_clip(
            [path],
            embedding_directory=None,
            clip_type=self.sd.CLIPType.MINIMAX,
        )

    def load_vae(self, path: str):
        sd, metadata = self.utils.load_torch_file(path, safe_load=True, return_metadata=True)
        return self.sd.VAE(sd=sd, metadata=metadata)

    def load_lora(self, path: str):
        return self.utils.load_torch_file(path, safe_load=True, return_metadata=True)

    def apply_model_lora(self, model, lora, strength):
        """返回带 LoRA 补丁的 patcher，底层权重由 ComfyUI 共享。"""
        weights, metadata = lora
        patched, _ = self.sd.load_lora_for_models(
            model, None, weights, strength, 0.0, lora_metadata=metadata,
        )
        return patched

    # ----------------------------------------------------------------- sampling
    def sample_latent(
        self,
        model,
        positive,
        negative,
        latent,
        noise,
        cfg: float,
        steps: int,
        sampler_name: str,
        scheduler: str,
        seed: int,
        sigmas=None,
    ):
        if sigmas is not None:
            guider = self.samplers.CFGGuider(model)
            if negative is None:
                guider.inner_set_conds({"positive": positive})
            else:
                guider.set_conds(positive, negative)
                guider.set_cfg(cfg)
            out = guider.sample(
                noise, latent, self.samplers.sampler_object(sampler_name),
                self.torch.tensor(sigmas, dtype=self.torch.float32),
                disable_pbar=True, seed=seed,
            )
            return out.to(device=self.mm.intermediate_device(), dtype=self.mm.intermediate_dtype())

        return self.sample.sample(
            model,
            steps=steps,
            noise=noise,
            cfg=cfg,
            sampler_name=sampler_name,
            scheduler=scheduler,
            positive=positive,
            negative=negative,
            latent_image=latent,
            seed=seed,
            disable_pbar=True,
        )

    def prepare_noise(self, latent, seed: int):
        return self.sample.prepare_noise(latent, seed)

    # ------------------------------------------------------------------ decode
    def decode(self, vae, latent):
        """latent 可能是 AV NestedTensor（视频+音频），这里只取第一路视频。"""
        if getattr(latent, "is_nested", False):
            latent = latent.unbind()[0]
        return vae.decode(latent)

    def decode_audio(self, vae, latent):
        """与 comfy_extras.nodes_audio.vae_decode_audio 等价（含音量归一化）。"""
        torch = self.torch
        if getattr(latent, "is_nested", False):
            latent = latent.unbind()[-1]
        audio = vae.decode(latent).movedim(-1, 1)
        std = torch.std(audio, dim=[1, 2], keepdim=True) * 5.0
        std[std < 1.0] = 1.0
        audio = audio / std
        rate = getattr(vae, "audio_sample_rate_output", getattr(vae, "audio_sample_rate", 32000))
        return {"waveform": audio, "sample_rate": int(rate)}

    # ------------------------------------------------------------------ unload
    def offload_all(self) -> None:
        """把已加载模型赶出显存，但保留 Python 对象（权重留在内存/磁盘映射里）。"""
        mm = self._mods.get("comfy.model_management")
        if mm is None:
            return
        for name in ("unload_all_models", "soft_empty_cache"):
            fn = getattr(mm, name, None)
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                pass
        torch = self._mods.get("torch")
        if torch is not None:
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

    def set_reserve_vram(self, gigabyte: float) -> None:
        """给激活值/解码留出的显存余量（ComfyUI 的 EXTRA_RESERVED_VRAM）。

        16GB 卡跑 20GB int8 底盘时这一步很关键：留得够，权重就少驻留、按块流式；
        留得少，前向能跑但 VAE 解码一定会撞 OOM。
        """
        mm = self.mm
        if gigabyte is not None:
            mm.EXTRA_RESERVED_VRAM = int(float(gigabyte) * 1024 ** 3)

    def hard_reset(self) -> None:
        """彻底清场：同步 → 卸载 → 回收 → 清空显存缓存。

        16 GB 卡上 20 GB 底盘 + 15 GB 文本编码器不可能同时驻留，
        视频换一次生成就得从零开始，否则残余碎片会让下一次直接在加载阶段 OOM。
        """
        torch = self._mods.get("torch")
        if torch is not None:
            try:
                torch.cuda.synchronize()
            except Exception:
                pass
        self.offload_all()
        gc.collect()
        if torch is not None:
            try:
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            except Exception:
                pass

    def unload(self) -> None:
        """退出时释放模型与显存；失败也不该影响调用方。"""
        mm = self._mods.get("comfy.model_management")
        if mm is not None:
            for name in ("unload_all_models", "soft_empty_cache"):
                fn = getattr(mm, name, None)
                if fn is None:
                    continue
                try:
                    fn()
                except Exception:
                    pass
        torch = self._mods.get("torch")
        gc.collect()
        if torch is not None:
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.ipc_collect()
            except Exception:
                pass
        self._mods.clear()
