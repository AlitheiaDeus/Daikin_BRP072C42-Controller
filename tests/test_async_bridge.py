"""
Automated unit & regression tests for Asynchronous uasyncio Daikin Bridge.
Validates configuration integrity, data structures, protocol translations,
coroutine signatures, and memory patterns for MicroPython ESP32 deployment.
"""

import sys
import os
import inspect
import unittest

# Add micropython directory to search path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(repo_root, "micropython"))

import config
import daikin
import discover
import main


class TestDaikinAsyncArchitecture(unittest.TestCase):
    def setUp(self):
        self.dev = daikin.DaikinController(
            device_id="ac_test",
            name="Test AC",
            ip="192.168.1.99",
            key="0123456789012"
        )

    def test_config_defaults(self):
        """Verify configuration provides 4 default Daikin endpoints and correct timing intervals."""
        self.assertEqual(len(config.DEFAULT_DEVICES), 4)
        self.assertEqual(config.HTTP_PORT, 80)
        self.assertEqual(config.GC_INTERVAL_SECS, 30)
        self.assertEqual(config.SOCKET_TIMEOUT_SECS, 5.0)
        self.assertEqual(config.POLL_CYCLE_TARGET_SECS, 30.0)
        self.assertEqual(config.POLL_STAGGER_MIN_SECS, 3.0)

        # Check endpoints
        expected_ids = ["ac_1", "ac_2", "ac_3", "ac_4"]
        actual_ids = [d["id"] for d in config.DEFAULT_DEVICES]
        self.assertEqual(actual_ids, expected_ids)

    def test_default_state_structure(self):
        """Verify default state dictionary contains all required telemetry keys for frontend UI."""
        state = self.dev.get_default_state()
        self.assertEqual(state["id"], "ac_test")
        self.assertEqual(state["name"], "Test AC")
        self.assertEqual(state["ip"], "192.168.1.99")
        self.assertTrue(state["configured"])
        self.assertIn("power", state)
        self.assertIn("mode", state)
        self.assertIn("target_temp", state)
        self.assertIn("indoor_temp", state)
        self.assertIn("outdoor_temp", state)
        self.assertIn("supported_modes", state)
        self.assertIn("fan_rate", state)
        self.assertIn("fan_dir", state)
        self.assertIn("compressor_freq", state)

    def test_mode_mappings(self):
        """Verify mode translations between human labels and Daikin hardware codes."""
        self.assertEqual(daikin.MODE_MAP["cool"], "3")
        self.assertEqual(daikin.MODE_MAP["heat"], "4")
        self.assertEqual(daikin.MODE_MAP["dry"], "2")
        self.assertEqual(daikin.MODE_MAP["fan"], "6")
        self.assertEqual(daikin.MODE_MAP["auto"], "1")

        self.assertEqual(daikin.MODE_REV_MAP["3"], "cool")
        self.assertEqual(daikin.MODE_REV_MAP["4"], "heat")
        self.assertEqual(daikin.MODE_REV_MAP["2"], "dry")
        self.assertEqual(daikin.MODE_REV_MAP["6"], "fan")

    def test_unquote_url_decoder(self):
        """Verify custom percent-encoding decoder without urllib dependency."""
        self.assertEqual(daikin.unquote("Living%20Room"), "Living Room")
        self.assertEqual(daikin.unquote("%21%40%23"), "!@#")
        self.assertEqual(daikin.unquote("NoEncoding"), "NoEncoding")
        self.assertEqual(daikin.unquote(""), "")

    def test_parse_temp(self):
        """Verify temperature parser handles Daikin dash/null values correctly."""
        self.assertEqual(daikin._parse_temp("24.5"), 24.5)
        self.assertEqual(daikin._parse_temp("--", default=25.0), 25.0)
        self.assertEqual(daikin._parse_temp("M", default=22.0), 22.0)
        self.assertEqual(daikin._parse_temp(None, default=26.0), 26.0)

    def test_generate_uuid(self):
        """Verify UUID generator produces 32-character hexadecimal strings."""
        u1 = daikin.generate_uuid()
        u2 = daikin.generate_uuid()
        self.assertEqual(len(u1), 32)
        self.assertEqual(len(u2), 32)
        self.assertNotEqual(u1, u2)

    def test_manager_and_in_memory_cache(self):
        """Verify DaikinDeviceManager correctly initializes units and in-memory app_state cache."""
        self.assertGreaterEqual(len(main.app_state["units"]), 4)
        for dev_id, state in main.app_state["units"].items():
            self.assertIn("id", state)
            self.assertIn("status", state)
            self.assertIn("configured", state)

    def test_coroutine_signatures(self):
        """Verify that all network and server routines are non-blocking coroutines."""
        # Main entry points & background tasks
        self.assertTrue(inspect.iscoroutinefunction(main.client_handler))
        self.assertTrue(inspect.iscoroutinefunction(main.poll_units_task))
        self.assertTrue(inspect.iscoroutinefunction(main.gc_task))
        self.assertTrue(inspect.iscoroutinefunction(main.main))
        self.assertTrue(inspect.iscoroutinefunction(main.send_json))
        self.assertTrue(inspect.iscoroutinefunction(main.send_cors))
        self.assertTrue(inspect.iscoroutinefunction(main.serve_file))

        # Daikin engine routines
        self.assertTrue(inspect.iscoroutinefunction(daikin.async_daikin_request))
        self.assertTrue(inspect.iscoroutinefunction(daikin.DaikinController.get_full_state))
        self.assertTrue(inspect.iscoroutinefunction(daikin.DaikinController.update_settings))
        self.assertTrue(inspect.iscoroutinefunction(daikin.DaikinController.register))
        self.assertTrue(inspect.iscoroutinefunction(daikin.DaikinController.query_endpoint))
        self.assertTrue(inspect.iscoroutinefunction(daikin.DaikinController._request))
        self.assertTrue(inspect.iscoroutinefunction(daikin.DaikinDeviceManager.poll_all))

        # UDP discovery
        self.assertTrue(inspect.iscoroutinefunction(discover.async_discover_units))

    def test_mime_type_resolution(self):
        """Verify HTTP server accurately resolves MIME types for embedded assets."""
        self.assertIn("text/html", main.get_mime_type("index.html"))
        self.assertIn("text/css", main.get_mime_type("style.css"))
        self.assertIn("application/javascript", main.get_mime_type("app.js"))
        self.assertIn("application/json", main.get_mime_type("api.json"))


if __name__ == "__main__":
    unittest.main()
