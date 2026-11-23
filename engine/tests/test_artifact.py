import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from engine import artifact, media


requires_av = unittest.skipUnless(importlib.util.find_spec("av"), "PyAV unavailable")


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_image_artifact_and_atomic_no_overwrite(self):
        path = self.root / "photo.png"
        image = np.zeros((1, 8, 12, 3), dtype=np.float32)
        def write(seed):
            try:
                return artifact.deliver_image(image + seed / 255, path, {"seed": seed})
            except artifact.DeliveryError as exc:
                self.assertEqual(exc.validation["status"], "hard_error")
                return exc.code
        with ThreadPoolExecutor(6) as pool:
            results = list(pool.map(write, range(6)))
        self.assertEqual(results.count("OUTPUT_EXISTS"), 5)
        result = next(x for x in results if isinstance(x, dict))
        self.assertEqual(tuple(result[k] for k in ("width", "height")), (12, 8))
        self.assertEqual(result, json.loads(Path(result["inspection"]["metadata"]).read_text("utf-8")))
        with Image.open(path) as actual:
            self.assertEqual(actual.getpixel((0, 0)), (result["seed"],) * 3)
        updated = artifact.deliver_image(image, path, {"seed": 100}, overwrite=True)
        self.assertEqual(updated["seed"], 100)

    @requires_av
    def test_video_containers_timestamps_audio_and_decoded_tail(self):
        import av
        fps, count, sr = 24, 11, 32000
        frames = np.zeros((count, 24, 32, 3), dtype=np.float32)
        for i in range(count):
            frames[i, :, :16] = (0.8 * i / count, 0.2, 0.7)
            frames[i, :, 16:] = (0.1, 0.8 * i / count, 0.5)
        for ext in ("mp4", "mov", "mkv"):
            with self.subTest(ext=ext):
                path = self.root / ("clip." + ext)
                audio = {"waveform": np.full((1, 1, sr), 0.25, np.float32), "sample_rate": sr}
                result = artifact.deliver_video(frames, path, {"seed": 12}, fps=fps, audio=audio,
                                                audio_required=True)
                pack = result["inspection"]
                self.assertEqual(json.loads(Path(pack["metadata"]).read_text("utf-8")), result)
                self.assertEqual(result["validation"]["status"], "passed")
                self.assertEqual(result["validation"]["checked_decode_frame_count"], count)
                self.assertTrue(result["validation"]["first_frame_decodable"])
                self.assertEqual(result["validation"]["audio_stream_count"], 1)
                self.assertEqual(result["frame_count"], count)
                self.assertEqual(result["duration_s"], count / fps)
                indices = [row["frame_index"] for row in pack["samples"]]
                self.assertEqual(indices, [0, 2, 4, 6, 8, 10])
                self.assertEqual([row["timestamp_s"] for row in pack["samples"]], [i / fps for i in indices])
                self.assertEqual(Path(pack["contact_sheet"]), self.root / f"clip.{ext}.contact.jpg")
                with Image.open(pack["contact_sheet"]) as sheet:
                    sheet.load()
                    self.assertEqual(sheet.format, "JPEG")
                    self.assertGreater(sheet.width, 2 * 32)
                with Image.open(pack["last_frame"]) as last:
                    last_frame = np.asarray(last).astype(float)
                np.testing.assert_array_equal(last_frame, (frames[-1] * 255).round())
                with av.open(str(path)) as container:
                    decoded = list(container.decode(video=0))
                    self.assertEqual(len(decoded), count)
                    actual = decoded[-1].to_ndarray(format="rgb24")
                self.assertLess(np.abs(actual - last_frame).mean(), 4)
                report = json.loads(Path(pack["audio_report"]).read_text("utf-8"))
                self.assertTrue(report["present"])
                self.assertEqual((report["codec"], report["sample_rate"], report["channels"]), ("aac", sr, 2))
                self.assertAlmostEqual(report["duration_s"], count / fps, delta=0.04)
                self.assertAlmostEqual(report["rms"], 0.25, places=4)
                self.assertAlmostEqual(report["peak"], 0.25, places=4)
                self.assertFalse(report["silence"])
                self.assertFalse(report["clipping"])

    @requires_av
    def test_standalone_video_validates_and_preserves_previous_media(self):
        import av

        path = self.root / "standalone.mp4"
        frames = np.zeros((3, 16, 32, 3), dtype=np.float32)
        saved_path, validation = media.save_video(frames, path, return_validation=True)
        self.assertEqual(saved_path, str(path))
        self.assertEqual(validation, {"status": "passed", "video_stream": True, "first_frame_decodable": True,
                                      "width": 32, "height": 16, "fps": 24.0, "duration_s": 0.125,
                                      "audio_present": False, "audio_stream_count": 0})
        with self.assertRaises(FileExistsError):
            media.save_video(frames + 1, path)
        with av.open(str(path)) as container:
            self.assertEqual(next(container.decode(video=0)).to_ndarray(format="rgb24").max(), 0)
        self.assertEqual(media.save_video(frames + 1, path, overwrite=True), str(path))
        with av.open(str(path)) as container:
            self.assertEqual(next(container.decode(video=0)).to_ndarray(format="rgb24").min(), 255)
        self.assertEqual(list(self.root.iterdir()), [path])

    @requires_av
    def test_silent_black_and_trim_warn_while_optional_absent_audio_is_valid(self):
        frames = np.zeros((2, 16, 16, 3), dtype=np.float32)
        audio = {"waveform": np.zeros((1, 1, 2000)), "sample_rate": 32000}
        for case, signal in (("silent", audio), ("absent", None)):
            path = self.root / (case + ".mp4")
            result = artifact.deliver_video(frames, path, {"trimmed_frame_count": 2, "requested_duration_s": 1},
                                            fps=24, audio=signal)
            report = result["audio"]
            self.assertEqual(report["present"], signal is not None)
            warnings = result["validation"]["warnings"]
            self.assertIn("black_frame", warnings)
            self.assertIn("frame_trimmed", warnings)
            self.assertIn("duration_deviation", warnings)
            if signal:
                self.assertIn("near_silent_audio", warnings)
            self.assertEqual(result["validation"]["status"], "warning")
            self.assertTrue(path.is_file())

    @requires_av
    def test_hard_errors_leave_no_final_files(self):
        path = self.root / "bad.mp4"
        zeros = np.zeros((4, 16, 16, 3), dtype=np.float32)
        with_nan = zeros.copy()
        with_nan[1, 0, 0, 0] = np.nan
        for frames, audio, required in [
            (zeros[:0], None, False), (with_nan, None, False), (zeros, None, True),
            (zeros, {"waveform": np.full((1, 1, 1000), np.inf), "sample_rate": 32000}, True)
        ]:
            with self.assertRaises(artifact.DeliveryError) as caught:
                artifact.deliver_video(frames, path, {}, fps=24, audio=audio, audio_required=required)
            self.assertEqual(caught.exception.code, "DELIVERY_ERROR")
            self.assertEqual(caught.exception.validation["status"], "hard_error")
            self.assertEqual(list(self.root.iterdir()), [])

    @requires_av
    def test_closed_but_corrupt_container_is_rejected(self):
        path = self.root / "bad.mp4"
        original = media._encode_video
        def damage(frames, staging, *args, **kwargs):
            result = original(frames, staging, *args, **kwargs)
            Path(staging).write_bytes(b"broken")
            return result
        with patch.object(media, "_encode_video", side_effect=damage), self.assertRaises(artifact.DeliveryError):
            artifact.deliver_video(np.ones((2, 16, 16, 3)), path, {}, fps=24)
        self.assertEqual(list(self.root.iterdir()), [])

    @requires_av
    def test_same_size_tail_packet_damage_leaves_no_final_files(self):
        import av

        frames = np.random.default_rng(18).uniform(size=(12, 24, 32, 3)).astype(np.float32)
        original = media._encode_video

        def damage(frames, staging, *args, **kwargs):
            result = original(frames, staging, *args, **kwargs)
            with av.open(str(staging)) as container:
                tail = [packet for packet in container.demux(video=0) if packet.size][-1]
            data = Path(staging).read_bytes()
            offset = data.index(bytes(tail), tail.pos)
            # This fixture has one length-prefixed slice in its last packet.
            # Preserve its length and NAL header to exercise the codec payload.
            start = offset + preserved_prefix
            end = offset + tail.size
            Path(staging).write_bytes(data[:start] + b"\xff" * (end - start) + data[end:])
            return result

        for ext in ("mp4", "mov", "mkv"):
            for preserved_prefix in (0, 5):
                with self.subTest(ext=ext, preserved_prefix=preserved_prefix):
                    case = self.root / f"{ext}-{preserved_prefix}"
                    path = case / ("bad." + ext)
                    with patch.object(media, "_encode_video", side_effect=damage):
                        with self.assertRaises(artifact.DeliveryError) as failure:
                            artifact.deliver_video(frames, path, {}, fps=24)
                    self.assertEqual(failure.exception.validation["status"], "hard_error")
                    self.assertEqual(list(case.iterdir()), [])

    def test_metadata_failure_leaves_previous_artifact_intact(self):
        path = self.root / "img.png"
        artifact.deliver_image(np.zeros((16, 16, 3)), path, {"seed": 1})
        previous = {f: f.read_bytes() for f in self.root.iterdir()}
        with self.assertRaises(artifact.DeliveryError):
            artifact.deliver_image(np.ones((16, 16, 3)), path, {"invalid": object()}, overwrite=True)
        self.assertEqual(previous, {f: f.read_bytes() for f in self.root.iterdir()})

    @requires_av
    def test_same_size_audio_packet_damage_leaves_no_final_files(self):
        import av

        original = media._encode_video

        def damage(frames, staging, *args, **kwargs):
            result = original(frames, staging, *args, **kwargs)
            staged = Path(staging)
            with av.open(str(staged)) as container:
                packet = [p for p in container.demux(audio=0) if p.size][2]
            data = staged.read_bytes()
            offset = data.index(bytes(packet), packet.pos)
            staged.write_bytes(data[:offset] + b"\xff" * packet.size + data[offset + packet.size:])
            return result

        samples = np.sin(np.linspace(0, 800, 8000, dtype=np.float32)) / 4
        audio = {"waveform": samples[None, None, :], "sample_rate": 32000}
        with patch.object(media, "_encode_video", side_effect=damage), self.assertRaises(artifact.DeliveryError):
            artifact.deliver_video(np.zeros((6, 16, 16, 3)), self.root / "damaged.mp4", {},
                                   fps=24, audio=audio, audio_required=True)
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    def test_windows_occupied_destination_preserves_new_product_and_existing_bundle(self):
        image = np.zeros((16, 16, 3), np.float32)
        for locked in ("media", "metadata"):
            path = self.root / (locked + ".png")
            original = artifact.deliver_image(image, path, {"seed": 1})
            metadata = Path(original["inspection"]["metadata"])
            with (path if locked == "media" else metadata).open("rb"):
                with self.assertRaises(artifact.DeliveryError) as caught:
                    artifact.deliver_image(image + 1, path, {"seed": 2}, overwrite=True)
            exc = caught.exception
            self.assertEqual(exc.code, "DELIVERY_ERROR")
            with Image.open(exc.recoverable_paths["media"]) as staged:
                self.assertEqual(staged.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(json.loads(metadata.read_text("utf-8"))["seed"], 1)
            with Image.open(path) as restored:
                self.assertEqual(restored.getpixel((0, 0)), (0, 0, 0))
            artifact.deliver_image(image + 1, path, {"seed": 2}, overwrite=True)

    def test_newly_occupied_destination_keeps_already_encoded_file(self):
        path = self.root / "occupied.png"
        original = media._encode_image
        def concurrent_creator(tensor, stage):
            result = original(tensor, stage)
            path.write_bytes(b"created independently")
            return result
        with patch.object(media, "_encode_image", side_effect=concurrent_creator):
            with self.assertRaises(artifact.DeliveryError) as failure:
                artifact.deliver_image(np.zeros((8, 12, 3)), path, {})
        self.assertEqual(failure.exception.code, "OUTPUT_EXISTS")
        self.assertEqual(path.read_bytes(), b"created independently")
        self.assertIn("media", failure.exception.recoverable_paths)
        with Image.open(failure.exception.recoverable_paths["media"]) as image:
            self.assertEqual(image.size, (12, 8))

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    @requires_av
    def test_locked_later_video_companion_restores_prior_completed_bundle(self):
        path = self.root / "video.mp4"
        frames = np.zeros((4, 16, 16, 3))
        completed = artifact.deliver_video(frames, path, {"seed": 1}, fps=24)
        old = {file: file.read_bytes() for file in self.root.iterdir()}
        with Path(completed["inspection"]["last_frame"]).open("rb"):
            with self.assertRaises(artifact.DeliveryError) as failure:
                artifact.deliver_video(frames + 1, path, {"seed": 2}, fps=24, overwrite=True)
        self.assertEqual({file: file.read_bytes() for file in old}, old)
        recovery = failure.exception.recoverable_paths
        self.assertEqual(set(recovery), {"media", "last_frame", "contact_sheet", "audio_report", "metadata"})
        self.assertTrue(all(Path(f).is_file() for f in recovery.values()))
        self.assertEqual(json.loads(Path(recovery["metadata"]).read_text("utf-8"))["seed"], 2)
        with Image.open(recovery["last_frame"]) as last:
            self.assertEqual(last.getpixel((0, 0)), (255, 255, 255))

    @requires_av
    def test_declared_count_mismatch_in_otherwise_readable_video_is_rejected(self):
        path = self.root / "short.mkv"
        original = media._encode_video
        def shortened(frames, stage, *args, **kwargs):
            return original(frames[:-1], stage, *args, **kwargs)
        with patch.object(media, "_encode_video", side_effect=shortened), self.assertRaises(artifact.DeliveryError) as exc:
            artifact.deliver_video(np.zeros((5, 16, 16, 3)), path, {}, fps=24)
        self.assertIn("Expected 5", str(exc.exception))
        self.assertEqual(list(self.root.iterdir()), [])

    @requires_av
    def test_encoding_time_is_in_sidecar_and_preserves_prior_stage_timings(self):
        path = self.root / "profile.mp4"
        base = {"timing_s": {"sampling": 3.25}}
        result = artifact.deliver_video(np.zeros((2, 16, 16, 3)), path, base, fps=24)
        self.assertIn("encoding", result["timing_s"])
        self.assertGreater(result["timing_s"]["encoding"], 0)
        self.assertEqual(result["timing_s"]["sampling"], 3.25)
        self.assertEqual(base, {"timing_s": {"sampling": 3.25}})
        self.assertEqual(result["timing_s"], json.loads(Path(result["inspection"]["metadata"]).read_text("utf-8"))["timing_s"])
