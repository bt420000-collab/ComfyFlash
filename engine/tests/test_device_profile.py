import subprocess
import unittest
from unittest.mock import patch

from engine.device_profile import build_device_profiles, decide_device, nvidia_telemetry


def gpu(index, free_mib, **fields):
    return {"index": index, "name": f"GPU {index}", "total_mib": 24576,
            "free_mib": free_mib, "ready": True, **fields}


def profiles(*gpus, free_ram_mib=24 * 1024, inventory=()):
    return build_device_profiles({"gpus": gpus, "ram_mib": 64 * 1024,
                                  "free_ram_mib": free_ram_mib}, inventory=inventory)


class DeviceProfileTests(unittest.TestCase):
    def test_profile_joins_telemetry_by_uuid_and_preserves_the_smaller_free_measurement(self):
        first, second = profiles(
            gpu(0, 15000, uuid="AAAA"), gpu(1, 13000),
            inventory=[{"index": 1, "uuid": "GPU-aaaa", "free_mib": 14000,
                        "utilization_pct": 0, "memory_utilization_pct": 22,
                        "driver_version": "570.00"}],
        )
        self.assertEqual((first["device"], first["free_mib"], first["utilization_pct"]),
                         ("cuda:0", 14000, 0))
        self.assertEqual(first["driver_version"], "570.00")
        self.assertEqual(first["free_ram_mib"], 24 * 1024)
        self.assertIsNone(first["model_cache"])
        self.assertIn("model_cache", first["unavailable_fields"])
        self.assertEqual(second["free_mib"], 13000)
        self.assertIsNone(second["utilization_pct"])
        self.assertIn("uuid", second["unavailable_fields"])

    def test_optional_nvidia_samples_preserve_available_fields_when_load_is_unavailable(self):
        output = "GPU-a, 15780, 67, 12, 570.00\nGPU-b, [N/A], N/A, 0, 570.00\n"
        result = subprocess.CompletedProcess([], 0, output)
        with patch("engine.device_profile.subprocess.run", return_value=result):
            first, second = nvidia_telemetry()
        self.assertEqual((first["uuid"], first["free_mib"], first["utilization_pct"]),
                         ("GPU-a", 15780, 67))
        self.assertEqual(second["driver_version"], "570.00")
        self.assertIsNone(second["free_mib"])
        self.assertIsNone(second["utilization_pct"])
        self.assertEqual(second["memory_utilization_pct"], 0)

    def test_optional_nvidia_samples_can_be_unavailable(self):
        with patch("engine.device_profile.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(nvidia_telemetry(), [])


class DeviceDecisionTests(unittest.TestCase):
    def test_pinned_selection_stays_on_requested_device(self):
        observed = profiles(gpu(0, 14500), gpu(1, 23000))
        decision = decide_device(observed, preferred="cuda:0", min_free_vram_mib=14000)
        self.assertEqual(decision["selected_device"], "cuda:0")
        self.assertEqual(decision["decision_reason"], "explicit_device")
        self.assertFalse(decision["fallback_used"])
        self.assertIn("device_pinned", decision["rejected_devices"][0]["reasons"])

    def test_ineligible_pinned_selection_cannot_hop_to_another_device(self):
        observed = profiles(gpu(0, 9000), gpu(1, 23000))
        decision = decide_device(observed, preferred="cuda:0", min_free_vram_mib=14000)
        self.assertIsNone(decision["selected_device"])
        self.assertFalse(decision["fallback_used"])
        self.assertIn("insufficient_free_vram", decision["rejected_devices"][0]["reasons"])

    def test_automatic_selection_rejects_devices_below_the_current_free_vram_floor(self):
        observed = profiles(gpu(0, 9000, total_mib=55000), gpu(1, 16000),
                            gpu(2, 23000, ready=False), gpu(3, None))
        decision = decide_device(observed, preferred="cuda:0", auto_schedule=True,
                                 min_free_vram_mib=14000)
        self.assertEqual(decision["selected_device"], "cuda:1")
        self.assertTrue(decision["fallback_used"])
        self.assertEqual(decision["profile_confidence"], "medium")
        rejected = {entry["device"]: entry["reasons"] for entry in decision["rejected_devices"]}
        self.assertIn("insufficient_free_vram", rejected["cuda:0"])
        self.assertIn("cuda_unavailable", rejected["cuda:2"])
        self.assertIn("free_vram_unavailable", rejected["cuda:3"])

    def test_automatic_selection_rejects_insufficient_or_unknown_available_ram(self):
        for free_ram, reason in ((12000, "insufficient_free_ram"), (None, "free_ram_unavailable")):
            with self.subTest(free_ram=free_ram):
                decision = decide_device(profiles(gpu(0, 18000), free_ram_mib=free_ram),
                                         auto_schedule=True, min_free_ram_mib=16000)
                self.assertIsNone(decision["selected_device"])
                self.assertIn(reason, decision["rejected_devices"][0]["reasons"])

    def test_automatic_tie_breaker_uses_observed_load_and_stable_index(self):
        observed = profiles(gpu(3, 16000, utilization_pct=2), gpu(2, 16000, utilization_pct=2),
                            gpu(1, 16000, utilization_pct=70), gpu(0, 16000))
        decision = decide_device(observed, auto_schedule=True)
        self.assertEqual(decision["selected_device"], "cuda:2")
        self.assertFalse(decision["fallback_used"])
        self.assertEqual(decide_device(observed, auto_schedule=True, candidate_indices={1})["selected_device"],
                         "cuda:1")

    def test_missing_pinned_device_is_reported_explicitly(self):
        decision = decide_device(profiles(gpu(0, 19000)), preferred="cuda:5")
        self.assertIsNone(decision["selected_device"])
        self.assertIn({"device": "cuda:5", "reasons": ["device_not_visible"]}, decision["rejected_devices"])
