import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
import torch

from engine import Flash, agent
from engine.core import ParamError, _Run
from engine import media


class RequestPreflightTests(unittest.TestCase):
    def make_flash(self, root):
        flash = Flash(config={"comfyui_root": str(root), "rsi_enabled": False})
        self.addCleanup(flash.close)
        return flash

    def test_t2_rejects_multiple_reference_images_before_model_loading(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / "a.png", root / "b.png"
            Image.new("RGB", (64, 64), "red").save(first)
            Image.new("RGB", (64, 64), "blue").save(second)
            flash = self.make_flash(root)
            with patch.object(flash, "model_path") as model_path:
                with self.assertRaises(ParamError) as caught:
                    flash.validate_request(
                        "t2", prompt="animate", image_in=[str(first), str(second)],
                        output=str(root / "out.mp4"),
                    )
            self.assertEqual(caught.exception.code, "BAD_INPUT_COUNT")
            model_path.assert_not_called()

    def test_video_decode_records_pretrim_and_posttrim_frame_counts(self):
        class RuntimeStub:
            dynamic_vram = True

            def stage(self):
                from contextlib import nullcontext
                return nullcontext()

            def decode(self, vae, samples):
                return torch.zeros((7, 8, 8, 3))

        with TemporaryDirectory() as folder:
            flash = self.make_flash(folder)
            spec = flash._spec("t1")
            request = flash._resolve(spec, "test", None, None, None, 1.6, 1, Path(folder) / "out.mp4", {})
            request["frames"] = 5
            with patch.object(flash, "runtime", return_value=RuntimeStub()):
                run = _Run(flash, spec, request)
                frames = run.decode_frames(object(), {"samples": object()})
        self.assertEqual(frames.shape[0], 5)
        self.assertEqual(run.meta["decoded_frame_count"], 7)
        self.assertEqual(run.meta["trimmed_frame_count"], 2)
        self.assertEqual(run.meta["actual_frame_count"], 5)
        self.assertIn("截断 2 帧", run.meta["frames_note"])

    def test_t4_match_is_not_pixel_exact_but_explicit_canvas_is(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            Image.new("RGB", (1376, 768), "white").save(source)
            flash = self.make_flash(root)
            with patch.object(flash, "model_path", return_value="mock-model"):
                matched = flash.validate_request(
                    "t4", prompt="edit", image_in=str(source), output=str(root / "match.png"),
                    resolution="match",
                )
                explicit = flash.validate_request(
                    "t4", prompt="edit", image_in=str(source), output=str(root / "explicit.png"),
                    resolution="1376x768",
                )
            self.assertEqual((matched["width"], matched["height"]), (1824, 1024))
            self.assertEqual((explicit["width"], explicit["height"]), (1376, 768))

    def test_existing_output_requires_explicit_overwrite(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "existing.png"
            output.write_bytes(b"existing")
            flash = self.make_flash(root)
            with patch.object(flash, "model_path", return_value="mock-model"):
                with self.assertRaises(ParamError) as caught:
                    flash.validate_request("t3", prompt="test", output=str(output))
                allowed = flash.validate_request(
                    "t3", prompt="test", output=str(output), overwrite=True,
                )
            self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
            self.assertTrue(allowed["ok"])
            self.assertTrue(allowed["overwrite"])

    def test_batch_preflight_aborts_all_requests_before_gpu_dispatch(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            config.write_text(json.dumps({"comfyui_root": str(root), "rsi_enabled": False}), encoding="utf-8")
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(json.dumps({"tone": "t3", "prompt": "test", "output": str(root / "good.png")}), encoding="utf-8")
            bad.write_text(json.dumps({"tone": "t4", "prompt": "edit", "output": str(root / "bad.png")}), encoding="utf-8")
            args = type("Args", (), {"config": config, "request": [good, bad]})()
            with patch.object(Flash, "model_path", return_value="mock-model"), patch.object(
                agent, "worker_results"
            ) as workers, patch.object(agent, "hardware_report") as hardware:
                result = agent.play(args)
            self.assertFalse(result["ok"])
            self.assertEqual(result["results"][0]["error"]["code"], "BATCH_ABORTED")
            self.assertEqual(result["results"][1]["error"]["code"], "MISSING_INPUT")
            workers.assert_not_called()
            hardware.assert_not_called()

    def test_dry_run_returns_validation_without_starting_hardware(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            config.write_text(json.dumps({"comfyui_root": str(root), "rsi_enabled": False}), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({"tone": "t3", "prompt": "test", "output": str(root / "out.png")}), encoding="utf-8")
            args = type("Args", (), {"config": config, "request": request, "dry_run": True})()
            with patch.object(Flash, "model_path", return_value="mock-model"), patch.object(agent, "hardware_report") as hardware:
                result = agent.play(args)
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["validations"][0]["width"], 1024)
            hardware.assert_not_called()


class AtomicMediaTests(unittest.TestCase):
    def test_image_write_is_atomic_and_does_not_overwrite_by_default(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "image.png"
            media.save_image(torch.zeros((12, 16, 3)), str(output))
            with Image.open(output) as image:
                self.assertEqual(image.size, (16, 12))
            with self.assertRaises(FileExistsError):
                media.save_image(torch.ones((12, 16, 3)), str(output))
            media.save_image(torch.ones((12, 16, 3)), str(output), overwrite=True)
            with Image.open(output) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(list(root.glob(".*.png")), [])

    def test_failed_image_encode_does_not_leave_a_final_or_temp_file(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "broken.png"
            with patch.object(Image.Image, "save", side_effect=OSError("simulated encoder failure")):
                with self.assertRaisesRegex(OSError, "simulated encoder failure"):
                    media.save_image(torch.zeros((8, 8, 3)), str(output))
            self.assertFalse(output.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_contact_sheet_uses_decoded_frames_and_has_time_labels(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            frames = torch.zeros((9, 32, 48, 3))
            for index in range(9):
                frames[index, :, :, 0] = index / 8
            output = root / "clip.mp4.contact.jpg"
            media.save_contact_sheet(frames, str(output), fps=24, max_frames=6)
            with Image.open(output) as image:
                self.assertGreater(image.width, 384)
                self.assertGreater(image.height, 240)
            last = root / "clip.mp4.last.png"
            media.save_image(frames[-1], str(last))
            with Image.open(last) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_video_inspection_paths_are_stable_and_non_ambiguous(self):
        paths = media.inspection_paths(Path("D:/renders/shot01.mp4"))
        self.assertTrue(paths["contact_sheet"].endswith("shot01.mp4.contact.jpg"))
        self.assertTrue(paths["last_frame"].endswith("shot01.mp4.last.png"))


if __name__ == "__main__":
    unittest.main()
