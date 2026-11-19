"""engine.h3 —— MiniMax-H3 的 AV 潜在构造、条件拼装与画面尺寸解析。

本文件的几何/条件逻辑移植自 ComfyUI 的 ``comfy_extras/nodes_minimax_h3.py``
（MiniMaxH3ImageToVideo / MiniMaxH3ReferenceToVideo / MiniMaxH3ImageConditioning
三个节点的 execute 主体，以及 _size_within_area / _size_short_edge / _image_refs /
_empty_av_latent / adapt_canvas 等辅助函数），只保留本机四个 Tone 实际走到的分支：

* t2va —— 纯文本（fl2va 的 T2V 用法）
* ref2va —— 单张参考图（图片先过 VAE 变成 ref block，同时进 Qwen 视觉通道）
* image —— H3-Image 的文生图
* image edit —— H3-Image 带 image_1 的编辑

上游：Comfy-Org/ComfyUI，GPL-3.0。移植改动记录见 engine/README.md。
"""

from __future__ import annotations

import math
import re

CANVAS_MULTIPLE = 32
BASE_SHORT_EDGE = 768
MAX_PIXELS = 768 * 1344          # 视频画布面积上限（约 1.03 MP）
FPS = 24
AUDIO_LATENT_FPS = 40

IMAGE_1K_EDGE = 1024              # 1k 方形边长，比例预设使用同等目标面积
IMAGE_SIZE = 2048                 # H3-Image 默认边长（面积约 4.2 MP）
IMAGE_MAX_EDGE = 3104             # H3-Image 实测可用上限（约 9.6 MP）
IMAGE_REF_SHORT_EDGE = 1024       # Qwen 看到的参考图短边
IMAGE_REF_VAE_MAX_SHORT_EDGE = 2048

VIDEO_ASPECTS = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "21:9": (21, 9),
}
VIDEO_ALIASES = {"720p": "16:9"}   # 768 短边≈720 级别；刻意不提供 480p/1080p，那会是假承诺
IMAGE_ASPECTS = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
}
_SIZE_RE = re.compile(r"^\s*(\d+)\s*[x*×]\s*(\d+)\s*$", re.IGNORECASE)


# --------------------------------------------------------------- 基础几何工具
def align_frame_count(n: int) -> int:
    """吸附到模型 17k+5 的帧网格。"""
    n = int(n)
    while n % 17 != 5:
        n += 1
    return n


def video_latent_t(frame_count: int) -> int:
    return 2 if frame_count <= 5 else ((frame_count - 5) // 17) * 5 + 2


def temporal_shape(length) -> tuple[int, int, int]:
    """秒数/帧数 -> (像素帧数, 视频潜帧数, 音频潜帧数)。"""
    frame_count = align_frame_count(max(5, int(length)))
    return frame_count, video_latent_t(frame_count), round(frame_count / FPS * AUDIO_LATENT_FPS)


def adapt_canvas(width: int, height: int) -> tuple[int, int]:
    """短边钉在 768，面积不超过 768×1344，两轴各自 round 到 32。"""
    ratio = width / height
    if ratio >= 1.0:
        nom_w, nom_h = BASE_SHORT_EDGE * ratio, BASE_SHORT_EDGE
    else:
        nom_w, nom_h = BASE_SHORT_EDGE, BASE_SHORT_EDGE / ratio
    if nom_w * nom_h > MAX_PIXELS:
        s = math.sqrt(MAX_PIXELS / (nom_w * nom_h))
        nom_w, nom_h = nom_w * s, nom_h * s
    return (
        max(CANVAS_MULTIPLE, round(nom_w / CANVAS_MULTIPLE) * CANVAS_MULTIPLE),
        max(CANVAS_MULTIPLE, round(nom_h / CANVAS_MULTIPLE) * CANVAS_MULTIPLE),
    )


def size_within_area(width: int, height: int, pixels: int, *, allow_upscale: bool = False) -> tuple[int, int]:
    """按比例缩放到目标面积，再对齐画布网格。默认只缩小。"""
    scale = math.sqrt(pixels / (width * height))
    if not allow_upscale:
        scale = min(1.0, scale)
    return tuple(
        max(CANVAS_MULTIPLE, round(side * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
        for side in (width, height)
    )


def size_short_edge(width: int, height: int, low: int, high: int) -> tuple[int, int]:
    scale = min(max(min(width, height), low), high) / min(width, height)
    return tuple(
        max(CANVAS_MULTIPLE, round(side * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
        for side in (width, height)
    )


def resolve_canvas(kind: str, value, image_size=None) -> tuple[int, int]:
    """把 resolution 解析成真实画布。kind: 'video' | 'image'。

    image_size 只在 kind='image' 且 value='match' 时使用（t4 跟随输入图）。
    """
    if value is None or value == "":
        value = "16:9" if kind == "video" else "2k"
    text = str(value).strip().lower()

    if kind == "image" and text == "match":
        if not image_size:
            raise ValueError("resolution='match' 需要一张输入图片")
        return size_short_edge(image_size[0], image_size[1], IMAGE_REF_SHORT_EDGE, IMAGE_REF_VAE_MAX_SHORT_EDGE)

    m = _SIZE_RE.match(text)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w % CANVAS_MULTIPLE or h % CANVAS_MULTIPLE:
            raise ValueError("画布宽高必须是 {} 的整数倍，收到 {}x{}".format(CANVAS_MULTIPLE, w, h))
        if w < CANVAS_MULTIPLE or h < CANVAS_MULTIPLE:
            raise ValueError("画布太小：{}x{}".format(w, h))
        if kind == "video":
            if w * h > MAX_PIXELS:
                raise ValueError(
                    "视频画布面积上限是 768x1344（约 1.03 MP），{}x{} = {:.2f} MP 超限；"
                    "H3 视频不会无条件承诺 1080p。".format(w, h, w * h / 1e6)
                )
            if min(w, h) < 256:
                raise ValueError("视频短边至少 256，收到 {}x{}".format(w, h))
        else:
            if w * h > IMAGE_MAX_EDGE * IMAGE_MAX_EDGE:
                raise ValueError(
                    "图片画布上限 {}x{}（约 {:.1f} MP），收到 {}x{}".format(
                        IMAGE_MAX_EDGE, IMAGE_MAX_EDGE, IMAGE_MAX_EDGE ** 2 / 1e6, w, h
                    )
                )
        return w, h

    if kind == "video":
        if text == "1080p":
            raise ValueError(
                "H3 视频画布短边固定 768、面积上限 768x1344；1080p 不在模型能力内，"
                "请用 720p / 16:9 或给出不超过 1.03 MP 的 WxH。"
            )
        aspect = VIDEO_ALIASES.get(text, text)
        if aspect not in VIDEO_ASPECTS:
            raise ValueError(
                "视频 resolution 支持 {} / 720p / WxH，收到 {!r}".format(
                    "/".join(VIDEO_ASPECTS), value
                )
            )
        aw, ah = VIDEO_ASPECTS[aspect]
        return adapt_canvas(aw, ah)

    if kind == "image":
        if text in ("1k", "2k", "max"):
            edge = {"1k": IMAGE_1K_EDGE, "2k": IMAGE_SIZE, "max": IMAGE_MAX_EDGE}[text]
            return edge, edge
        if text in IMAGE_ASPECTS:
            aw, ah = IMAGE_ASPECTS[text]
            return size_within_area(aw, ah, IMAGE_1K_EDGE ** 2, allow_upscale=True)
        raise ValueError(
            "图片 resolution 支持 1k / 2k / max / match / 16:9 / 9:16 / 1:1 / 4:3 / 3:4 / WxH，收到 {!r}".format(value)
        )


# --------------------------------------------------------------- 图像 -> 张量
def resize_image(rt, image, width: int, height: int, crop: str = "disabled"):
    """image [B,H,W,C] -> [B,height,width,3]；crop='disabled' 拉伸 / 'center' 居中裁切。"""
    samples = image[..., :3].movedim(-1, 1)
    samples = rt.utils.common_upscale(samples, width, height, "lanczos", crop)
    return samples.movedim(1, -1)


def resize_to(rt, image, size):
    if (image.shape[2], image.shape[1]) == size:
        return image[..., :3]
    return resize_image(rt, image, size[0], size[1], "disabled")


# --------------------------------------------------------------- 潜在变量构造
def av_latent(rt, width: int, height: int, latent_t: int, audio_t: int, batch_size: int = 1):
    torch = rt.torch
    device = rt.mm.intermediate_device()
    video = torch.zeros([batch_size, 24, latent_t, height // 16, width // 16], device=device)
    audio = torch.zeros([batch_size, 32, 2, audio_t], device=device)
    return {"samples": rt.nested.NestedTensor((video, audio))}


def empty_av_latent(rt, width: int, height: int, length: int, batch_size: int = 1):
    frame_count, latent_t, audio_t = temporal_shape(length)
    return av_latent(rt, width, height, latent_t, audio_t, batch_size), frame_count


def image_latent(rt, width: int, height: int, batch_size: int = 1):
    torch = rt.torch
    latent = torch.zeros(
        [batch_size, 24, 1, height // 16, width // 16],
        device=rt.mm.intermediate_device(),
    )
    return {"samples": latent}


# --------------------------------------------------------------- 条件拼装
def set_cond_values(conditioning, values):
    """等价于 node_helpers.conditioning_set_values。"""
    out = []
    for item in conditioning:
        entry = [item[0], item[1].copy()]
        entry[1].update(values)
        out.append(entry)
    return out


# ManualSigmas in the Pika T2V and REF2VA workflows.
PIKA_SIGMAS = (1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0)


def zero_out(conditioning):
    """等价于 ConditioningZeroOut：只清零主张量，保留附加字典（含 minimax_refs）。"""
    out = []
    for item in conditioning:
        values = item[1].copy()
        if values.get("pooled_output") is not None:
            values["pooled_output"] = values["pooled_output"] * 0
        out.append([item[0] * 0, values])
    return out


def cond_text_av(rt, clip, prompt: str, width: int, height: int, length: int):
    """t2va：纯文本 -> (conditioning, AV latent)。"""
    latent, frame_count = empty_av_latent(rt, width, height, length)
    tokens = clip.tokenize(prompt)
    cond = clip.encode_from_tokens_scheduled(tokens)
    return cond, latent, frame_count


def cond_ref_av(rt, clip, vae, prompt: str, image, width: int, height: int, length: int, dtype=None):
    """ref2va：图像只缩不放，默认保留原 VAE 精度。"""
    latent, frame_count = empty_av_latent(rt, width, height, length)

    h, w = image.shape[1], image.shape[2]
    scale = min(1.0, math.sqrt((width * height) / (w * h)))
    tw = max(CANVAS_MULTIPLE, round(w * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
    th = max(CANVAS_MULTIPLE, round(h * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
    resized = resize_image(rt, image[:1], tw, th, "disabled")

    ref_latent = vae.encode(resized)
    if dtype is not None:
        ref_latent = ref_latent.to(dtype=dtype)

    ref_items = [{"type": "image", "data": resized}]
    ref_blocks = [{
        "kind": "image",
        "latent_h": th // 16,
        "latent_w": tw // 16,
        "latent": ref_latent,
    }]

    tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)
    cond = clip.encode_from_tokens_scheduled(tokens)
    cond = set_cond_values(cond, {"minimax_refs": ref_blocks})
    return cond, latent, frame_count


def cond_image(rt, clip, vae, prompt: str, width: int, height: int, images=None):
    """H3-Image：文生图，或带 <Picture n> 参考图的编辑。

    images: [torch.Tensor(1,H,W,C), ...] 或 None
    """
    images = [img[:1] for img in (images or [])]

    def vision_size(w, h):
        return size_short_edge(w, h, IMAGE_REF_SHORT_EDGE, IMAGE_REF_SHORT_EDGE)

    def vae_size(w, h):
        return size_short_edge(w, h, IMAGE_REF_SHORT_EDGE, IMAGE_REF_VAE_MAX_SHORT_EDGE)

    vision_images, refs = [], []
    for image in images:
        size = (image.shape[2], image.shape[1])
        vision_images.append(resize_to(rt, image, vision_size(*size)))
        if vae is not None:
            z = vae.encode(resize_to(rt, image, vae_size(*size)))
            refs.append({
                "kind": "image",
                "latent_h": z.shape[-2],
                "latent_w": z.shape[-1],
                "latent": z,
            })

    cond = clip.encode_from_tokens_scheduled(
        clip.tokenize(prompt, images=vision_images, picture_label="Picture %d: ")
    )
    if refs:
        cond = set_cond_values(cond, {"minimax_refs": refs})
    latent = image_latent(rt, width, height)
    return cond, latent
