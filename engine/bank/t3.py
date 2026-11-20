"""t3 · H3 文生图 —— MiniMax-H3-Image 底盘，PDD 六头蒸馏，6 步出图。

配方来源（本机实测可跑的资产）：
    底盘 diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors
         （safetensors 元数据里 pdd_steps=6、pdd_sigmas=[1.0, 0.9675, 0.8875, 0.75, 0.5328, 0.2320, 0.0]）
    VAE  vae/minimax_h3_image_vae_fp16.safetensors

这张图底盘的 sigma 网格由 PDD 六头决定：comfy 的 ModelSampling 在 6 步时正好落在这条网格上，
所以步数被钉死在 6（工作流里也是 KSampler 6 步 / euler / simple / cfg 1.0）。
"""

from __future__ import annotations

from .. import h3

from .h3_contract import h3_tone

TONE = h3_tone({
    "id": "t3",
    "name": "H3 文生图",
    "purpose": "一句 prompt 出高质量静帧，默认 1024x1024",
    "output": "image",
    "inputs": {"required": ["prompt"], "optional": []},
    "controls": {
        "prompt": {"note": "自然语言描述画面；模型对细节长句的服从度比短词好"},
        "resolution": {
            "default": "1k",
            "presets": ["1k", "2k", "max", "16:9", "9:16", "1:1", "4:3", "3:4", "WxH"],
            "limits": "1k=1024x1024（单卡 16GB 实测可用）；2k=2048x2048（4.2 MP，需 ≥24GB 可用显存）；"
                      "max=3104x3104（约 9.6 MP，更吃显存）；两轴 32 的倍数",
        },
        "seed": {"default": None, "limits": "None=随机（结果里回传实际种子）；整数=固定"},
    },
    "advanced": {
        "steps": {"default": 6, "choices": [6], "note": "PDD 六头蒸馏，只支持 6 步"},
        "cfg": {"default": 1.0, "min": 1.0, "max": 2.0},
        "sampler": {"default": "euler", "choices": ["euler"]},
        "scheduler": {"default": "simple", "choices": ["simple"]},
    },
    "models": {
        "diffusion": "minimax_h3_image_pruned_int8_convrot.safetensors",
        "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae_image": "minimax_h3_image_vae_fp16.safetensors",
    },
    "notes": [
        "图像 Tone 不使用 duration，传了会直接报错。",
        "要改图请换 t4（带 image_in）。",
    ],
}, capability="text_to_image")


def play(run):
    cond, latent = run.condition(
        h3.cond_image, run.clip(), None, run.prompt, run.width, run.height,
    )
    samples = run.sample(run.unet(), cond, latent)
    image = run.decode_frames(run.vae("vae_image"), samples)
    return run.save_image(image[0])
