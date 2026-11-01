import unittest
from unittest.mock import patch

from mainline_agent.hardware import Hardware, detect_hardware, select_model


class HardwareTests(unittest.TestCase):
    def mac(self, ram):
        return Hardware("Darwin", "arm64", ram, True)

    def test_mac_tiers(self):
        for ram, tier, tag, ctx in [(16, "low", "qwen2.5-coder:7b", 8192),
                                    (24, "medium", "qwen2.5-coder:14b", 16384),
                                    (32, "medium", "qwen2.5-coder:14b", 16384),
                                    (48, "high", "qwen3-coder:30b", 32768),
                                    (64, "high", "qwen3-coder:30b", 32768)]:
            with self.subTest(ram=ram):
                plan = select_model(self.mac(ram))
                self.assertEqual((plan["tier"], plan["model"], plan["context"]), (tier, tag, ctx))

    def test_dedicated_gpu_not_added_to_ram(self):
        for vram, tier in [(8, "low"), (12, "low"), (16, "medium"), (24, "medium"), (32, "high")]:
            with self.subTest(vram=vram):
                self.assertEqual(select_model(Hardware("Linux", "x86_64", 64, False, vram))["tier"], tier)

    def test_small_memory_and_invalid_numbers_fail(self):
        for ram in (8, 0, -1, float("nan"), float("inf")):
            with self.subTest(ram=ram), self.assertRaises(ValueError):
                select_model(self.mac(ram))

    def test_context_and_cloud_guards(self):
        for context in (1024, 32768):
            with self.assertRaises(ValueError):
                select_model(self.mac(16), context=context)
        with self.assertRaises(ValueError):
            select_model(self.mac(16), override="qwen-cloud")
        self.assertEqual(select_model(self.mac(16), override="custom:local")["model"], "custom:local")

    @patch("mainline_agent.hardware._output", return_value=str(16 * 1024 ** 3))
    @patch("mainline_agent.hardware.platform.machine", return_value="arm64")
    @patch("mainline_agent.hardware.platform.system", return_value="Darwin")
    def test_mac_detection(self, *_):
        detected = detect_hardware()
        self.assertEqual(detected.ram_gb, 16)
        self.assertTrue(detected.unified_memory)
