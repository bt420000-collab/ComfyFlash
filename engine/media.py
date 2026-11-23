"""Shared Pillow and PyAV encoders for standalone media and Artifact delivery."""

from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import tempfile


_IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP"}
IMAGE_EXT = set(_IMAGE_FORMATS)
VIDEO_EXT = {".mp4", ".mov", ".mkv"}
_SHEET_CELL_WIDTH = 384
_SHEET_IMAGE_HEIGHT = 240


class MediaWriteError(OSError):
    """媒体编码、验证或安全提交失败。"""


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
        img.verify()
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).size


def inspection_paths(path: str | Path) -> dict[str, str]:
    """Return video inspection filenames beside the video."""
    out = Path(path)
    return {
        "contact_sheet": str(out.with_name(out.name + ".contact.jpg")),
        "last_frame": str(out.with_name(out.name + ".last.png")),
    }


def _temporary_path(out: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{out.stem}.", suffix=out.suffix, dir=out.parent)
    os.close(fd)
    return Path(name)


def _install_staged(source: Path, target: Path, *, overwrite: bool) -> None:
    if overwrite:
        os.replace(source, target)
    elif os.name == "nt":
        os.rename(source, target)  # Windows rename fails if the target already exists.
    else:
        os.link(source, target)  # POSIX rename would replace an existing target.


@contextmanager
def _atomic_output(out: Path, *, overwrite: bool):
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = _temporary_path(out)
    try:
        yield stage
        try:
            _install_staged(stage, out, overwrite=overwrite)
        except OSError as exc:
            if overwrite:
                raise
            if isinstance(exc, FileExistsError) or os.path.lexists(out):
                raise FileExistsError(f"输出文件已存在；如需替换请显式设置 overwrite=True：{out}") from exc
            raise MediaWriteError(f"无法安全原子提交输出文件：{out}；{exc}") from exc
    finally:
        stage.unlink(missing_ok=True)


def _sample_array(value, *, finite=True):
    import numpy as np

    if hasattr(value, "detach"):
        value = value.detach().float().cpu().numpy()
    array = np.asarray(value, dtype=np.float32)
    if finite and not np.isfinite(array).all():
        raise ValueError("Generated samples contain NaN or Inf")
    return array


def _pixels8(array):
    import numpy as np

    return (np.clip(array, 0, 1) * 255.0).round().astype("uint8")


def _rgb8(frame):
    array = _sample_array(frame)
    if array.ndim != 3 or array.shape[2] != 3 or min(array.shape[:2]) < 1:
        raise ValueError(f"Expected a nonempty RGB frame, received {array.shape}")
    return _pixels8(array)


def _encode_image(tensor, path: Path, *, strict=True) -> None:
    from PIL import Image

    suffix = path.suffix.lower()
    if suffix not in _IMAGE_FORMATS:
        raise MediaWriteError(f"不支持的图片扩展名：{suffix}")
    if len(tensor.shape) == 4:
        if strict and tensor.shape[0] != 1:
            raise ValueError(f"Expected one generated image, received {tensor.shape[0]}")
        tensor = tensor[0]
    pixels = _rgb8(tensor) if strict else _pixels8(_sample_array(tensor, finite=False))
    image = Image.fromarray(pixels)
    image_format = _IMAGE_FORMATS[suffix]
    if image_format == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")
    options = {"quality": 92} if image_format in {"JPEG", "WEBP"} else {}
    image.save(path, format=image_format, **options)


def _verify_image(path: Path) -> None:
    from PIL import Image

    with Image.open(path) as check:
        check.verify()


def save_image(tensor, path: str | Path, *, overwrite: bool = False) -> str:
    """Encode an image and publish it atomically after verification."""
    out = Path(path)
    with _atomic_output(out, overwrite=overwrite) as stage:
        _encode_image(tensor, stage, strict=False)
        _verify_image(stage)
    return str(out)


def _contact_thumbnail(image):
    from PIL import Image

    scale = min(_SHEET_CELL_WIDTH / image.width, _SHEET_IMAGE_HEIGHT / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _write_contact_sheet(samples, count, path):
    from PIL import Image, ImageDraw

    cell_w, image_h, label_h, pad = _SHEET_CELL_WIDTH, _SHEET_IMAGE_HEIGHT, 28, 12
    cols = min(3, len(samples))
    rows = math.ceil(len(samples) / cols)
    sheet = Image.new("RGB", (pad + cols * (cell_w + pad), pad + rows * (image_h + label_h + pad)), (24, 26, 30))
    draw = ImageDraw.Draw(sheet)
    for position, (frame_index, timestamp, thumb) in enumerate(samples):
        col, row = position % cols, position // cols
        x = pad + col * (cell_w + pad)
        y = pad + row * (image_h + label_h + pad)
        draw.text((x, y + 5), f"Frame {frame_index + 1}/{count}  |  {timestamp:.2f}s", fill=(238, 240, 244))
        px = x + (cell_w - thumb.width) // 2
        py = y + label_h + (image_h - thumb.height) // 2
        sheet.paste(thumb, (px, py))
    sheet.save(path, format="JPEG", quality=90)


def save_contact_sheet(frames, path: str | Path, *, fps: int = 24, max_frames: int = 6,
                       overwrite: bool = False) -> str:
    """Publish a timestamped JPEG contact sheet of the requested video frames."""
    import numpy as np
    from PIL import Image

    if max_frames < 1:
        raise ValueError("max_frames 必须大于 0")
    if len(frames.shape) != 4 or frames.shape[0] < 1:
        raise ValueError("Contact Sheet 需要非空 [T,H,W,C] 帧张量")
    count = int(frames.shape[0])
    indices = np.linspace(0, count - 1, min(count, max_frames), dtype=int).tolist()
    samples = []
    for index in indices:
        pixels = _pixels8(_sample_array(frames[index][..., :3], finite=False))
        samples.append((index, index / fps, _contact_thumbnail(Image.fromarray(pixels))))
    out = Path(path)
    with _atomic_output(out, overwrite=overwrite) as stage:
        _write_contact_sheet(samples, count, stage)
        _verify_image(stage)
    return str(out)


def _video_probe_details(stream, audio_stream_count):
    rate = float(stream.average_rate) if stream.average_rate else None
    duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else None
    return {
        "video_stream": True,
        "first_frame_decodable": True,
        "width": int(stream.width),
        "height": int(stream.height),
        "fps": rate,
        "duration_s": round(duration, 3) if duration is not None else None,
        "audio_present": bool(audio_stream_count),
        "audio_stream_count": audio_stream_count,
    }


def probe_video(path: str | Path, *, require_audio: bool = False) -> dict:
    """Reopen a video and check its streams and first decodable frame."""
    import av

    with av.open(str(path), mode="r") as container:
        if not container.streams.video:
            raise MediaWriteError(f"视频文件没有视频流：{path}")
        stream = container.streams.video[0]
        if stream.width <= 0 or stream.height <= 0:
            raise MediaWriteError(f"视频尺寸无效：{path}")
        if next(container.decode(video=0), None) is None:
            raise MediaWriteError(f"视频无法解码出首帧：{path}")
        audio_stream_count = len(container.streams.audio)
        if require_audio and not audio_stream_count:
            raise MediaWriteError(f"生成请求包含音频，但封装结果没有音轨：{path}")
        return {"status": "passed", **_video_probe_details(stream, audio_stream_count)}


def audio_summary(audio, frame_count: int, fps: int) -> dict:
    if audio is None or audio.get("waveform") is None:
        return {"present": False}
    waveform = audio["waveform"]
    shape = waveform.shape if hasattr(waveform, "shape") else _sample_array(waveform, finite=False).shape
    if len(shape) == 3:
        shape = shape[1:]
    channels = int(shape[0]) if len(shape) >= 2 else 1
    sample_count = int(shape[-1]) if len(shape) else 0
    sr = int(audio.get("sample_rate") or 32000)
    return {
        "present": True,
        "sample_rate": sr,
        "channels": channels,
        "duration_s": round(sample_count / sr, 3) if sr else None,
        "expected_video_duration_s": round(frame_count / fps, 3),
    }


def _audio_for_encode(audio, total, fps):
    import numpy as np

    sr = int(audio.get("sample_rate") or 32000)
    if sr <= 0:
        raise ValueError(f"Invalid audio sample rate {sr}")
    wav = _sample_array(audio["waveform"])
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


def _encode_video(frames, path: Path, fps: int = 24, audio=None, *, on_frame=None, on_audio=None) -> None:
    """Encode to a staging path and pass the encoder inputs to inspection callbacks."""
    import av
    import numpy as np

    if len(frames.shape) != 4 or min(frames.shape[:3]) < 1 or frames.shape[3] != 3 or fps <= 0:
        raise ValueError(f"Invalid video dimensions/frame rate: {frames.shape}, {fps} fps")
    total, height, width, _ = map(int, frames.shape)
    with av.open(str(path), mode="w") as container:
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
            pixels = _rgb8(frames[i])
            if on_frame:
                on_frame(i, pixels)
            vframe = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in vstream.encode(vframe):
                container.mux(packet)
        for packet in vstream.encode():
            container.mux(packet)


def save_video(frames, path: str | Path, fps: int = 24, audio=None, *, overwrite: bool = False,
               return_validation: bool = False, on_frame=None, on_audio=None):
    """Encode a video and publish it atomically after checking the streams."""
    out = Path(path)
    with _atomic_output(out, overwrite=overwrite) as stage:
        _encode_video(frames, stage, fps, audio, on_frame=on_frame, on_audio=on_audio)
        validation = probe_video(stage, require_audio=audio is not None and audio.get("waveform") is not None)
        if (validation["height"], validation["width"]) != tuple(frames.shape[1:3]):
            raise MediaWriteError(f"封装后视频尺寸与输入帧不一致：{validation}")
    return (str(out), validation) if return_validation else str(out)
