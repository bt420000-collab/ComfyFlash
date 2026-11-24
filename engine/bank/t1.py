"""t1 · Pika 文生视频 —— H3 fl2va 底盘 + Pika Flex 运动 LoRA，纯文本起手。

配方来源（本机实测可跑的资产）：
    底盘 diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
    LoRA loras/pika_h3_flex_fl2va_v1_bf16.safetensors
         （Pika 2.0 运动/镜头参考 LoRA，baked_scale=1.0，50 个 block 全量对齐底盘）

采样沿用 Pika 工作流的六步 LCM、固定 sigma、cfg 1.0。
H3 是音视频联合模型，画面和 32 kHz 同期声一起生成，所以产物是带声音的 mp4。
"""

from __future__ import annotations

from .. import h3

from .h3_contract import h3_tone

TONE = h3_tone({
    "id": "t1",
    "name": "Pika 文生视频",
    "purpose": "一句 prompt 生成带同期声的短视频，Pika Flex 的运动与镜头质感",
    "output": "video",
    "inputs": {"required": ["prompt"], "optional": []},
    "prompt_format": {
        "required": True,
        "name": 'MiniMax H3 T2VA',
        "instruction": '必须改写为官方 T2VA 三段结构：integrated_multimodal_description、overall_soundscape、non_diegetic_music；不可默认直接传入原始需求或普通翻译。',
    },
    "controls": {
        "prompt": {"note": "必须按 H3 T2VA 三段专用格式编写：integrated_multimodal_description / overall_soundscape / non_diegetic_music；画面与声音描述用英文，对白保留原文。"},
        "resolution": {
            "default": "16:9",
            "presets": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "720p", "WxH"],
            "limits": "短边固定 768，面积上限 768x1344（约 1.03 MP），两轴 32 的倍数；"
                      "720p 等价于 16:9（1344x768），不是标准 720p 画布；1080p 超出模型能力，会被明确拒绝",
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
        "diffusion": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae_video": "minimax_h3_video_vae_int8_convrot.safetensors",
        "vae_audio": "minimax_h3_audio_vae_fp32.safetensors",
        "lora": "pika_h3_flex_fl2va_v1_bf16.safetensors",
    },
    "reserve_vram_gb": 3.0,
    "notes": [
        "只吃 prompt；需要参考图请换 t2。",
        "声音由模型联合生成，v0.1 不接受 audio_in。",
    ],
}, capability="text_to_video")


def play(run):
    cond, latent, frame_count = run.condition(
        h3.cond_text_av, run.clip(), run.prompt, run.width, run.height, run.frames,
    )
    samples = run.sample(run.with_lora(run.unet()), cond, latent)
    audio = run.decode_audio(run.vae("vae_audio"), samples)
    frames = run.decode_frames(run.vae("vae_video"), samples)

    if frames.shape[0] != frame_count:
        run.meta["frames_note"] = "VAE 解出 {} 帧，期望 {}".format(frames.shape[0], frame_count)
    return run.save_video(frames, audio)
