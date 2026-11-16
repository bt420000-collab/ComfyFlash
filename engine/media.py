"""engine.media —— 只做"把人给的文件读进来、把结果写出去"，不含任何生成逻辑。

图片走 Pillow，视频走 PyAV（H.264 + AAC）。
中间不落任何临时文件；若容器封装需要缓冲，只在内存里做。
"""

from __future__ import annotations

from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv"}


def load_image(rt, path: str):
    """读一张图 -> [1,H,W,3] float32 0..1（已按 EXIF 转正）。"""
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        arr = np.asarray(img, dtype="float32") / 255.0
    return rt.torch.from_numpy(arr)[None, ...]


def image_size(path: str):
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        w, h = ImageOps.exif_transpose(img).size
    return w, h


def save_image(tensor, path: str) -> str:
    """[H,W,C] 或 [1,H,W,C] 0..1 -> 磁盘图片。"""
    import numpy as np
    from PIL import Image

    arr = tensor
    if getattr(arr, "dim", lambda: 0)() == 4:
        arr = arr[0]
    arr = arr.detach().float().clamp(0, 1).cpu().numpy()
    arr = (arr * 255.0).round().astype("uint8")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(str(out))
    return str(out)


def save_video(frames, path: str, fps: int = 24, audio=None) -> str:
    """frames [T,H,W,C] 0..1 + audio {'waveform':[B,C,L], 'sample_rate':int} -> mp4/mov/mkv。"""
    import av
    import numpy as np

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    total = int(frames.shape[0])
    height = int(frames.shape[1])
    width = int(frames.shape[2])

    with av.open(str(out), mode="w") as container:
        vstream = container.add_stream("libx264", rate=fps)
        vstream.width = width
        vstream.height = height
        vstream.pix_fmt = "yuv420p"
        vstream.options = {"crf": "17", "preset": "medium"}

        astream = None
        if audio is not None and audio.get("waveform") is not None:
            sr = int(audio.get("sample_rate") or 32000)
            wav = audio["waveform"]
            wav = wav.detach().float().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav, "float32")
            wav = wav[0] if wav.ndim == 3 else wav
            if wav.shape[0] == 1:
                wav = np.repeat(wav, 2, axis=0)
            wav = wav[:2]
            limit = int(round(total / fps * sr))
            if wav.shape[1] > limit:
                wav = wav[:, :limit]
            else:
                wav = np.pad(wav, ((0, 0), (0, limit - wav.shape[1])))

            astream = container.add_stream("aac", rate=sr)
            astream.layout = "stereo"

            pcm = (np.clip(wav.T, -1.0, 1.0) * 32767.0).astype("<i2")
            for start in range(0, pcm.shape[0], 1024):
                block = np.ascontiguousarray(pcm[start:start + 1024])
                if block.shape[0] == 0:
                    continue
                aframe = av.AudioFrame(format="s16", layout="stereo", samples=block.shape[0])
                aframe.sample_rate = sr
                aframe.pts = start
                aframe.planes[0].update(block.tobytes())
                for packet in astream.encode(aframe):
                    container.mux(packet)
            for packet in astream.encode():
                container.mux(packet)

        for i in range(total):
            arr = frames[i].detach().float().clamp(0, 1).cpu().numpy()
            arr = (arr * 255.0).round().astype("uint8")
            vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for packet in vstream.encode(vframe):
                container.mux(packet)
        for packet in vstream.encode():
            container.mux(packet)

    return str(out)
