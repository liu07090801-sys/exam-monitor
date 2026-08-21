"""
冻结检测与在线判定单元测试。
冻结语义：在线但超过 freeze_timeout 秒未收到截图帧（而非相邻帧哈希相同）。
"""
import unittest
import time

import server


class TestFreezeCheck(unittest.TestCase):

    def _client(self, online=True, last_shot=0.0, logged=False):
        c = server.ClientInfo("test001")
        c.is_online = online
        c.last_screenshot_ts = last_shot
        c.freeze_logged = logged
        return c

    def test_no_screenshot_yet_not_freeze(self):
        # 还没收到过截图（last_screenshot_ts=0）不判冻结
        c = self._client(online=True, last_shot=0.0)
        self.assertFalse(c.freeze_check(90, now=time.time()))

    def test_offline_not_freeze(self):
        c = self._client(online=False, last_shot=time.time() - 200)
        self.assertFalse(c.freeze_check(90, now=time.time()))

    def test_recent_screenshot_not_freeze(self):
        now = time.time()
        c = self._client(online=True, last_shot=now - 10)
        self.assertFalse(c.freeze_check(90, now=now))

    def test_freeze_reports_once_then_silent(self):
        now = time.time()
        c = self._client(online=True, last_shot=now - 200)
        self.assertTrue(c.freeze_check(90, now=now))       # 首次报警
        self.assertFalse(c.freeze_check(90, now=now + 1))  # 已记录，不重复
        self.assertFalse(c.freeze_check(90, now=now + 2))  # 持续静默

    def test_freeze_resets_after_new_frame(self):
        now = time.time()
        c = self._client(online=True, last_shot=now - 200)
        self.assertTrue(c.freeze_check(90, now=now))
        # 收到新帧
        c.last_screenshot_ts = now + 5
        self.assertFalse(c.freeze_check(90, now=now + 6))
        # 再次长时间无帧，应能再次报警
        self.assertTrue(c.freeze_check(90, now=now + 200))


class TestCheckOnline(unittest.TestCase):

    def test_heartbeat_timeout_marks_offline(self):
        c = server.ClientInfo("test002")
        c.last_heartbeat = time.time() - 100
        self.assertFalse(c.check_online(15))

    def test_recent_heartbeat_stays_online(self):
        c = server.ClientInfo("test003")
        c.last_heartbeat = time.time()
        self.assertTrue(c.check_online(15))

    def test_update_heartbeat_revives(self):
        c = server.ClientInfo("test004")
        c.last_heartbeat = time.time() - 100
        c.check_online(15)
        self.assertFalse(c.is_online)
        c.update_heartbeat()
        self.assertTrue(c.is_online)


if __name__ == "__main__":
    unittest.main()
