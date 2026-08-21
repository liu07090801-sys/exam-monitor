"""
配置加载与合并单元测试。
"""
import unittest

import config


class TestDeepMerge(unittest.TestCase):

    def test_override_wins(self):
        merged = config._deep_merge({"a": 1, "b": {"x": 1}}, {"a": 2})
        self.assertEqual(merged["a"], 2)
        self.assertEqual(merged["b"]["x"], 1)

    def test_nested_merge_keeps_defaults(self):
        merged = config._deep_merge(
            {"server": {"port": 8765, "token": "t"}},
            {"server": {"port": 9999}},
        )
        self.assertEqual(merged["server"]["port"], 9999)
        self.assertEqual(merged["server"]["token"], "t")

    def test_new_keys_added(self):
        merged = config._deep_merge({"a": 1}, {"b": 2})
        self.assertEqual(merged, {"a": 1, "b": 2})

    def test_config_exports_freeze_timeout(self):
        self.assertGreater(config.FREEZE_TIMEOUT, 0)

    def test_round3_config_exports(self):
        self.assertIsInstance(config.EXAM_ENABLED, bool)
        self.assertTrue(config.EXAM_URL_WHITELIST)
        self.assertIn("chrome.exe", config.EXAM_BROWSERS_LOWER)
        self.assertGreaterEqual(config.EXAM_GRACE_SECONDS, 0)
        self.assertIsInstance(config.EXAM_STRICT_URL_CHECK, bool)
        self.assertIn(config.CLIENT_LOCK_MODE, ("popup", "overlay"))
        self.assertIsInstance(config.TEACHER_PASSWORD, str)
        self.assertGreaterEqual(config.MAX_SCREENSHOTS_DISK, 0)


if __name__ == "__main__":
    unittest.main()
