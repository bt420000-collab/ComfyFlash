"""t7 · RelayVSR ×2 video upscaling."""

from ..contracts import ParamError
from ..media import VIDEO_EXT


TONE = {
    "id": "t7",
    "name": "RelayVSR 视频 2× 超分",
    "purpose": "将视频用 RelayVSR 放大 2×，保留源帧率和音轨",
    "output": "video",
    "inputs": {"required": ["video_in"], "optional": [], "fields": {
        "video_in": {"type": "path", "role": "source_video", "media_type": "video", "required": True},
    }},
    "outputs": {"media_type": "video", "formats": sorted(VIDEO_EXT), "audio_required": False},
    "controls": {"video_in": {"type": "string", "min_frames": 1, "note": "源视频的本地绝对路径。"},
                 "seed": {"type": "integer", "default": 42}},
    "advanced": {
        "upscale_preset": {"type": "string", "default": "relayvsr", "choices": ["relayvsr", "default"],
                           "note": "可省略。旧请求使用 default 时也执行 RelayVSR 2×。"},
        "steps": {"type": "integer", "default": 1, "choices": [1]},
        "sampler": {"type": "string", "default": "euler", "choices": ["euler"]},
        "scheduler": {"type": "string", "default": "simple", "choices": ["simple"]},
        "cfg": {"type": "number", "default": 1.0, "choices": [1.0]},
        "detail": {"type": "number", "default": 1.0, "min": 0.0, "max": 1.0,
                   "note": "保留超分新增细节的比例。"},
        "protect_source": {"type": "boolean", "default": True,
                           "note": "限制 RelayVSR 在当前帧引入的大块错误明暗与结构。"},
    },
    "models": {"diffusion": "relayvsr_x2_lightx2v_int8_convrot.safetensors",
               "vae_video": "relayvsr_flash_vae_x2_fp32.safetensors"},
    "schema_version": "0.2",
    "version": "0.2",
    "upscale_factor": 2,
    "tags": ["relayvsr", "video", "video_upscale"],
    "capabilities": ["video_upscale"],
    "prompt_format": {"required": False},
    "hardware_requirements": {"device": "cuda"},
    "latency_class": "tens_of_seconds_to_minutes",
    "notes": ["沿用源帧率与原有帧数。音轨按媒体交付规则编码为 AAC。"],
}


def _condition(rt, model, frames):
    from comfy_extras.nodes_wan import RelayVSRConditioning

    return RelayVSRConditioning.execute(model, frames).result


def _protect_source_frames(source, output):
    """Suppress large local colour changes from RelayVSR's neighbouring keyframes."""
    import torch
    from torch.nn import functional as F

    grid = (max(1, source.shape[1] // 8), max(1, source.shape[2] // 8))
    size = output.shape[1:3]
    restored = 0
    for start in range(0, len(source), 8):
        reference = source[start:start + 8].movedim(-1, 1)
        candidate = output[start:start + 8].movedim(-1, 1)
        colour_delta = (F.adaptive_avg_pool2d(candidate, grid) -
                        F.adaptive_avg_pool2d(reference, grid)).abs().amax(dim=1, keepdim=True)
        # Expand the affected area before feathering, so the erroneous contour is covered too.
        colour_delta = F.max_pool2d(colour_delta, kernel_size=3, stride=1, padding=1)
        weights = ((colour_delta - 0.035) / (0.105 - 0.035)).clamp_(0, 1)
        affected = torch.nonzero(weights.flatten(1).amax(dim=1), as_tuple=True)[0].tolist()
        for index in affected:
            baseline = F.interpolate(reference[index:index + 1], size=size, mode="bicubic",
                                     align_corners=False).clamp_(0, 1)
            weight = F.interpolate(weights[index:index + 1], size=size, mode="bilinear",
                                   align_corners=False)
            candidate[index:index + 1].lerp_(baseline, weight)
        restored += len(affected)
    return restored


def play(run):
    from comfy_api.latest import InputImpl
    from comfy_extras.nodes_wan import VSRDecode

    with run.timed_stage("input_decode"):
        components = InputImpl.VideoFromFile(run.video_path).get_components()
        frames = components.images
        audio = components.audio
        fps = float(components.frame_rate)
        del components
    count, height, width, channels = frames.shape
    if not count or fps <= 0 or channels != 3:
        raise ParamError("BAD_INPUT", "RelayVSR requires decodable RGB video frames and a valid frame rate")
    if run.req.get("source_audio") and audio is None:
        raise ParamError("BAD_INPUT", "The source has an audio stream that could not be decoded")
    factor = TONE["upscale_factor"]
    run.width, run.height, run.frames = width * factor, height * factor, count
    run.req.update(fps=fps, requested_frame_count=count, aligned_frame_count=count,
                   requested_duration_s=count / fps)
    model = run.unet()
    cond, latent = run.condition(_condition, model, frames)
    samples = run.sample(model, cond, latent, negative=cond)
    del model, cond, latent
    run._offload_for_decode()
    vae = run.vae("vae_video")
    with run.timed_stage("video_decode"), run.rt.stage():
        upscaled, = VSRDecode.execute(vae, samples, frames, detail=run.params["detail"]).result
    del samples
    if tuple(upscaled.shape) != (count, run.height, run.width, channels):
        raise ParamError("BAD_OUTPUT", "RelayVSR output differs from the expected 2× dimensions or frame count")
    if run.params["protect_source"]:
        with run.timed_stage("protect_source"):
            run.meta["source_protected_frames"] = _protect_source_frames(frames, upscaled)
    del frames
    run.meta["upscale_factor"] = factor
    return run.save_video(upscaled, audio)
