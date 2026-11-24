"""t6 · H3 Ref2VA video conditioned on ordered image and motion references."""

from .. import h3, media
from ..contracts import ParamError
from .h3_contract import derived_video_tone
from .t2 import TONE as SINGLE_REFERENCE


MIN_REFERENCE_VIDEO_FRAMES = 5

TONE = derived_video_tone(
    SINGLE_REFERENCE, tone_id="t6", name="Pika 多参考生视频",
    purpose="用按顺序提供的多张图片和一段可选动作底片，生成带同期声的短视频",
    capability="multimedia_reference_to_video", image_role="reference_image",
    prompt_format={
        "required": True, "name": "MiniMax H3 Ref2VA",
        "instruction": "使用 Ref2VA 六段结构。图片按输入顺序引用为 <Picture N>，参考视频引用为 <Video 1>。",
    },
    image_control={"min_items": 1, "max_items": 9,
                   "note": "按引用顺序提供一到九张图片的绝对路径或图像 Artifact。",
                   "limits": "保持宽高比，按生成画布面积缩小参考图。"},
    video_control={"min_frames": MIN_REFERENCE_VIDEO_FRAMES,
                   "note": "可选的一段本地视频绝对路径，按 <Video 1> 引用。读取画面并按 24 fps 取样。"},
    notes=["参考视频只提供视觉条件，模型联合生成输出声音。",
           "参考视频取片首，最长取到输出帧数，模型按 17k+5 帧网格向下对齐。"],
)


def _condition(rt, clip, vae, prompt, width, height, length, images, video):
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3ReferenceToVideo

    return MiniMaxH3ReferenceToVideo.execute(
        clip=clip, vae=vae, prompt=prompt, width=width, height=height, length=length,
        ref_images={f"ref_image_{index}": image for index, image in enumerate(images, 1)},
        ref_videos={"ref_video_1": video} if video is not None else None,
    ).result


def play(run):
    images = [media.load_image(run.rt, path) for path in run.image_paths]
    video = None
    if run.video_path:
        try:
            video = media.load_reference_video(run.rt, run.video_path, fps=h3.FPS, max_frames=run.frames,
                                               max_pixels=h3.MAX_PIXELS)
        except (OSError, ValueError, ImportError, RuntimeError) as exc:
            raise ParamError("BAD_INPUT", f"Unreadable reference video {run.video_path}: {exc}") from exc
        if video.shape[0] < MIN_REFERENCE_VIDEO_FRAMES:
            raise ParamError("BAD_INPUT", f"Reference video must provide at least {MIN_REFERENCE_VIDEO_FRAMES} frames")
    vae = run.vae("vae_video")
    cond, latent = run.condition(_condition, run.clip(), vae, run.prompt, run.width, run.height,
                                 run.frames, images, video)
    del images, video
    return h3.finish_conditioned_video(run, vae, cond, latent)
