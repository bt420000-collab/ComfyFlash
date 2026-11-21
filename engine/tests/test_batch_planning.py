from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import os
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from engine import agent


class BatchPlanningTests(unittest.TestCase):
    def test_similar_workloads_keep_identical_models_on_each_worker(self):
        requests = [{"tone": tone, "resolution": "768x448", "duration": 1.6}
                    for tone in ("t1", "t1", "t2", "t2")]
        self.assertEqual(agent.batch_layout(requests, 2), [[0, 1], [2, 3]])

    def test_different_duration_or_media_retains_balanced_round_robin(self):
        requests = [{"tone": tone, "resolution": "768x448"} for tone in ("t1", "t1", "t3", "t3")]
        self.assertEqual(agent.batch_layout(requests, 2), [[0, 2], [1, 3]])
        requests[2] = {"tone": "t2", "resolution": "768x448", "duration": 5}
        self.assertEqual(agent.batch_layout(requests[:3], 2), [[0, 2], [1]])

    def test_ram_admission_caps_workers_and_delays_constrained_parallel_starts(self):
        self.assertEqual(agent.admit_workers(2, 23 * 1024), (1, 0))
        seats, delay_s = agent.admit_workers(3, 30 * 1024)
        self.assertEqual(seats, 2)
        self.assertGreater(delay_s, 0)
        self.assertEqual(agent.admit_workers(2, 40 * 1024), (2, 0))

    def test_batch_dependency_preserves_order_and_result_identity(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            agent.write_text(config, agent.json_text({"comfyui_root": folder}))
            outputs = [str(root / f"{i}.png") for i in range(3)]
            items = [dict(tone="t4", output=outputs[0], resolution="1k", request_id="first"),
                     dict(tone="t3", output=outputs[1], resolution="1k", request_id="producer"),
                     dict(tone="t4", output=outputs[2], resolution="1k", request_id="consumer",
                          image_in={"media_type": "image", "path": outputs[1]})]
            paths = [root / f"{i}.json" for i in range(3)]
            for path, item in zip(paths, items):
                agent.write_text(path, agent.json_text(item))
            gpu = {"index": 0, "free_mib": 15000, "total_mib": 16000, "ready": True}
            hardware = {"active_devices": [0, 1], "gpus": [gpu], "free_ram_mib": 40 * 1024}
            with patch.object(agent, "hardware_report", return_value=hardware), patch.object(agent, "probe_python"), patch.object(
                agent, "worker_results", return_value=[(i, {"ok": True}) for i in range(3)]
            ) as worker:
                result = agent.play(argparse.Namespace(config=config, request=paths))
            worker.assert_called_once()
            self.assertEqual(worker.call_args.args[1], list(enumerate(paths)))
            self.assertFalse(result["parallel"])
            self.assertEqual([r["request_id"] for r in result["results"]], ["first", "producer", "consumer"])

    def test_failed_video_invalidates_a_previous_tail_image(self):
        with TemporaryDirectory() as folder:
            video = Path(folder) / "video.mp4"
            tail = Path(f"{video}.last.png")
            tail.touch()
            flash = Mock()
            flash.play.return_value = {"ok": False, "error": {"code": "BAD_OUTPUT"}}
            requests = [{"tone": "t1", "output": str(video)}, {"tone": "t4", "image_in": str(tail)}]
            with patch.object(agent, "Flash", return_value=nullcontext(flash)):
                results = agent.render_requests(None, requests)
            flash.play.assert_called_once()
            self.assertEqual(results[1]["error"]["code"], "DEPENDENCY_FAILED")

    def test_batch_uses_logical_zero_when_parent_mask_pins_one_physical_card(self):
        hardware = {"gpus": [{"index": 0, "ready": True, "uuid": "GPU-bravo", "free_mib": 15100,
                               "total_mib": 16000}], "active_devices": []}
        profiles = agent.build_device_profiles(hardware)
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "GPU-bravo"}):
            decisions, _ = agent.schedule({"device": "cuda:1"}, hardware, profiles, 2)
        self.assertEqual(decisions[0]["selected_device"], "cuda:0")
        self.assertEqual(decisions[0]["configured_device"], "cuda:1")
