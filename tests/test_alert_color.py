"""
状态灯颜色判定单元测试（alert_color 纯函数）。
"""
import unittest

import config
import server


class TestAlertColor(unittest.TestCase):

    def test_offline_is_gray(self):
        self.assertEqual(
            server.MonitorServer.alert_color(False, 0.0, now=100.0),
            config.COLOR_OFFLINE,
        )
        # 离线即使最近有报警也是灰色
        self.assertEqual(
            server.MonitorServer.alert_color(False, 99.0, now=100.0),
            config.COLOR_OFFLINE,
        )

    def test_online_no_alert_is_green(self):
        self.assertEqual(
            server.MonitorServer.alert_color(True, 0.0, now=100.0),
            config.COLOR_NORMAL,
        )

    def test_recent_alert_is_red(self):
        self.assertEqual(
            server.MonitorServer.alert_color(True, 95.0, now=100.0, hold_seconds=10),
            config.COLOR_ALERT,
        )

    def test_alert_hold_window_recovers_to_green(self):
        # 超过保持窗口后自动恢复绿色（避免永久变红）
        self.assertEqual(
            server.MonitorServer.alert_color(True, 80.0, now=100.0, hold_seconds=10),
            config.COLOR_NORMAL,
        )

    def test_hold_window_boundary(self):
        # 恰好等于窗口时长不算红色（now - last < hold）
        self.assertEqual(
            server.MonitorServer.alert_color(True, 90.0, now=100.0, hold_seconds=10),
            config.COLOR_NORMAL,
        )

    def test_uses_config_default(self):
        # 未传 hold_seconds 时读取 config.ALERT_HOLD_SECONDS（默认 300）
        self.assertGreater(config.ALERT_HOLD_SECONDS, 0)
        # now - last = 0 < hold → 红色
        self.assertEqual(
            server.MonitorServer.alert_color(True, 1.0, now=1.0),
            config.COLOR_ALERT,
        )


if __name__ == "__main__":
    unittest.main()
