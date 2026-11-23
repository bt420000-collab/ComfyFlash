"""t4 · H3 图片编辑 —— 同一张 H3-Image 底盘，带 image_1 的参考编辑。

走法与 ComfyUI 的 MiniMaxH3ImageConditioning 完全一致：
    image_1 既是「要改的那张图」（Qwen 视觉通道，短边 1024），
    也过 Image-VAE 变成 ref latent（短边 1024–2048）随每一步回灌。
prompt 里用 <Picture 1> 指代它。

默认画布跟随输入图的比例与参考尺寸区间（resolution="match"），不保证逐像素等于原图；
如需指定模型画布，可传 WxH（两轴为 32 的倍数），例如 1376x768。
"""

from __future__ import annotations

from .. import h3, media

from .h3_contract import h3_tone

TONE = h3_tone({
    "id": "t4",
    "name": "H3 图片编辑",
    "purpose": "给一张图加一句指令，按原图结构改画面",
    "output": "image",
    "inputs": {"required": ["prompt", "image_in"], "optional": []},
    "prompt_format": {
        "required": True,
        "name": 'MiniMax H3-Image Edit',
        "guide": 'engine/skills/comfyflash/references/h3-image.md',
        "instruction": '必须按官方 H3-Image 编辑规则写成一个连续英文段落：源图/主体定义、目标画面与保留关系、具体修改；使用 <Picture 1>。',
    },
    "controls": {
        "prompt": {"note": "必须按 H3-Image Edit 指南写一个连续英文段落：源图定义、目标与保留关系、修改细节；使用 <Picture 1>。"},
        "image_in": {
            "max_items": 1,
            "min_items": 1,
            "note": "单张待编辑图（绝对路径或图像 Artifact，可传 1 个元素的列表）；超出数量会在生成前拒绝",
            "limits": "Qwen 视觉通道缩到短边 1024，VAE ref latent 缩到短边 1024–2048",
        },
        "resolution": {
            "default": "match",
            "presets": ["match", "1k", "2k", "max", "16:9", "9:16", "1:1", "4:3", "3:4", "WxH"],
            "limits": "match=保持输入图比例，将短边标准化到 1024–2048 并对齐 32 像素，不保证与输入像素尺寸相同；"
                      "如需指定画布可用 WxH（两轴均须为 32 的倍数）。1k=1024x1024（单卡 16GB 实测可用）；"
                      "2k / max 更吃显存",
        },
        "seed": {"default": None, "limits": "None=随机（结果里回传实际种子）；整数=固定"},
    },
    "advanced": {
        "steps": {"default": 6, "choices": [6], "note": "PDD 六头蒸馏，只支持 6 步"},
        "cfg": {"default": 1.0, "min": 1.0, "max": 2.0},
        "sampler": {"default": "euler", "choices": ["euler"]},
        "scheduler": {"default": "simple", "choices": ["simple"]},
        "prompt_prefix": {
            "default": "Edit <Picture 1>. ",
            "note": "prompt 里没有 <Picture 时自动加在最前面；写成空串即完全不动 prompt",
        },
    },
    "models": {
        "diffusion": "minimax_h3_image_pruned_int8_convrot.safetensors",
        "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae_image": "minimax_h3_image_vae_fp16.safetensors",
    },
    "notes": [
        "必须给 image_in；纯文字出图请换 t3。",
        "图像 Tone 不使用 duration，传了会直接报错。",
    ],
}, capability="image_edit", image_role="edit_source")


def play(run):
    rt = run.rt
    vae_image = run.vae("vae_image")

    prompt = run.prompt
    if "<Picture" not in prompt:
        prompt = str(run.params.get("prompt_prefix") or "") + prompt
    run.prompt = prompt

    images = [media.load_image(rt, run.image_paths[0])]

    cond, latent = run.condition(
        h3.cond_image, run.clip(), vae_image, prompt, run.width, run.height, images=images,
    )
    samples = run.sample(run.unet(), cond, latent)
    image = run.decode_frames(vae_image, samples)
    return run.save_image(image[0])
