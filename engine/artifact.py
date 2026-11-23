"""Validate, inspect and publish locally generated image/video Artifacts."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
from time import perf_counter

from . import media


class DeliveryError(Exception):
    def __init__(self, message, *, code="DELIVERY_ERROR", recoverable_paths=None):
        super().__init__(message)
        self.code = code
        self.validation = {"status": "hard_error", "errors": [message]}
        self.recoverable_paths = {k: str(v) for k, v in (recoverable_paths or {}).items()}


def output_paths(path, media_type):
    path = Path(path)
    path = path.parent.resolve() / path.name
    result = {"media": path}
    if media_type == "video":
        result.update({key: Path(value) for key, value in media.inspection_paths(path).items()})
        result["audio_report"] = Path(f"{path}.audio.json")
    result["metadata"] = Path(f"{path}.meta.json")
    return result


@contextmanager
def _publish_lock(directory):
    """Serialize the short multi-file commit across writers, including overlapping bundles."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import hashlib

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        digest = hashlib.sha256(os.path.normcase(str(directory)).encode()).hexdigest()
        handle = kernel.CreateMutexW(None, False, "Local\\ComfyFlashPublish-" + digest)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # WAIT_ABANDONED also grants ownership after a publisher exits.
            if kernel.WaitForSingleObject(handle, 0xffffffff) not in (0, 0x80):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                kernel.ReleaseMutex(handle)
        finally:
            kernel.CloseHandle(handle)
    else:
        import fcntl
        fd = os.open(directory, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _check_destinations(paths, overwrite, stage=None):
    for path in paths.values():
        if os.path.lexists(path):
            if not overwrite:
                raise DeliveryError(f"Output already exists: {path}", code="OUTPUT_EXISTS",
                                    recoverable_paths=stage)
            if path.is_symlink() or not path.is_file():
                raise DeliveryError(f"Cannot replace a non-regular output: {path}", recoverable_paths=stage)


def _publish(paths, stage, overwrite):
    with _publish_lock(paths["media"].parent):
        _check_destinations(paths, overwrite, stage)
        backups = {}
        installed = []
        metadata = paths["metadata"]
        try:
            # The metadata is the completion marker. Hide the old marker throughout
            # an overwrite and install the new marker only after every other member.
            if overwrite and metadata.exists():
                backup = media._temporary_path(metadata)
                try:
                    os.replace(metadata, backup)
                except OSError:
                    backup.unlink()
                    raise
                backups["metadata"] = backup
            for key, path in paths.items():
                if overwrite and path.exists():
                    backup = media._temporary_path(path)
                    backup.unlink()
                    try:
                        os.link(path, backup)
                    except OSError:
                        try:
                            shutil.copy2(path, backup)
                        except OSError:
                            backup.unlink(missing_ok=True)
                            raise
                    backups[key] = backup
                media._install_staged(stage[key], path, overwrite=overwrite)
                installed.append(key)
        except OSError as exc:
            recovery = {}
            rollback_errors = []
            for key in reversed(installed):
                try:
                    if stage[key].exists():  # POSIX no-replace retained our staging link.
                        paths[key].unlink()
                    else:
                        os.replace(paths[key], stage[key])
                    if key in backups:
                        os.replace(backups[key], paths[key])
                except OSError as undo:
                    rollback_errors.append(str(undo))
                    if paths[key].exists():
                        recovery[f"published_{key}"] = paths[key]
            if "metadata" in backups and not rollback_errors:
                try:
                    os.replace(backups["metadata"], metadata)
                except OSError as undo:
                    rollback_errors.append(str(undo))
            for key, backup in backups.items():
                if backup.exists():
                    if rollback_errors:
                        recovery[f"previous_{key}"] = backup
                    else:
                        backup.unlink()
            recovery.update({k: p for k, p in stage.items() if p.exists()})
            detail = f"Could not publish {paths['media']}: {exc}"
            if rollback_errors:
                detail += ". Restore errors: " + ", ".join(rollback_errors)
            code = "OUTPUT_EXISTS" if isinstance(exc, FileExistsError) else "DELIVERY_ERROR"
            raise DeliveryError(detail, code=code, recoverable_paths=recovery) from exc
        for backup in backups.values():
            backup.unlink(missing_ok=True)


@contextmanager
def _staging(paths, overwrite):
    stage = {}
    preserve = set()
    try:
        _check_destinations(paths, overwrite)
        paths["media"].parent.mkdir(parents=True, exist_ok=True)
        for name, path in paths.items():
            stage[name] = media._temporary_path(path)
        yield stage
        _publish(paths, stage, overwrite)
    except DeliveryError as exc:
        preserve.update(exc.recoverable_paths.values())
        raise
    except Exception as exc:
        raise DeliveryError(f"Could not deliver {paths['media']}: {exc}") from exc
    finally:
        for path in stage.values():
            if str(path) not in preserve:
                path.unlink(missing_ok=True)


def _write_json(path, value):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _base(base_artifact, paths, kind):
    return {**base_artifact, "schema_version": base_artifact.get("schema_version", "0.2"),
            "type": kind, "media_type": kind, "path": str(paths["media"]),
            "inspection": {k: str(p) for k, p in paths.items() if k != "media"}}


def _read_image_size(path):
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        return image.size


def _validation(warnings, **checks):
    return {"status": "warning" if warnings else "passed",
            "warnings": list(dict.fromkeys(warnings)), **checks}


def deliver_image(tensor, path, base_artifact, *, overwrite=False):
    paths = output_paths(path, "image")
    with _staging(paths, overwrite) as stage:
        started = perf_counter()
        media._encode_image(tensor, stage["media"])
        encoding_s = perf_counter() - started
        width, height = _read_image_size(stage["media"])
        if (height, width) != tuple(tensor.shape[-3:-1]) or min(width, height) <= 0:
            raise ValueError(f"Encoded dimensions disagree with generated image: {width}x{height}")
        result = _base(base_artifact, paths, "image")
        result.update(width=width, height=height,
                      timing_s={**base_artifact.get("timing_s", {}), "encoding": encoding_s},
                      validation=_validation([], bytes=stage["media"].stat().st_size))
        _write_json(stage["metadata"], result)
    return result


def _audio_stats(wav, rate):
    import numpy as np

    peak = float(np.max(np.abs(wav)))
    rms = float(np.sqrt(np.mean(np.square(wav, dtype=np.float64))))
    clipped = int(np.count_nonzero(np.abs(wav) >= 1.0))
    return {"analysis_source": "encoder_input_before_clipping", "analyzed_duration_s": wav.shape[1] / rate,
            "sample_count_per_channel": wav.shape[1], "rms": rms, "peak": peak, "silence": rms <= 0.0001,
            "clipping": bool(clipped), "clipped_sample_count": clipped}


def _probe_video(path, count, width, height, fps, audio_required):
    """Check encoded integrity in one streaming pass without extracting preview pixels."""
    import av

    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("Video stream is missing")
        video = container.streams.video[0]
        if video.average_rate is None or not math.isclose(float(video.average_rate), fps):
            raise ValueError(f"Encoded video frame rate differs from expected {fps}")
        decoder = video.codec_context
        decoder.options = {**decoder.options, "err_detect": "explode"}
        decoder.thread_count = 2
        decoder.thread_type = "AUTO"
        audio = next(iter(container.streams.audio), None)
        if audio is not None:
            audio.codec_context.options = {**audio.codec_context.options, "err_detect": "explode"}
        packets = 0
        decoded = 0
        audio_packets = 0
        audio_frames = 0
        audio_end = None
        for packet in container.demux():
            if packet.is_corrupt:
                raise ValueError(f"Damaged {packet.stream.type} packet")
            if packet.stream.index == video.index:
                if packet.size:
                    packets += 1  # This writer emits one H.264 access unit per packet.
                # PyAV's final empty packet flushes frames delayed by reordering.
                for frame in packet.decode():
                    if frame.is_corrupt or (frame.width, frame.height) != (width, height):
                        raise ValueError(f"Damaged video frame or unexpected dimensions at frame {decoded}")
                    decoded += 1
            elif audio is not None and packet.stream.index == audio.index:
                if packet.size:
                    audio_packets += 1
                    if packet.pts is not None:
                        end = float((packet.pts + (packet.duration or 0)) * packet.time_base)
                        audio_end = max(audio_end or 0, end)
                for frame in packet.decode():
                    if frame.is_corrupt or frame.samples < 1:
                        raise ValueError(f"Damaged or empty audio frame at frame {audio_frames}")
                    audio_frames += 1
        if packets != count or video.frames not in (0, count):
            raise ValueError(f"Expected {count} video frames, found {packets} packets / {video.frames} declared frames")
        if decoded != count:
            raise ValueError(f"Expected {count} decodable video frames, decoded {decoded}")
        if audio is not None and not audio_frames:
            raise ValueError("Audio stream contains no decodable frames")
        if audio_required and audio is None:
            raise ValueError("Required audio stream is missing")
        report = {"present": audio is not None}
        if audio is not None:
            codec = audio.codec_context
            declared = float(audio.duration * audio.time_base) if audio.duration is not None else None
            report.update(codec=codec.name, sample_rate=codec.sample_rate, channels=codec.channels,
                          duration_s=declared if declared is not None else audio_end,
                          duration_source="stream" if declared is not None else "packet_end_pts",
                          packet_count=audio_packets, checked_decode_frame_count=audio_frames)
        return {**media._video_probe_details(video, len(container.streams.audio)),
                "bytes": path.stat().st_size, "video_packet_count": packets,
                "declared_frame_count": video.frames, "checked_decode_frame_count": decoded,
                "video_codec": decoder.name}, report


class _VideoInspection:
    def __init__(self, count, fps, requested, stage):
        if type(requested) is not int or requested <= 0 or count < 1:
            raise ValueError("Video and inspection frame counts must be positive integers")
        samples = min(requested, count)
        self.indices = {round(i * (count - 1) / max(1, samples - 1)) for i in range(samples)}
        self.samples = []
        self.warnings = []
        self.thumbnails = []
        self.stage = stage
        self.last = count - 1
        self.fps = fps

    def accept(self, index, pixels):
        from PIL import Image

        if index != self.last and index not in self.indices:
            return
        image = Image.fromarray(pixels)
        if index == self.last:
            image.save(self.stage["last_frame"])
        if index in self.indices:
            extrema = image.getextrema()
            if max(high for low, high in extrema) <= 4:
                self.warnings.append("black_frame")
            elif all(high - low <= 3 for low, high in extrema):
                self.warnings.append("solid_color_frame")
            self.thumbnails.append(media._contact_thumbnail(image))
            self.samples.append({"frame_index": index, "timestamp_s": index / self.fps})

    def save_sheet(self):
        thumbnails = [(sample["frame_index"], sample["timestamp_s"], thumb)
                      for thumb, sample in zip(self.thumbnails, self.samples)]
        media._write_contact_sheet(thumbnails, self.last + 1, self.stage["contact_sheet"])


def deliver_video(frames, path, base_artifact, *, fps, audio=None, audio_required=False,
                  inspection_frames=6, overwrite=False):
    paths = output_paths(path, "video")
    with _staging(paths, overwrite) as stage:
        count, height, width, _ = map(int, frames.shape)
        inspection = _VideoInspection(count, fps, inspection_frames, stage)
        stats = {}

        def inspect_audio(wav, rate):
            stats.update(_audio_stats(wav, rate))

        started = perf_counter()
        media._encode_video(frames, stage["media"], fps=fps, audio=audio,
                            on_frame=inspection.accept, on_audio=inspect_audio)
        encoding_s = perf_counter() - started
        checks, report = _probe_video(stage["media"], count, width, height, fps, audio_required or bool(stats))
        if report["present"]:
            report.update(stats)
        inspection.save_sheet()
        if _read_image_size(stage["last_frame"]) != (width, height):
            raise ValueError("Last frame dimensions disagree with video")
        _read_image_size(stage["contact_sheet"])
        _write_json(stage["audio_report"], report)
        duration = count / fps
        warnings = inspection.warnings
        if report.get("silence"):
            warnings.append("near_silent_audio")
        if report.get("clipping"):
            warnings.append("audio_clipping")
        requested = base_artifact.get("requested_duration_s")
        if requested is not None and not math.isclose(duration, requested, abs_tol=1e-9):
            warnings.append("duration_deviation")
        if base_artifact.get("trimmed_frame_count", 0) > 0:
            warnings.append("frame_trimmed")
        result = _base(base_artifact, paths, "video")
        result["inspection"]["samples"] = inspection.samples
        result["timing_s"] = {**base_artifact.get("timing_s", {}), "encoding": encoding_s}
        result.update(width=width, height=height, duration_s=duration, frame_count=count, fps=fps,
                      audio=report, validation=_validation(warnings, **checks))
        _write_json(stage["metadata"], result)
    return result
