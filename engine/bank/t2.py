"""t2 · Pika 参考生视频 —— H3 ref2va 底盘 + Pika Flex 参考 LoRA，一张图定人物/场景。

配方来源（本机实测可跑的资产）：
    底盘 diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
    LoRA loras/pika_h3_flex_ref2va_v1_bf16.safetensors
         （Pika 2.0 运动参考 LoRA，50 个 block 全量对齐 ref2va 底盘）

参考图的走法与 ComfyUI 的 MiniMaxH3ReferenceToVideo 一致（ref_image_size="match"）：
    1) 等比缩到本次生成画布的面积（只缩不放，32 的倍数）后进 Qwen3-VL 视觉通道；
    2) 同一张图过视频 VAE 变成 ref latent，随每一步采样回灌到底盘。
prompt 里用 <Picture 1> 指代这张参考图。
"""

from __future__ import annotations

from .. import h3, media

TONE = {
    "id": "t2",
    "name": "Pika 参考生视频",
    "purpose": "给一张参考图，生成保持人物/场景一致、带同期声的短视频",
    "output": "video",
    "inputs": {"required": ["prompt", "image_in"], "optional": []},
    "controls": {
        "prompt": {"note": "用 <Picture 1> 指代参考图，例如「<Picture 1> 里的女孩转身微笑」"},
        "image_in": {
            "note": "单张参考图（绝对路径，可传 1 个元素的列表）；只取第一张",
            "limits": "会按本次画布面积等比缩小，不放大",
        },
        "resolution": {
            "default": "768x448",
            "presets": ["768x448", "16:9", "9:16", "1:1", "4:3", "3:4", "720p", "WxH"],
            "limits": "参考图 latent 参与每一步采样，单卡短视频对照用 768x448；"
                      "画布两轴 32 对齐，面积上限约 1.03 MP",
        },
        "duration": {
            "default": 5.0,
            "min": 1.0,
            "max": 15.0,
            "unit": "秒",
            "limits": "24 fps，帧数向上吸附到 17k+5 网格，5 秒对应 124 帧；"
                      "可用时长取决于画布和显存余量",
        },
        "seed": {"default": None, "limits": "None=随机（结果里回传实际种子）；整数=固定"},
    },
    "advanced": {
        "steps": {"default": 6, "min": 4, "max": 50},
        "cfg": {"default": 1.0, "min": 1.0, "max": 5.0},
        "sampler": {"default": "lcm", "choices": ["lcm", "res_multistep", "euler", "dpmpp_2m"]},
        "scheduler": {"default": "pika", "choices": ["pika", "simple", "beta", "normal"],
                      "note": "pika 使用原工作流的固定 sigma，需要 lcm 和 6 步"},
        "lora_strength": {"default": 1.0, "min": 0.0, "max": 1.5},
    },
    "models": {
        "diffusion": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae_video": "minimax_h3_video_vae_int8_convrot.safetensors",
        "vae_audio": "minimax_h3_audio_vae_fp32.safetensors",
        "lora": "pika_h3_flex_ref2va_v1_bf16.safetensors",
    },
    "reserve_vram_gb": 3.0,
    "notes": [
        "必须给 image_in；只要文字请换 t1。",
        "声音由模型联合生成，v0.1 不接受 audio_in。",
    ],
}


def play(run):
    rt = run.rt
    clip = run.clip()
    vae_video = run.vae("vae_video")

    if len(run.image_paths) > 1:
        run.meta["images_note"] = "t2 只取第一张参考图，其余 {} 张已忽略".format(len(run.image_paths) - 1)
    reference = media.load_image(rt, run.image_paths[0])

    # The v0.1 sampler used a reference latent cast to the model dtype.
    model = run.unet() if run.params["scheduler"] != "pika" else None
    cond, latent, frame_count = run.condition(
        h3.cond_ref_av, clip, vae_video, run.prompt, reference, run.width, run.height, run.frames,
        dtype=model.model_dtype() if model is not None else None,
    )
    if model is None:
        model = run.unet()
    samples = run.sample(run.with_lora(model), cond, latent)
    audio = run.decode_audio(run.vae("vae_audio"), samples)
    frames = run.decode_frames(vae_video, samples)

    if frames.shape[0] != frame_count:
        run.meta["frames_note"] = "VAE 解出 {} 帧，期望 {}".format(frames.shape[0], frame_count)
    return run.save_video(frames, audio)
