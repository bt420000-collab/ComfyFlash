import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from engine import Flash, agent


class ProductionApprovalTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = self.root / "film"
        self.project.mkdir()
        self.marker = self.write(self.project / ".comfyflash-project.json", {"video_workflow": "cinespindle"})
        self.frame = self.project / "frame.png"
        Image.new("RGB", (64, 64), "white").save(self.frame)
        self.config = {"comfyui_root": str(self.root), "rsi_enabled": False}
        self.flash = Flash(config=self.config)
        self.addCleanup(self.flash.close)
        self.request = {"tone": "t5", "prompt": "The subject turns slowly.",
                        "image_in": str(self.frame), "output": str(self.project / "shots" / "out.mp4")}

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_raw_video_dry_run_and_public_play_stop_before_loading_models(self):
        config = self.write(self.root / "config.json", self.config)
        payload = self.write(self.root / "request.json", self.request)
        args = type("Args", (), {"config": config, "request": payload, "dry_run": True})()
        with patch.object(Flash, "_check_models", return_value={}) as models, patch.object(
            Flash, "runtime"
        ) as runtime:
            dry = agent.play(args)
            self.assertFalse(dry["ok"])
            self.assertEqual(dry["results"][0]["error"]["code"], "PRODUCTION_APPROVAL_REQUIRED")
            result = self.flash.play(**self.request)
            self.assertEqual(result["error"]["code"], "PRODUCTION_APPROVAL_REQUIRED")
            models.assert_not_called()
            runtime.assert_not_called()

    def test_working_inside_a_registered_project_also_checks_external_output(self):
        request = {**self.request, "output": str(self.root / "external.mp4")}
        with patch.object(Path, "cwd", return_value=self.project), patch.object(
            self.flash, "_check_models", return_value={}
        ):
            result = self.flash.preflight(**request)
        self.assertEqual(result.get("error", {}).get("code"), "PRODUCTION_APPROVAL_REQUIRED")

    def test_images_and_unregistered_video_keep_the_existing_contract(self):
        with patch.object(self.flash, "_check_models", return_value={}):
            self.assertTrue(self.flash.preflight("t3", prompt="Study", output=self.project / "still.png")["ok"])
            self.assertTrue(self.flash.preflight(**{**self.request, "output": str(self.root / "raw.mp4")})["ok"])

    def test_false_approval_and_invalid_registration_fail_closed(self):
        with patch.object(self.flash, "_check_models", return_value={}):
            forged = self.flash.preflight(**self.request, production_approval=True)
            self.assertEqual(forged.get("error", {}).get("code"), "PRODUCTION_APPROVAL_INVALID")
            self.marker.write_text("{", encoding="utf-8")
            policy = self.flash.preflight(**self.request)
        self.assertEqual(policy.get("error", {}).get("code"), "PRODUCTION_POLICY_INVALID")

    @unittest.skipUnless(importlib.util.find_spec("xcard.CineSpindle"), "CineSpindle is locally installed")
    def test_real_shot_approval_validates_tone_stage_and_exact_reviewed_media(self):
        from xcard.CineSpindle.mindset.h3_preproduction import plan_h3_shot

        plan = plan_h3_shot({
            "shot_id": "SH01", "first_frame": {"path": "frame.png", "reason": "Face and table are clear."},
            "review": {"complex_motion": False, "first_frame_complete": True, "identity_clear": True,
                       "evidence": "The opening face and the table have been checked."},
        }, self.project, self.project / "crops", self.flash.tone_list())
        path = self.write(self.project / "shot.plan.json", plan)
        proof = {"plan": str(path), "stage": "final"}
        with patch.object(self.flash, "_check_models", return_value={}):
            approved = self.flash.preflight(**self.request, production_approval=proof)
            self.assertTrue(approved["ok"])
            self.assertEqual(approved["resolved"]["production_approval"]["plan_id"], plan["plan_id"])
            other = self.project / "other.png"
            Image.new("RGB", (64, 64), "blue").save(other)
            wrong_image = self.flash.preflight(**{**self.request, "image_in": str(other)}, production_approval=proof)
            self.assertEqual(wrong_image["error"]["code"], "PRODUCTION_APPROVAL_INVALID")
            wrong_tone = self.flash.preflight(**{**self.request, "tone": "t2"}, production_approval=proof)
            self.assertEqual(wrong_tone["error"]["code"], "PRODUCTION_APPROVAL_INVALID")
            Image.new("RGB", (64, 64), "red").save(self.frame)
            stale = self.flash.preflight(**self.request, production_approval=proof)
            self.assertEqual(stale["error"]["code"], "PRODUCTION_APPROVAL_INVALID")

    @unittest.skipUnless(importlib.util.find_spec("xcard.CineSpindle"), "CineSpindle is locally installed")
    def test_r2_final_requires_the_approved_motion_plate_after_motion_can_start(self):
        from xcard.CineSpindle.mindset.h3_preproduction import plan_h3_shot, review_h3_motion

        plan = plan_h3_shot({
            "shot_id": "SH02", "motion_goal": "Lift, carry, and place the cup.",
            "grid": {"path": "frame.png", "purpose": "storyboard", "layout": {"rows": 1, "columns": 1},
                     "cells": [{"shot_id": "SH02"}]},
            "selection": {"mode": "cells", "indices": [0], "reason": "Use the table position from the cell."},
            "review": {"complex_motion": True, "first_frame_complete": True, "identity_clear": True,
                       "evidence": "The hands and table are visible. The carry and set-down need a dynamic plate."},
        }, self.project, self.project / "crops", self.flash.tone_list())
        path = self.write(self.project / "motion.plan.json", plan)
        plate = self.project / "plate.mp4"
        plate.write_bytes(b"example motion")
        with patch.object(self.flash, "_check_models", return_value={}), patch(
            "engine.core.media.probe_video", return_value={"duration_s": 3}
        ):
            draft = self.flash.preflight("t1", prompt="Lift the cup", output=self.project / "draft.mp4",
                                         production_approval={"plan": str(path), "stage": "motion"})
            self.assertTrue(draft["ok"])
            final = {**self.request, "tone": "t6", "image_in": [plan["references"][0]["path"]],
                     "video_in": str(plate)}
            proof = {"plan": str(path), "stage": "final"}
            self.assertEqual(self.flash.preflight(**final, production_approval=proof)["error"]["code"],
                             "PRODUCTION_APPROVAL_INVALID")
            review = review_h3_motion(plan, plate, "passed", "The cup stays upright through the set-down.")
            proof["motion_review"] = str(self.write(self.project / "motion.review.json", review))
            approved = self.flash.preflight(**final, production_approval=proof)
            self.assertTrue(approved["ok"])
            self.assertEqual(approved["resolved"]["production_approval"]["stage"], "final")
            plate.write_bytes(b"changed motion")
            self.assertEqual(self.flash.preflight(**final, production_approval=proof)["error"]["code"],
                             "PRODUCTION_APPROVAL_INVALID")
