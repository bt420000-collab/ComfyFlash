from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine import Flash
from engine.artifact import DeliveryError
from engine.bank import TONES
from engine.core import _Run


class ArtifactIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.flash = Flash({"comfyui_root": str(self.root)})

    def test_existing_output_and_its_companions_are_checked_before_models(self):
        output = self.root / "existing.png"
        metadata = output.with_name(output.name + ".meta.json")
        metadata.write_text("old record", encoding="utf-8")
        request = dict(tone="t3", prompt="green square", output=output)
        with patch.object(self.flash, "_check_models") as model_check:
            result = self.flash.preflight(**request)
            self.assertEqual(result["error"]["code"], "OUTPUT_EXISTS")
            model_check.assert_not_called()
            explicit = self.flash.preflight(**request, overwrite=True)
            self.assertTrue(explicit["ok"])
            self.assertTrue(explicit["resolved"]["overwrite"])
        self.assertEqual(metadata.read_text(encoding="utf-8"), "old record")

    def test_an_image_artifact_is_a_valid_reference_and_retains_lineage(self):
        reference = self.root / "original.png"
        reference.touch()
        metadata = str(reference.with_name(reference.name + ".meta.json"))
        source = {"media_type": "image", "path": str(reference), "inspection": {"metadata": metadata}}
        with patch.object(self.flash, "_check_models"), patch("engine.media.image_size", return_value=(640, 480)):
            result = self.flash.preflight("t4", prompt="edit", image_in=source,
                                          output=self.root / "edited.png", request_id="edit-1")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["resolved"]["images"], [str(reference)])
        self.assertEqual(result["resolved"]["input_artifacts"], [metadata])
        self.assertEqual(result["resolved"]["request_id"], "edit-1")

    def test_video_inspection_configuration_is_declared_and_checked(self):
        schema = self.flash.tone_info("t1")["schema"]["properties"]
        self.assertEqual(schema["inspection_frames"]["default"], 6)
        with patch.object(self.flash, "_check_models") as model_check:
            result = self.flash.preflight("t1", prompt="green square", output=self.root / "video.mp4",
                                          inspection_frames=0)
        self.assertEqual(result["error"]["code"], "BAD_PARAM")
        model_check.assert_not_called()

    def test_oom_inside_delivery_still_resets_the_worker_cache(self):
        oom = RuntimeError("allocation rejected with code 2")
        runtime = Mock(dynamic_vram=True)
        runtime.generation.side_effect = nullcontext
        runtime.is_oom.side_effect = lambda exc: exc is oom
        self.flash._rt = runtime
        self.flash._cache["temporary"] = object()

        def fail_delivery(*args, **kwargs):
            raise DeliveryError("encoding failed") from oom

        with patch.object(self.flash, "_check_models"), patch.object(_Run, "_artifact_details", return_value={}), patch.object(
            TONES["t3"], "play", side_effect=lambda run: run.save_image(None)
        ), patch("engine.artifact.deliver_image", side_effect=fail_delivery):
            result = self.flash.play("t3", prompt="test", output=self.root / "image.png")
        self.assertEqual(result["error"]["code"], "OOM", result)
        runtime.hard_reset.assert_called_once_with()
        self.assertFalse(self.flash._cache)
