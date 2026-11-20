import argparse
import hashlib
import io
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine import agent, setup_checks
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
        with patch.object(agent, "choose_python", return_value={
            "ready": True, "path": str(Path(shutil.which("python") or "python").absolute()),
            "ram_mib": 49152, "gpus": [
                {"index": index, "name": "RTX", "total_mib": 16300, "free_mib": 15500, "ready": True,
                 "video_acceleration": {"sage_attention": True, "fp16_accumulation": True}}
                for index in range(2)
            ],
        }), patch.object(setup_checks, "nvidia_inventory", return_value=[]), patch.object(
            agent, "model_issue", side_effect=lambda path, artifact: None if path.is_file() else "missing"
        ):
            result = agent.register(self.args)
        if self.args.config.is_file():
            self.args.comfyui_root = self.args.models_path = None
        return result

    def test_project_registration_reports_real_model_readiness_and_is_idempotent(self):
        self.models_for(*TONES)
        first = self.run_registration()
        self.assertTrue(first["ok"])
        self.assertTrue(all(entry["ready"] for entry in first["functions"]))
        self.assertIn("默认", first["default_engine"])
        self.assertEqual(first["hardware"]["batch_devices"], [0, 1])
        saved = agent.read_json(self.args.config)
        self.assertEqual(saved["python"], first["play_argv"][0])
        self.assertTrue(saved["sage_attention"])
        self.assertTrue(saved["fp16_accumulation"])
        target = self.project / ".agents/skills/comfyflash/SKILL.md"
        self.assertEqual(Path(first["guide"]), target)
        installed = target.read_text(encoding="utf-8")
        for name in ("h3-video.md", "h3-image.md"):
            reference = agent.GUIDE.parent / "references" / name
            self.assertIn(f"(<{reference.as_posix()}>)", installed)
            self.assertTrue(reference.is_file())
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
        guide = Path(result["guide"])
        content = guide.read_text(encoding="utf-8")
        for name in ("h3-video.md", "h3-image.md"):
            reference = f"references/{name}"
            self.assertIn(f"({reference})", content)
            self.assertTrue((guide.parent / reference).is_file())

    def test_no_weights_do_not_create_installed_skill(self):
        result = self.run_registration()
        self.assertEqual(result["code"], "MISSING_MODELS")
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_download_confirmation"])
        self.assertEqual(len(result["missing_models"]), 9)
        self.assertFalse(self.args.config.exists())
        self.assertFalse(any(self.project.iterdir()))

    def test_registration_honors_explicit_acceleration_settings(self):
        self.models_for(*TONES)
        initial = {"sage_attention": False, "fp16_accumulation": False}
        agent.write_text(self.args.config, agent.json_text(initial))
        result = self.run_registration()
        self.assertTrue(result["ok"])
        self.assertEqual(result["video_acceleration"], initial)
        saved = agent.read_json(self.args.config)
        self.assertFalse(saved["sage_attention"])
        self.assertFalse(saved["fp16_accumulation"])

    def test_explicit_models_path_repairs_an_existing_setup(self):
        old = agent.json_text({"comfyui_root": str(self.comfy), "comfyui_models_path": str(self.weights)})
        self.args.config.write_text(old, encoding="utf-8")
        first = self.run_registration()
        self.assertFalse(first["ok"])
        self.assertEqual(self.args.config.read_text(encoding="utf-8"), old)
        self.weights = self.root / "corrected-models"
        self.models_for(*TONES)
        self.args.models_path = str(self.weights)
        self.assertTrue(self.run_registration()["ok"])

    @unittest.skipUnless(shutil.which("git"), "Git is unavailable")
    def test_project_registration_keeps_local_paths_out_of_git(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, capture_output=True)
        self.models_for(*TONES)
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
        self.models_for(*TONES)
        self.run_registration()
        request = self.root / "请求.json"
        request.write_text(
            '{"tone":"t2","prompt":"测试","output":"sample.mp4"}', encoding="utf-8-sig"
        )
        result = agent.play(argparse.Namespace(config=self.args.config, request=request))
        self.assertEqual(result["error"]["code"], "MISSING_INPUT")

    def test_cuda_mask_preserves_logical_device_order(self):
        self.assertEqual(agent.device_mask(1, "GPU-a, GPU-b"), "GPU-b")
        self.assertEqual(agent.device_mask(2, None), "2")
        with self.assertRaises(ValueError):
            agent.device_mask(2, "1,0")

    def test_structurally_bad_weight_is_detected(self):
        path = self.weights / "weight.safetensors"
        self.weights.mkdir()
        path.write_bytes(b"not tensors")
        self.assertEqual(agent.model_issue(path, {"bytes": 11}), "invalid_header")

    def test_download_requires_affirmative_consent(self):
        self.args.yes = False
        with patch.object(agent, "download_weight") as download:
            with self.assertRaises(ValueError):
                agent.download_models(self.args)
            download.assert_not_called()
        self.assertFalse(self.args.config.exists())

    def test_download_rejects_bad_bytes_and_preserves_previous_file(self):
        path = self.root / "weight.safetensors"
        path.write_bytes(b"old")
        header = b'{"tensor":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}'
        weight = len(header).to_bytes(8, "little") + header + b"x"
        entry = {"path": str(path), "url": "https://example.invalid/model",
                 "bytes": len(weight), "sha256": hashlib.sha256(weight).hexdigest()}
        with patch.object(setup_checks, "urlopen", return_value=io.BytesIO(weight[:-1] + b"y")):
            with self.assertRaises(ValueError):
                setup_checks.download_weight(entry)
        self.assertEqual(path.read_bytes(), b"old")
        self.assertFalse(list(self.root.glob("*.part")))
        with patch.object(setup_checks, "urlopen", return_value=io.BytesIO(weight)):
            self.assertTrue(setup_checks.download_weight(entry))
        self.assertEqual(path.read_bytes(), weight)

    def test_duplicate_batch_outputs_are_rejected_before_rendering(self):
        request = self.root / "duplicate.json"
        request.write_text(agent.json_text({"output": str(self.root / "out.png")}), encoding="utf-8")
        args = argparse.Namespace(config=self.args.config, request=[request, request])
        with self.assertRaisesRegex(ValueError, "不同的 output"):
            agent.play(args)


if __name__ == "__main__":
    unittest.main()
