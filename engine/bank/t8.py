"""H3 TARA v1.0 single-clip motion transfer."""

from importlib import import_module
import os
from pathlib import Path
import sys
from types import ModuleType

from .. import contracts, h3, media
from .h3_contract import derived_video_tone
from .t2 import TONE as REF2VA


PLUGIN = "ComfyUI-BFSNodes"
TARA_MODULE = "minimax_h3_native_temporal"
GUIDE = "https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI/blob/main/docs/motion-transfer.md"
TONE = derived_video_tone(
    REF2VA, tone_id="t8", name="H3 TARA 动作迁移",
    purpose="用动作视频的表演、节奏与运镜驱动参考图中的角色，保留动作视频的音轨",
    capability="motion_transfer", image_role="identity_reference",
    prompt_format={
        "required": True, "name": "H3 TARA motion_transfer",
        "instruction": "以 motion_transfer: 开头，按 H3 Ref2VA 六段写作，分别标注 <Picture N> 和 <Video 1>。",
    },
    image_control={"min_items": 1, "max_items": 4, "note": "按 <Picture N> 的顺序提供参考图。"},
    video_control={"min_frames": 5, "note": "必填的一段本地动作视频。片首按 24 fps 取样。"},
    notes=["动作视频提供表演和时序。参考图提供人物身份或提示词指定的其他属性。",
           "按 17k+5 网格对齐到动作视频覆盖的帧数。保留原音轨（如有）。",
           "单段首轮，默认 Pika 6 步。动作 LoRA 包含训练适配器 V2。"],
)
TONE["inputs"]["optional"].remove("video_in")
TONE["inputs"]["required"].append("video_in")
TONE["inputs"]["fields"]["video_in"].update(required=True, role="motion_guide")
TONE["controls"]["resolution"]["default"] = "672x384"
TONE["controls"]["duration"].update(default=4.5, min=0.2,
                                    limits="24 fps，按动作视频可用长度向下截到 17k+5 网格。")
TONE["temporal"]["cap_to_reference"] = True
PIKA = {key: REF2VA["advanced"][key]["default"] for key in ("steps", "sampler", "scheduler")}
DMD = {"steps": 8, "sampler": "euler", "scheduler": "simple"}
TONE["advanced"] = {
    "steps": {"type": "integer", "default": PIKA["steps"], "choices": [PIKA["steps"], DMD["steps"]]},
    "cfg": {"type": "number", "default": 1.0, "choices": [1.0]},
    "sampler": {"type": "string", "default": PIKA["sampler"], "choices": [PIKA["sampler"], DMD["sampler"]]},
    "scheduler": {"type": "string", "default": PIKA["scheduler"], "choices": [PIKA["scheduler"], DMD["scheduler"]]},
    "ref_image_size": {"type": "string", "default": "max", "choices": ["max", "match"],
                       "note": "max 优先保身份细节，每张图限制在 1 MP。match 按生成画布缩小。"},
}
TONE["presets"] = {"pika": PIKA, "dmd": DMD}
TONE["sampling_policy"].update(custom_preset="dmd", implicit_custom_keys=["steps", "sampler"],
                               strict_custom=True)
del TONE["models"]["vae_audio"]
TONE["models"].update(
    lora="minimax_h3_motion_transfer_tara_v1.0_ref2va.safetensors",
    lora_turbo=REF2VA["models"]["lora"],
)
TONE["models_by_scheduler"] = {DMD["scheduler"]: {
    "lora_turbo": "minimax_h3_dmd_ref2va_8step_turbo_pruned.safetensors",
}}
TONE["outputs"]["audio_required"] = False
TONE["capabilities"] = ["motion_transfer", "source_audio"]
TONE["plugins"] = [{"name": PLUGIN, "file": f"{PLUGIN}/{TARA_MODULE}.py",
                    "source": "https://github.com/alisson-anjos/ComfyUI-BFSNodes"}]
TONE["workflow_guide"] = GUIDE


def _condition(rt, clip, vae, prompt, width, height, length, images, guide, image_size):
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3ReferenceToVideo

    sized = [h3.resize_to(rt, image, h3.size_within_area(image.shape[2], image.shape[1], 1_000_000))
             for image in images]
    return MiniMaxH3ReferenceToVideo.execute(
        clip=clip, vae=vae, prompt=prompt, width=width, height=height, length=length,
        ref_images={f"ref_image_{index}": image for index, image in enumerate(sized)},
        ref_videos={"ref_video_0": guide}, ref_image_size=image_size,
    ).result


def _tara_module(comfy_root):
    # The package initializer registers HTTP routes. Load the standalone node
    # and its relative imports without starting a ComfyUI server.
    directory = (Path(comfy_root) / "custom_nodes" / PLUGIN).resolve()
    name = f"_comfyflash_bfs_tara_{abs(hash(directory))}"
    if name not in sys.modules:
        package = ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    return import_module(f"{name}.{TARA_MODULE}")


def _motion_model(run):
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3SigmaShift
    from comfy_extras.nodes_model_advanced import ModelAttentionBackend

    package = _tara_module(run.rt.root)
    flash = run.rt.torch.backends.cuda.is_flash_attention_available()
    backend = "flash partitions (no compile)" if flash else "comfy kitchen (experimental)"
    attention = "pytorch attention" if flash else "comfy kitchen attention"
    model, = MiniMaxH3SigmaShift.execute(run.unet(), 12.0, 3.0).result
    model, = ModelAttentionBackend.execute(model, attention).result
    model = run.with_lora(model, role="lora_turbo", cache_patch=False)
    model = run.with_lora(model, cache_patch=False)
    model, = package.BFSMiniMaxH3NativeTemporal().apply(
        model, motion_video_index=1, strength=2.0, window_seconds=0.2,
        falloff_seconds=0.25, max_penalty=6.0, attention_backend=backend,
    )
    return model, backend


def play(run):
    with run.timed_stage("reference_decode"):
        guide = media.load_reference_video(run.rt, run.video_path, fps=h3.FPS,
                                           max_frames=run.frames, max_pixels=run.width * run.height)
        contracts.cap_reference_frames(run.req, TONE["temporal"], len(guide))
        run.frames = run.req["aligned_frame_count"]
        guide = guide[:run.frames]
        audio = (media.load_video_soundtrack(run.rt, run.video_path, run.frames / h3.FPS)
                 if run.req.get("source_audio") else None)
    images = [media.load_image(run.rt, path) for path in run.image_paths]
    vae = run.vae("vae_video")
    cond, latent = run.condition(_condition, run.clip(), vae, run.prompt, run.width, run.height,
                                 run.frames, images, guide, run.params["ref_image_size"])
    del images, guide
    auto_install_key = "BFS_H3_TEMPORAL_AUTO_INSTALL"
    auto_install_configured = auto_install_key in os.environ
    os.environ.setdefault(auto_install_key, "0")
    try:
        model, backend = _motion_model(run)
        samples = run.sample(model, cond, latent, negative=cond)
    finally:
        if not auto_install_configured:
            os.environ.pop(auto_install_key, None)
    run._offload_for_decode(force=True)
    run.meta["motion_transfer"] = {"mode": "tara", "reference_frames": run.frames,
                                   "source_audio": audio is not None, "attention_backend": backend}
    return run.save_video(run.decode_frames(vae, samples), audio)
