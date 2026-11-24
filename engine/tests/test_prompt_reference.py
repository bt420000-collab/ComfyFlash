from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from engine.prompt_reference import read_pack


ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "engine" / "agent.py"


class PromptReferenceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "h3 prompt pack.sqlite"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript("""
                CREATE TABLE skills (entry_path TEXT PRIMARY KEY, name TEXT, description TEXT);
                CREATE TABLE files (path TEXT PRIMARY KEY, skill_entry_path TEXT, content TEXT);
                CREATE VIEW text_files AS SELECT path, skill_entry_path, content AS text FROM files;
            """)
            db.executemany("INSERT INTO skills VALUES (?, ?, ?)", [
                ("product/SKILL.md", "product-ad", "餐饮广告和口红TVC产品镜头"),
                ("motion/SKILL.md", "poster-motion", "海报动态化"),
                ("generic/SKILL.md", "generic-tvc", "通用广告"),
            ])
            db.executemany("INSERT INTO files VALUES (?, ?, ?)", [
                ("h3-prompt-writing/SKILL.md", "h3-prompt-writing/SKILL.md", "Official H3 workflow"),
                ("h3-prompt-writing/references/base-en.txt", "h3-prompt-writing/SKILL.md", "T2VA and I2VA fields"),
                ("h3-prompt-writing/references/ref-en.txt", "h3-prompt-writing/SKILL.md", "Ref2VA fields"),
                ("h3-prompt-expert/references/modes-t2va.md", "h3-prompt-expert/SKILL.md", "Alternate workflow"),
                ("product/SKILL.md", "product/SKILL.md", "Product workflow"),
                ("product/references/h3-video-prompt-template.md", "product/SKILL.md", "x" * 12000),
                ("product/references/h3-video-prompt-template.py", "product/SKILL.md", "must stay out"),
                ("motion/SKILL.md", "motion/SKILL.md", "Motion workflow"),
            ])

    def call(self, *args):
        completed = subprocess.run([sys.executable, "-B", str(ENTRY), *args], cwd=ROOT,
                                   capture_output=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        return json.loads(completed.stdout)

    def test_returns_a_mode_and_one_bounded_scene_reference_without_writing(self):
        before = self.db.read_bytes()
        result = self.call("prompt-ref", "t2", "--query", "餐饮 广告", "--db", str(self.db))
        self.assertTrue(result["available"])
        self.assertEqual(result["official_skill"]["text"], "Official H3 workflow")
        self.assertEqual(result["references"][0]["path"], "h3-prompt-writing/references/ref-en.txt")
        self.assertEqual(result["references"][0]["text"], "Ref2VA fields")
        scene = result["references"][1]
        self.assertEqual(scene["path"], "product/references/h3-video-prompt-template.md")
        self.assertTrue(scene["truncated"])
        self.assertLessEqual(len(scene["text"]), 3000)
        product = self.call("prompt-ref", "t2", "--query", "口红 TVC", "--db", str(self.db))
        self.assertEqual(product["matches"][0]["entry_path"], "product/SKILL.md")
        self.assertEqual(self.db.read_bytes(), before)

    def test_exact_skill_and_no_match_have_predictable_fallbacks(self):
        result = self.call("prompt-ref", "t1", "--entry", "motion/SKILL.md", "--db", str(self.db))
        self.assertEqual([item["text"] for item in result["references"]], ["T2VA and I2VA fields", "Motion workflow"])
        result = self.call("prompt-ref", "t1", "--query", "no matching mot", "--db", str(self.db))
        self.assertEqual([item["text"] for item in result["references"]], ["T2VA and I2VA fields"])

    def test_full_guide_reads_the_selected_official_source(self):
        content = "Ref2VA fields\n" + "x" * 8000 + "\nReference example"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE files SET content = ? WHERE path = ?",
                       (content, "h3-prompt-writing/references/ref-en.txt"))
        default = self.call("prompt-ref", "t2", "--db", str(self.db))["references"][0]
        self.assertTrue(default["truncated"])
        self.assertEqual(default["text"], content[:len(default["text"])])
        full = self.call("prompt-ref", "t2", "--full-guide", "--db", str(self.db))["references"][0]
        self.assertFalse(full["truncated"])
        self.assertEqual(full["text"], content)

    def test_video_search_excludes_image_skills_from_categorized_database(self):
        image_skill = "h3-image-prompt-writing/SKILL.md"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("ALTER TABLE skills ADD COLUMN medium TEXT NOT NULL DEFAULT 'video'")
            db.execute("INSERT INTO skills VALUES (?, ?, ?, ?)",
                       (image_skill, "poster-image", "海报动态化", "image"))
            db.execute("INSERT INTO files VALUES (?, ?, ?)", (image_skill, image_skill, "Image workflow"))
        result = self.call("prompt-ref", "t1", "--query", "海报", "--db", str(self.db))
        self.assertEqual([item["entry_path"] for item in result["matches"]], ["motion/SKILL.md"])
        with self.assertRaises(ValueError):
            read_pack("t1", db_path=self.db, entry=image_skill)

    def test_optional_database_and_tone_discovery(self):
        missing = self.db.with_name("missing.sqlite")
        result = self.call("prompt-ref", "t1", "--db", str(missing))
        self.assertFalse(result["available"])
        self.assertEqual(Path(result["prompt_guide_ref"]), ROOT / "engine/skills/comfyflash/SKILL.md")
        self.assertFalse(missing.exists())
        info = self.call("tone-info", "t2")["info"]
        self.assertEqual(info["prompt_ref_argv"][-2:], ["prompt-ref", "t2"])
        self.assertNotIn("prompt_ref_argv", self.call("tone-info", "t3")["info"])

    def test_native_h3_video_tones_use_their_matching_prompt_modes(self):
        for tone, mode in (("t5", "T2VA and I2VA fields"), ("t6", "Ref2VA fields")):
            with self.subTest(tone=tone):
                info = self.call("tone-info", tone)["info"]
                self.assertEqual(info["prompt_ref_argv"][-2:], ["prompt-ref", tone])
                pack = self.call("prompt-ref", tone, "--db", str(self.db))
                self.assertEqual(pack["references"][0]["text"], mode)
