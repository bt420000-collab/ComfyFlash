from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import torch

from engine import Flash, agent, media
from engine.bank import TONES


class NativeH3ToneTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.first = self.root / "first.png"
        Image.new("RGB", (8, 16), color="red").save(self.first)
        self.flash = Flash({"comfyui_root": str(self.root), "rsi_enabled": False})
        self.addCleanup(self.flash.close)

    def test_t5_declares_a_required_single_first_frame(self):
        info = self.flash.tone_info("t5")
        self.assertIn("image_in", info["schema"]["required"])
        self.assertEqual(info["inputs"]["fields"]["image_in"]["role"], "first_frame")
        request = dict(tone="t5", prompt="The bird hops.", output=self.root / "out.mp4")
        self.assertEqual(self.flash.preflight(**request)["error"]["code"], "MISSING_INPUT")
        result = self.flash.preflight(**request, image_in=[self.first, self.first])
        self.assertEqual(result["error"]["code"], "BAD_INPUT_COUNT")

    def test_t5_passes_the_decoded_image_to_native_first_frame_conditioning(self):
        if "t5" not in TONES:
            self.fail("t5 must be discoverable")
        run = Mock(width=768, height=448, frames=39, prompt="The bird hops.",
                   image_paths=[self.first], rt=SimpleNamespace(torch=torch))
        run.condition.side_effect = lambda build, *args, **kwargs: build(run.rt, *args, **kwargs)
        run.decode_frames.return_value = torch.empty((39, 1, 1, 3))
        cond, latent = object(), object()
        execute = Mock(return_value=SimpleNamespace(result=(cond, latent)))
        nodes = SimpleNamespace(MiniMaxH3ImageToVideo=SimpleNamespace(execute=execute))
        with patch.dict("sys.modules", {"comfy_extras.nodes_minimax_h3": nodes}):
            result = TONES["t5"].play(run)
        inputs = execute.call_args.kwargs
        self.assertEqual(inputs["length"], 39)
        self.assertEqual(inputs["first_frame"].shape, (1, 16, 8, 3))
        self.assertEqual(inputs["first_frame"][0, 0, 0].tolist(), [1.0, 0.0, 0.0])
        run.sample.assert_called_once_with(run.with_lora.return_value, cond, latent)
        self.assertIs(result, run.save_video.return_value)

    def make_reference_video(self, frames=12, fps=12):
        path = self.root / "motion.mp4"
        pixels = torch.zeros((frames, 32, 32, 3))
        pixels[1::2, ..., 0] = 1
        media.save_video(pixels, path, fps=fps)
        return path

    def test_t6_preflight_keeps_order_and_checks_optional_video(self):
        info = self.flash.tone_info("t6")
        self.assertEqual(info["inputs"]["fields"]["image_in"]["max_items"], 9)
        self.assertIn("video_in", info["schema"]["properties"])
        self.assertNotIn("video_in", info["schema"]["required"])
        second = self.root / "second.png"
        Image.new("RGB", (8, 16), color="blue").save(second)
        video = self.make_reference_video()
        request = dict(tone="t6", prompt="Animate <Picture 1> with <Video 1>.",
                       image_in=[second, self.first], output=self.root / "out.mp4")
        with patch.object(self.flash, "model_path", return_value="mock-model"):
            resolved = self.flash.preflight(**request, video_in=video)["resolved"]
            self.assertEqual(resolved["images"], [str(second), str(self.first)])
            self.assertEqual(resolved["video_path"], str(video))
            self.assertTrue(self.flash.preflight(**request)["ok"])
        result = self.flash.preflight(**{**request, "image_in": [self.first] * 10})
        self.assertEqual(result["error"]["code"], "BAD_INPUT_COUNT")
        result = self.flash.preflight(**{**request, "tone": "t2", "image_in": self.first}, video_in=video)
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_INPUT")

    def test_t6_rejects_unreadable_or_too_short_references_before_loading_models(self):
        video = self.make_reference_video(frames=1, fps=24)
        damaged = self.root / "damage.mp4"
        damaged.write_text("bad", encoding="utf-8")
        for reference in (video, damaged, "relative.mp4", [str(video)]):
            with self.subTest(reference=reference), patch.object(self.flash, "model_path") as model_path:
                result = self.flash.preflight("t6", prompt="Match motion.", image_in=self.first,
                                              video_in=reference, output=self.root / "out.mp4")
                self.assertEqual(result["error"]["code"], "BAD_INPUT")
                model_path.assert_not_called()

    def test_t6_passes_ordered_images_and_resampled_video_to_native_conditioning(self):
        if "t6" not in TONES:
            self.fail("t6 must be discoverable")
        second = self.root / "second.png"
        Image.new("RGB", (8, 16), color="blue").save(second)
        run = Mock(width=768, height=448, frames=39, prompt="Match motion.",
                   image_paths=[self.first, second], rt=SimpleNamespace(torch=torch),
                   video_path=self.make_reference_video())
        run.condition.side_effect = lambda build, *args, **kwargs: build(run.rt, *args, **kwargs)
        run.decode_frames.return_value = torch.empty((39, 1, 1, 3))
        cond, latent = object(), object()
        execute = Mock(return_value=SimpleNamespace(result=(cond, latent)))
        nodes = SimpleNamespace(MiniMaxH3ReferenceToVideo=SimpleNamespace(execute=execute))
        with patch.dict("sys.modules", {"comfy_extras.nodes_minimax_h3": nodes}):
            TONES["t6"].play(run)
        inputs = execute.call_args.kwargs
        images = inputs["ref_images"]
        self.assertEqual(list(images), ["ref_image_1", "ref_image_2"])
        self.assertEqual([frame[0, 0, 0].tolist() for frame in images.values()],
                         [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        ref_video = inputs["ref_videos"]["ref_video_1"]
        self.assertEqual(ref_video.shape, (24, 32, 32, 3))
        self.assertLess(float(ref_video[:2, 0, 0, 0].max()), 0.1)
        self.assertGreater(float(ref_video[2:4, 0, 0, 0].min()), 0.9)
        self.assertIs(inputs["vae"], run.vae.return_value)
        run.sample.assert_called_once_with(run.with_lora.return_value, cond, latent)

    def test_batch_accepts_earlier_video_output_and_marks_failed_dependency(self):
        video = str(self.root / "motion.mp4")
        requests = [
            dict(tone="t1", prompt="Generate motion.", output=video),
            dict(tone="t6", prompt="Match motion.", image_in=str(self.first),
                 video_in=video, output=str(self.root / "final.mp4")),
        ]
        with patch.object(Flash, "model_path", return_value="mock-model"):
            checks = agent.preflight_requests({"comfyui_root": str(self.root)}, requests)
        self.assertTrue(checks["ok"], checks)
        self.assertEqual(list(agent.reference_paths(requests[1])), [self.first.resolve(), Path(video).resolve()])
        with patch.object(Flash, "play", return_value={"ok": False}) as play:
            results = agent.render_requests({"comfyui_root": str(self.root)}, requests)
        play.assert_called_once()
        self.assertEqual(results[1]["error"]["code"], "DEPENDENCY_FAILED")
