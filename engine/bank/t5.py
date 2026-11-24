"""t5 · H3 FL2VA video anchored to a supplied first frame."""

from .. import h3, media
from .h3_contract import derived_video_tone
from .t1 import TONE as TEXT_VIDEO


TONE = derived_video_tone(
    TEXT_VIDEO, tone_id="t5", name="Pika 首帧生视频",
    purpose="用单张图片作为首帧条件，生成带同期声的短视频",
    capability="first_frame_to_video", image_role="first_frame",
    prompt_format={
        "required": True, "name": "MiniMax H3 I2VA",
        "instruction": "先写 I2VA 首帧对齐句，将 <Picture 1> 对齐至 0.00 秒。空一行后写基础模式的三个字段，重点描述后续动作、镜头和声音的变化。",
    },
    image_control={"min_items": 1, "max_items": 1,
                   "note": "首帧图片的绝对路径或图像 Artifact。",
                   "limits": "模型会把首帧调整到生成画布尺寸，建议让图片和输出画布使用同一宽高比。"},
    notes=["首帧条件通过 MiniMaxH3ImageToVideo 编码。", "声音由模型联合生成。"],
)


def _condition(rt, clip, vae, prompt, width, height, length, first_frame):
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo

    return MiniMaxH3ImageToVideo.execute(
        clip=clip, vae=vae, prompt=prompt, width=width, height=height, length=length,
        first_frame=first_frame,
    ).result


def play(run):
    first_frame = media.load_image(run.rt, run.image_paths[0])
    vae = run.vae("vae_video")
    cond, latent = run.condition(_condition, run.clip(), vae, run.prompt, run.width, run.height,
                                 run.frames, first_frame)
    return h3.finish_conditioned_video(run, vae, cond, latent)
