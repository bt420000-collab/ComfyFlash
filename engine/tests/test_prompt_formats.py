from pathlib import Path
import unittest

from engine.bank import TONES
from engine.core import Flash


ROOT = Path(__file__).resolve().parents[2]
SKILL_GUIDE = ROOT / "engine" / "skills" / "comfyflash" / "SKILL.md"


class PromptFormatMetadataTests(unittest.TestCase):
    def test_every_tone_declares_a_required_model_specific_prompt_format(self):
        expected = {
            "t1": "MiniMax H3 T2VA",
            "t2": "MiniMax H3 Ref2VA",
            "t3": "MiniMax H3-Image T2I",
            "t4": "MiniMax H3-Image Edit",
            "t5": "MiniMax H3 I2VA",
            "t6": "MiniMax H3 Ref2VA",
            "t8": "H3 TARA motion_transfer",
        }
        self.assertEqual(set(TONES), set(expected) | {"t7"})
        self.assertFalse(TONES["t7"].TONE["prompt_format"]["required"])
        for tone_id, expected_name in expected.items():
            with self.subTest(tone=tone_id):
                metadata = TONES[tone_id].TONE.get("prompt_format")
                self.assertIsInstance(metadata, dict)
                self.assertTrue(metadata.get("required"))
                self.assertEqual(metadata.get("name"), expected_name)
                guide = ROOT / metadata["guide"]
                self.assertEqual(guide, SKILL_GUIDE)
                self.assertTrue(guide.is_file(), str(guide))

    def test_tone_list_exposes_prompt_format_to_agents(self):
        flash = object.__new__(Flash)
        items = flash.tone_list()
        self.assertEqual({item["tone"] for item in items}, {"t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"})
        for item in items:
            with self.subTest(tone=item["tone"]):
                self.assertEqual(item["prompt_format"]["required"], item["tone"] != "t7")


if __name__ == "__main__":
    unittest.main()
