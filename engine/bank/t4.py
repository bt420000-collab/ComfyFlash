"""t4 · H3 图片编辑 —— 同一张 H3-Image 底盘，带 image_1 的参考编辑。

走法与 ComfyUI 的 MiniMaxH3ImageConditioning 完全一致：
    image_1 既是「要改的那张图」（Qwen 视觉通道，短边 1024），
    也过 Image-VAE 变成 ref latent（短边 1024–2048）随每一步回灌。
prompt 里用 <Picture 1> 指代它。

默认画布跟随输入图（resolution="match"），也可显式给 1k/2k/max/比例/WxH。
"""

from __future__ import annotations

from .. import h3, media

TONE = {
    "id": "t4",
    "name": "H3 图片编辑",
    "purpose": "给一张图加一句指令，按原图结构改画面",
    "output": "image",
    "inputs": {"required": ["prompt", "image_in"], "optional": []},
    "controls": {
        "prompt": {"note": "用 <Picture 1> 指代原图，例如「把 <Picture 1> 的背景换成雪山黄昏」"},
        "image_in": {
            "note": "单张待编辑图（绝对路径，可传 1 个元素的列表）；只取第一张",
            "limits": "Qwen 视觉通道缩到短边 1024，VAE ref latent 缩到短边 1024–2048",
        },
        "resolution": {
            "default": "match",
            "presets": ["match", "1k", "2k", "max", "16:9", "9:16", "1:1", "4:3", "3:4", "WxH"],
            "limits": "match=跟随输入图尺寸（短边 1024–2048）；1k=1024x1024（单卡 16GB 实测可用）；"
                      "2k / max 更吃显存；两轴 32 的倍数",
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
}


def play(run):
    rt = run.rt
    vae_image = run.vae("vae_image")

    prompt = run.prompt
    if "<Picture" not in prompt:
        prompt = str(run.params.get("prompt_prefix") or "") + prompt

    if len(run.image_paths) > 1:
        run.meta["images_note"] = "t4 只编辑第一张图，其余 {} 张已忽略".format(len(run.image_paths) - 1)
    images = [media.load_image(rt, run.image_paths[0])]

    cond, latent = run.condition(
        h3.cond_image, run.clip(), vae_image, prompt, run.width, run.height, images=images,
    )
    samples = run.sample(run.unet(), cond, latent)
    image = run.decode_frames(vae_image, samples)
    return run.save_image(image[0])
