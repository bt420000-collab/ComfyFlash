from contextlib import nullcontext
from fractions import Fraction
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

    def test_tara_requires_a_guide_and_aligns_to_its_frame_grid(self):
        info = self.flash.tone_info("t8")
        self.assertIn("video_in", info["schema"]["required"])
        self.assertFalse(info["plugins_ready"])
        plugin = self.root / "custom_nodes" / "ComfyUI-BFSNodes"
        plugin.mkdir(parents=True)
        (plugin / "minimax_h3_native_temporal.py").touch()
        source = self.make_reference_video(frames=12, fps=12)
        request = dict(tone="t8", prompt="motion_transfer: Follow <Video 1>.",
                       image_in=str(self.first), duration=4.5, output=str(self.root / "motion_out.mp4"))
        self.assertEqual(self.flash.preflight(**request)["error"]["code"], "MISSING_INPUT")
        with patch.object(self.flash, "model_path", return_value="mock-model"):
            plan = self.flash.preflight(**request, video_in=source)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual((plan["resolved"]["aligned_frame_count"], plan["resolved"]["fps"]), (22, 24))
        self.assertEqual(plan["resolved"]["advanced"], {
            "steps": 6, "sampler": "lcm", "scheduler": "pika", "cfg": 1.0, "ref_image_size": "max",
        })
        models = {item["role"]: item["file"] for item in info["models"]}
        self.assertEqual(models["lora_turbo"], "pika_h3_flex_ref2va_v1_bf16.safetensors")

    def test_tara_explicit_dmd_recipe_uses_its_own_weight(self):
        plugin = self.root / "custom_nodes" / "ComfyUI-BFSNodes"
        plugin.mkdir(parents=True)
        (plugin / "minimax_h3_native_temporal.py").touch()
        request = dict(tone="t8", prompt="motion_transfer: Follow <Video 1>.",
                       image_in=self.first, video_in=self.make_reference_video(),
                       output=self.root / "dmd.mp4", steps=8)
        with patch.object(self.flash, "model_path", side_effect=lambda name, kind: name):
            plan = self.flash.preflight(**request)
            self.assertTrue(plan["ok"], plan)
            self.assertEqual((plan["resolved"]["advanced"]["sampler"],
                              plan["resolved"]["advanced"]["scheduler"]), ("euler", "simple"))
            models = self.flash.validate_request(**request)["model_paths"]
        self.assertEqual(models["lora_turbo"], "minimax_h3_dmd_ref2va_8step_turbo_pruned.safetensors")
        self.assertEqual(self.flash.preflight(**request, scheduler="pika")["error"]["code"], "BAD_PARAM")

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

    def test_relayvsr_preflight_keeps_native_rate_and_doubles_source_size(self):
        rate = Fraction(30000, 1001)
        source = self.make_reference_video(frames=12, fps=rate)
        spec = self.flash.tone_info("t7")
        self.assertEqual(spec["schema"]["required"], ["tone", "video_in", "output"])
        output = self.root / "upscaled.mp4"
        with patch.object(self.flash, "model_path", return_value="mock-model"):
            result = self.flash.preflight("t7", video_in=source, output=output)
            self.assertTrue(result["ok"], result)
            plan = result["resolved"]
            self.assertEqual((plan["width"], plan["height"], plan["frames"]), (64, 64, 12))
            self.assertEqual((plan["fps"], plan["seed"]), (float(rate), 42))
            self.assertEqual(plan["advanced"], {"steps": 1, "sampler": "euler", "scheduler": "simple",
                                                "cfg": 1.0, "detail": 1.0, "upscale_preset": "relayvsr",
                                                "protect_source": True})
            for settings in ({"resolution": "1k"}, {"duration": 2}, {"fps": 24}, {"steps": 2}):
                with self.subTest(settings=settings):
                    self.assertFalse(self.flash.preflight("t7", video_in=source, output=output, **settings)["ok"])
        with patch("psutil.virtual_memory", return_value=SimpleNamespace(available=1024)), \
             patch.object(self.flash, "model_path") as model_path:
            result = self.flash.preflight("t7", video_in=source, output=output)
            self.assertEqual(result["error"]["code"], "INSUFFICIENT_RAM")
            model_path.assert_not_called()

    def test_relayvsr_batch_can_preflight_output_of_earlier_video(self):
        source = str(self.root / "motion.mp4")
        requests = [
            dict(tone="t1", prompt="Generate motion.", resolution="768x448", duration=1, output=source),
            dict(tone="t7", video_in=source, output=str(self.root / "upscaled.mp4")),
        ]
        with patch.object(Flash, "model_path", return_value="mock-model"):
            result = agent.preflight_requests({"comfyui_root": str(self.root)}, requests)
        self.assertTrue(result["ok"], result)
        upscale = result["validations"][1]
        self.assertEqual((upscale["width"], upscale["height"], upscale["fps"]), (1536, 896, 24))
        self.assertEqual(upscale["aligned_frame_count"], result["validations"][0]["aligned_frame_count"])

    def test_relayvsr_uses_double_size_with_a_legacy_local_preset(self):
        source = self.make_reference_video(frames=12)
        output = self.root / "upscaled.mp4"
        with Flash({"comfyui_root": str(self.root), "t7_upscale_preset": "dlss5_relayvsr"}) as flash, \
             patch.object(flash, "model_path", return_value="mock-model"):
            for settings in ({}, {"upscale_preset": "default"}, {"upscale_preset": "relayvsr"}):
                with self.subTest(settings=settings):
                    result = flash.preflight("t7", video_in=source, output=output, **settings)
                    self.assertTrue(result["ok"], result)
                    plan = result["resolved"]
                    self.assertEqual((plan["width"], plan["height"], plan["frames"]), (64, 64, 12))
                    self.assertEqual(plan["advanced"]["upscale_preset"], "relayvsr")

    def test_relayvsr_rejects_the_removed_preset_before_loading_models(self):
        source = self.make_reference_video()
        schema = self.flash.tone_info("t7")["schema"]
        self.assertEqual(set(schema["properties"]["upscale_preset"]["enum"]), {"default", "relayvsr"})
        with patch.object(self.flash, "model_path") as model_path:
            result = self.flash.preflight("t7", video_in=source, output=self.root / "upscaled.mp4",
                                          upscale_preset="dlss5_relayvsr")
        self.assertEqual(result["error"]["code"], "BAD_PARAM")
        model_path.assert_not_called()

    def test_relayvsr_preserves_small_details_but_removes_large_false_structures(self):
        frames = torch.full((2, 64, 64, 3), 0.5)
        enhanced = torch.full((2, 128, 128, 3), 0.48)
        enhanced[:, :, ::2] = 0.52
        enhanced[0, 40:88, 40:88] = 0.15
        unchanged = enhanced[1].clone()

        restored = TONES["t7"]._protect_source_frames(frames, enhanced)

        self.assertEqual(restored, 1)
        torch.testing.assert_close(enhanced[0, 40:88, 40:88], torch.full((48, 48, 3), 0.5))
        torch.testing.assert_close(enhanced[0, 0, 0], torch.full((3,), 0.52))
        torch.testing.assert_close(enhanced[1], unchanged)

    def test_relayvsr_reuses_the_source_frames_for_both_nodes_and_retains_audio(self):
        frames, upscaled = torch.empty((12, 16, 32, 3)), torch.empty((12, 32, 64, 3))
        audio = {"waveform": torch.ones((1, 2, 16000)), "sample_rate": 48000}
        source = SimpleNamespace(images=frames, audio=audio, frame_rate=Fraction(30000, 1001))
        video = Mock(return_value=SimpleNamespace(get_components=lambda: source))
        cond, latent = object(), object()
        condition = Mock(return_value=SimpleNamespace(result=(cond, latent)))
        decode = Mock(return_value=SimpleNamespace(result=(upscaled,)))
        run = Mock(rt=SimpleNamespace(stage=nullcontext), video_path=self.root / "source.mp4",
                   params={"detail": 0.8, "protect_source": False}, req={"source_audio": True}, meta={})
        run.timed_stage.side_effect = lambda stage: nullcontext()
        run.condition.side_effect = lambda build, *args: build(run.rt, *args)
        modules = {"comfy_api.latest": SimpleNamespace(InputImpl=SimpleNamespace(VideoFromFile=video)),
                   "comfy_extras.nodes_wan": SimpleNamespace(RelayVSRConditioning=SimpleNamespace(execute=condition),
                                                             VSRDecode=SimpleNamespace(execute=decode))}
        with patch.dict("sys.modules", modules):
            result = TONES["t7"].play(run)
        condition.assert_called_once_with(run.unet.return_value, frames)
        run.sample.assert_called_once_with(run.unet.return_value, cond, latent, negative=cond)
        decode.assert_called_once_with(run.vae.return_value, run.sample.return_value, frames, detail=0.8)
        run.save_video.assert_called_once_with(upscaled, audio)
        self.assertEqual(run.req["fps"], float(source.frame_rate))
        self.assertIs(result, run.save_video.return_value)
