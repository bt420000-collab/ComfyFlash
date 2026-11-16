import argparse
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine import agent
from engine.bank import TONES
from engine.core import _MODEL_KINDS


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = self.root / "Agent 项目"
        self.project.mkdir()
        self.comfy = self.root / "ComfyUI"
        (self.comfy / "comfy").mkdir(parents=True)
        (self.comfy / "comfy" / "sd.py").touch()
        self.weights = self.root / "models"
        self.args = argparse.Namespace(
            project=self.project, agent="codex", config=self.root / "config.json",
            comfyui_root=str(self.comfy), models_path=str(self.weights), python=None,
        )

    def models_for(self, *tones):
        for tone in tones:
            for role, filename in TONES[tone].TONE["models"].items():
                path = self.weights / _MODEL_KINDS[role] / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

    def run_registration(self):
        with patch.object(agent, "LIBRARIES", ("json",)):
            result = agent.register(self.args)
        self.args.comfyui_root = self.args.models_path = None
        return result

    def test_project_registration_reports_real_model_readiness_and_is_idempotent(self):
        self.models_for("t3")
        first = self.run_registration()
        self.assertTrue(first["ok"])
        self.assertEqual([entry["tone"] for entry in first["functions"] if entry["ready"]], ["t3", "t4"])
        self.assertEqual([entry["tone"] for entry in first["functions"] if entry["missing"]], ["t1", "t2"])
        target = self.project / ".agents/skills/comfyflash/SKILL.md"
        self.assertEqual(Path(first["guide"]), target)
        original = target.read_bytes()
        time = target.stat().st_mtime_ns
        second = self.run_registration()
        self.assertEqual(first, second)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mtime_ns, time)
        self.assertEqual(Path(second["play_argv"][-1]), self.args.config)

    def test_conversation_registration_leaves_other_projects_untouched(self):
        self.models_for(*TONES)
        self.args.project = None
        result = self.run_registration()
        self.assertEqual(result["scope"], "conversation")
        self.assertTrue(all(item["ready"] for item in result["functions"]))
        self.assertFalse(any(self.project.iterdir()))

    def test_no_weights_do_not_create_installed_skill(self):
        result = self.run_registration()
        self.assertEqual(result["code"], "MISSING_MODELS")
        self.assertFalse(result["ok"])
        self.assertFalse(any(self.project.iterdir()))

    def test_explicit_models_path_repairs_an_existing_setup(self):
        first = self.run_registration()
        self.assertFalse(first["ok"])
        self.models_for("t3")
        corrected = self.root / "corrected-models"
        self.weights.rename(corrected)
        self.args.models_path = str(corrected)
        self.assertTrue(self.run_registration()["ok"])

    @unittest.skipUnless(shutil.which("git"), "Git is unavailable")
    def test_project_registration_keeps_local_paths_out_of_git(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, capture_output=True)
        self.models_for("t3")
        self.run_registration()
        target = ".agents/skills/comfyflash/SKILL.md"
        check = subprocess.run(["git", "-C", str(self.project), "check-ignore", "--", target],
                               capture_output=True, check=False)
        self.assertEqual(check.returncode, 0, check.stderr.decode(errors="replace"))
        self.assertFalse((self.project / ".gitignore").exists())

    def test_custom_skill_is_preserved(self):
        existing = self.project / ".agents/skills/comfyflash/SKILL.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("Custom skill", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            agent.register(self.args)
        self.assertEqual(existing.read_text(encoding="utf-8"), "Custom skill")
        self.assertFalse(self.args.config.exists())

    def test_play_reads_request_and_returns_native_validation_error(self):
        self.args.config = agent.configure(self.args)
        request = self.root / "请求.json"
        request.write_text(
            '{"tone":"t2","prompt":"测试","output":"sample.mp4"}', encoding="utf-8-sig"
        )
        result = agent.play(argparse.Namespace(config=self.args.config, request=request))
        self.assertEqual(result["error"]["code"], "MISSING_INPUT")


if __name__ == "__main__":
    unittest.main()
