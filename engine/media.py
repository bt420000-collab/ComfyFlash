"""Pillow/PyAV image and H.264/AAC codec primitives used by Artifact delivery."""
from __future__ import annotations

from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv"}


def load_image(rt, path: str):
    """Read an EXIF-oriented image into a [1,H,W,3] float32 tensor."""
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        arr = np.asarray(img, dtype="float32") / 255.0
    return rt.torch.from_numpy(arr)[None, ...]


def image_size(path: str):
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).size


def _finite_array(value):
    import numpy as np

    if hasattr(value, "detach"):
        value = value.detach().float().cpu().numpy()
    arr = np.asarray(value, dtype=np.float32)
    if not np.isfinite(arr).all():
        raise ValueError("Generated samples contain NaN or Inf")
    return arr


def _rgb8(frame):
    import numpy as np

    arr = _finite_array(frame)
    if arr.ndim != 3 or arr.shape[2] != 3 or min(arr.shape[:2]) < 1:
        raise ValueError(f"Expected a nonempty RGB frame, received {arr.shape}")
    return (np.clip(arr, 0, 1) * 255.0).round().astype("uint8")


def save_image(tensor, path: str) -> str:
    """Encode one RGB tensor [H,W,3] or [1,H,W,3]."""
    from PIL import Image

    if len(tensor.shape) == 4:
        if tensor.shape[0] != 1:
            raise ValueError(f"Expected one generated image, received {tensor.shape[0]}")
        tensor = tensor[0]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(_rgb8(tensor)).save(str(out))
    return str(out)


def _audio_for_encode(audio, total, fps):
    import numpy as np

    sr = int(audio.get("sample_rate") or 32000)
    if sr <= 0:
        raise ValueError(f"Invalid audio sample rate {sr}")
    wav = _finite_array(audio["waveform"])
    if wav.ndim == 3:
        wav = wav[0]
    if wav.ndim != 2 or min(wav.shape) == 0:
        raise ValueError(f"Expected nonempty [channels,samples] waveform, received {wav.shape}")
    if wav.shape[0] == 1:
        wav = np.repeat(wav, 2, axis=0)
    limit = int(round(total / fps * sr))
    wav = wav[:2, :limit]
    if wav.shape[1] < limit:
        wav = np.pad(wav, ((0, 0), (0, limit - wav.shape[1])))
    return wav, sr


def save_video(frames, path: str, fps: int = 24, audio=None, *, on_frame=None, on_audio=None) -> str:
    """Encode [T,H,W,3]; optional inspection callbacks see the exact encoder inputs."""
    import av
    import numpy as np

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(frames.shape) != 4 or min(frames.shape[:3]) < 1 or frames.shape[3] != 3 or fps <= 0:
        raise ValueError(f"Invalid video dimensions/frame rate: {frames.shape}, {fps} fps")
    total, height, width, _ = map(int, frames.shape)

    with av.open(str(out), mode="w") as container:
        vstream = container.add_stream("libx264", rate=fps)
        vstream.width = width
        vstream.height = height
        vstream.pix_fmt = "yuv420p"
        vstream.options = {"crf": "17", "preset": "medium"}

        if audio is not None and audio.get("waveform") is not None:
            wav, sr = _audio_for_encode(audio, total, fps)
            if on_audio:
                on_audio(wav, sr)
            astream = container.add_stream("aac", rate=sr)
            astream.layout = "stereo"
            pcm = (np.clip(wav.T, -1.0, 1.0) * 32767.0).astype("<i2")
            for start in range(0, pcm.shape[0], 1024):
                block = np.ascontiguousarray(pcm[start:start + 1024])
                aframe = av.AudioFrame(format="s16", layout="stereo", samples=block.shape[0])
                aframe.sample_rate = sr
                aframe.pts = start
                aframe.planes[0].update(block.tobytes())
                for packet in astream.encode(aframe):
                    container.mux(packet)
            for packet in astream.encode():
                container.mux(packet)

        for i in range(total):
            arr = _rgb8(frames[i])
            if on_frame:
                on_frame(i, arr)
            vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for packet in vstream.encode(vframe):
                container.mux(packet)
        for packet in vstream.encode():
            container.mux(packet)
    return str(out)
