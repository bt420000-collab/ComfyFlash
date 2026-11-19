import unittest

from engine.h3 import resolve_canvas


class CanvasTests(unittest.TestCase):
    def test_image_aspect_presets_use_1k_area_on_the_model_grid(self):
        canvases = {
            "16:9": (1376, 768),
            "9:16": (768, 1376),
            "4:3": (1184, 896),
            "3:4": (896, 1184),
            "1:1": (1024, 1024),
        }
        for preset, expected in canvases.items():
            with self.subTest(preset=preset):
                self.assertEqual(resolve_canvas("image", preset), expected)

    def test_image_explicit_dimensions_are_preserved(self):
        self.assertEqual(resolve_canvas("image", "1024x576"), (1024, 576))

    def test_image_match_uses_reference_image_dimensions(self):
        self.assertEqual(resolve_canvas("image", "match", (1440, 1088)), (1440, 1088))

    def test_video_preset_keeps_its_video_canvas(self):
        self.assertEqual(resolve_canvas("video", "16:9"), (1344, 768))
