import contextlib
import copy
import importlib

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine import Flash, bank
from engine.core import _Run
from PIL import Image


class ContractTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.flash = Flash({"comfyui_root": str(self.root)})
        self.image = self.root / "reference.png"
        Image.new("RGB", (8, 16)).save(self.image)

    def resolve(self, tone="t1", **overrides):
        spec = self.flash._spec(tone)
        args = dict(prompt="bird", image_in=None, audio_in=None, resolution=None,
                    duration=None, seed=123, output=self.root / ("out.mp4" if spec["output"] == "video" else "out.png"))
        advanced = {k: v for k, v in overrides.items() if k not in args}
        args.update((k, v) for k, v in overrides.items() if k in args)
        return self.flash._resolve(spec, **args, advanced=advanced)

    def test_catalog_exposes_contract_and_filesystem_independent_schema_hash(self):
        row = next(r for r in self.flash.tone_list() if r["tone"] == "t2")
        self.assertIn("schema_hash", row)
        info = self.flash.tone_info("t2")
        self.assertEqual(row["schema_hash"], info["schema_hash"])
        self.assertEqual(len(info["schema_hash"]), 64)
        self.assertTrue((Path(bank.__file__).parents[2] / info["prompt_guide_ref"]).is_file())
        self.assertEqual(info["inputs"]["fields"]["image_in"]["role"], "reference_image")
        schema = info["schema"]
        self.assertTrue({"tone","prompt","output","image_in"} <= set(schema["required"]))
        self.assertEqual(schema["properties"]["fps"]["const"], 24)
        self.assertIn(".mp4", info["outputs"]["formats"])
        for dep in info["models"]:
            p = Path(dep["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        self.assertTrue(self.flash.tone_info("t2")["models_ready"])
        self.assertEqual(self.flash.tone_info("t2")["schema_hash"], row["schema_hash"])

    def test_count_corrupt_file_fps_and_invalid_controls_fail_before_runtime(self):
        damaged = self.root / "broken.png"
        damaged.write_bytes(b"bad")
        cases = [
            ("t2", {"image_in": [self.image, self.image]}, "BAD_INPUT"),
            ("t4", {"image_in": [self.image, self.image]}, "BAD_INPUT"),
            ("t2", {"image_in": damaged, "resolution": "768x448"}, "BAD_INPUT"),
            ("t1", {"fps": 30}, "BAD_PARAM"),
            ("t1", {"scheduler": "pika", "steps": 12}, "BAD_PARAM"),
            ("t3", {"duration": 1}, "UNSUPPORTED_CONTROL"),
            ("t1", {"duration": "five"}, "BAD_DURATION"),
            ("t1", {"steps": True}, "BAD_PARAM"),
        ]
        for tone, options, code in cases:
            with self.subTest(tone=tone, options=options):
                with patch.object(self.flash, "runtime", side_effect=AssertionError("runtime started")):
                    out = self.root / ("out.mp4" if tone in ("t1","t2") else "out.png")
                    result = self.flash.play(tone, prompt="test", output=out, **options)
                    self.assertEqual(result["error"]["code"], code, result)
                    if isinstance(options.get("image_in"), list):
                        self.assertRegex(result["error"]["message"], r"2.*1|1.*2")

    def test_ref_path_list_and_tuple_all_resolve(self):
        for ref in (self.image, str(self.image), [str(self.image)], (self.image,)):
            with self.subTest(ref=ref):
                self.assertEqual(self.resolve("t4", image_in=ref)["images"], [str(self.image)])

    def test_canvas_limits_and_named_sizes_in_schema_are_enforced(self):
        spec = copy.deepcopy(self.flash._spec("t2"))
        spec["controls"]["resolution"]["max_pixels"] = 256 * 256
        args = dict(prompt="test", image_in=self.image, audio_in=None, resolution="512x512",
                    duration=None, seed=1, output=self.root/"out.mp4", advanced={})
        from engine.core import ParamError
        with self.assertRaises(ParamError) as err:
            self.flash._resolve(spec, **args)
        self.assertEqual(err.exception.code, "BAD_RESOLUTION")
        info = self.flash.tone_info("t2")
        canvas = info["schema"]["properties"]["resolution"]["x-canvas"]
        self.assertIn("21:9", canvas["sizes"])
        self.assertEqual(self.resolve("t2", image_in=self.image, resolution="21:9")["width"], canvas["sizes"]["21:9"][0])

    def test_unusable_output_paths_fail_before_model_check(self):
        forbidden_parent = self.root/"existing_file"
        forbidden_parent.touch()
        paths = [forbidden_parent/"test.mp4", str(self.root/"bad\0name.mp4")]
        if sys.platform == "win32":
            paths.extend([self.root/"CON.mp4", self.root/"bad?.mp4", self.root/"reserved:"/"out.mp4"])
        for output in paths:
            with self.subTest(output=output):
                result = self.flash.play("t1", prompt="test", output=output)
                self.assertEqual(result["error"]["code"], "BAD_OUTPUT", result)

    def test_json_schema_validation_agrees_with_canonical_sampling_requests(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("optional independent JSON Schema validator unavailable")
        schema = self.flash.tone_info("t1")["schema"]
        validator = Draft202012Validator(schema)
        request = dict(tone="t1", prompt="test", output=str(self.root/"out.mp4"))
        for knobs, valid in [
            ({"scheduler":"pika","sampler":"euler"}, False), ({"scheduler":"pika","steps":4}, False),
            ({"steps":18,"cfg":5,"sampler":"euler"}, True), ({"steps":3}, False), ({"cfg":0.9}, False),
            ({"sampler":"lcm","cfg":2}, True), ({"fps":25}, False), ({"unknown":1}, False),
            ({"duration":0.9},False), ({"steps":False}, False),
        ]:
            with self.subTest(knobs=knobs):
                self.assertEqual(validator.is_valid({**request, **knobs}), valid)
                result = self.flash.play(**request, **knobs)
                self.assertEqual(result["error"]["code"] == "MISSING_MODEL", valid)

    def test_effective_pika_and_explicit_v01_sampling(self):
        for given, expected in [
            ({}, ("pika", "lcm", 6)),
            ({"fps": 24}, ("pika", "lcm", 6)),
            ({"cfg": 1}, ("simple", "res_multistep", 20)),
            ({"steps": 6}, ("simple", "res_multistep", 6)),
            ({"sampler": "lcm"}, ("pika", "lcm", 6)),
            ({"sampler": "lcm", "steps": 8}, ("simple","lcm",8)),
            ({"sampler": "euler"}, ("simple","euler",20)),
            ({"scheduler": "beta"}, ("beta","res_multistep",20)),
            ({"scheduler": "pika", "cfg": 2}, ("pika","lcm",6)),
            ({"lora_strength": 0.5}, ("pika","lcm",6)),
        ]:
            with self.subTest(given=given):
                params = self.resolve(**given)["advanced"]
                self.assertEqual(tuple(params[k] for k in ("scheduler","sampler","steps")), expected)

    def test_preflight_checks_models_and_reports_resolved_grid_without_runtime(self):
        with patch.object(self.flash, "runtime", side_effect=AssertionError("runtime started")):
            request = dict(tone="t1", prompt="test", output=self.root/"out.mp4", duration=5)
            self.assertTrue(hasattr(self.flash, "preflight"))
            self.assertEqual(self.flash.preflight(**request)["error"]["code"], "MISSING_MODEL")
            for dep in self.flash.tone_info("t1")["models"]:
                path = Path(dep["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            r = self.flash.preflight(**request)
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["resolved"]["requested_frame_count"], 120)
            self.assertEqual(r["resolved"]["aligned_frame_count"], 124)

    def test_discovered_video_with_its_own_grid_and_canvas(self):
        spec = {"id": "testgrid", "name":"Example", "version":"1", "schema_version":"0.2",
                "purpose":"grid", "output":"video","inputs":{"required":["prompt"], "optional":[]},
                "controls":{"prompt":{"type":"string"}, "resolution":{"type":"string", "default":"10x14"},
                            "duration":{"type":"number","default":1,"min":0.1,"max":2}},
                "outputs":{"media_type":"video","formats":[".mp4"]}, "models":{},
                "temporal":{"fps":10,"frame_step":4,"frame_offset":1,"min_frames":1,"max_frames":17,
                            "duration_policy":"round_then_ceil_to_grid"}}
        (self.root/"t_testgrid.py").write_text("TONE = " + repr(spec) + "\ndef play(run): return run.output\n", encoding="utf-8")
        self.addCleanup(sys.modules.pop, "engine.bank.t_testgrid", None)
        with patch.object(bank, "__path__", [*bank.__path__, str(self.root)]):
            importlib.invalidate_caches()
            with patch.dict(bank.TONES, bank._discover()):
                self.assertIn("testgrid", {row["tone"] for row in self.flash.tone_list()})
                self.assertIn("schema", self.flash.tone_info("testgrid"))
                result = self.resolve("testgrid")
                self.assertEqual((result["width"],result["height"],result["fps"],result["frames"]), (10,14,10,13))
                denied = self.flash.play("testgrid",prompt="hi",duration=1.95,output=self.root/"out.mp4")
                self.assertEqual(denied["error"]["code"], "BAD_DURATION")

    def test_decoded_and_delivered_counts_include_trim_and_shortfall(self):
        import numpy as np
        class Frames(np.ndarray):
            def dim(self):
                return self.ndim
        runtime = Mock(dynamic_vram=True)
        runtime.stage.side_effect = contextlib.nullcontext
        for count, delivered, trimmed in [(126,124,2), (122,122,0)]:
            with self.subTest(decoded=count), patch.object(self.flash, "runtime", return_value=runtime):
                runtime.decode.return_value = np.zeros((count,1,1,3)).view(Frames)
                run = _Run(self.flash, self.flash._spec("t1"), self.resolve(duration=5))
                frames = run.decode_frames(None, {"samples": None})
                with patch("engine.media.save_video", return_value="out.mp4"):
                    run.save_video(frames)
                meta = run.meta
                self.assertEqual((meta.get("requested_duration_s"), meta.get("requested_frame_count")), (5,120))
                self.assertEqual((meta["decoded_frame_count"],meta["actual_frame_count"], meta["trimmed_frame_count"]), (count,delivered,trimmed))
                self.assertEqual((meta["frames"],meta["fps"]), (delivered,24))
                self.assertAlmostEqual(meta["actual_duration_s"], delivered/24, places=5)
