"""
窗口白名单匹配单元测试。
"""
import unittest

import client
import config


class TestWindowAllow(unittest.TestCase):

    def setUp(self):
        self._orig_whitelist = config.WHITE_LIST
        self._orig_procs = config.WHITELIST_PROCESSES

    def tearDown(self):
        config.WHITE_LIST = self._orig_whitelist
        config.WHITELIST_PROCESSES = self._orig_procs

    def test_title_whitelist_keyword_match(self):
        config.WHITE_LIST = ["洛谷"]
        config.WHITELIST_PROCESSES = []
        self.assertTrue(client.is_window_allowed("洛谷 - 刷题", ""))
        self.assertTrue(client.is_window_allowed("我的洛谷题目", ""))

    def test_title_not_in_whitelist(self):
        config.WHITE_LIST = ["洛谷"]
        config.WHITELIST_PROCESSES = []
        self.assertFalse(client.is_window_allowed("记事本", ""))

    def test_empty_title_not_allowed(self):
        config.WHITE_LIST = ["洛谷"]
        config.WHITELIST_PROCESSES = []
        self.assertFalse(client.is_window_allowed("", ""))

    def test_process_whitelist(self):
        config.WHITE_LIST = []
        config.WHITELIST_PROCESSES = ["exam.exe"]
        self.assertTrue(client.is_window_allowed("任意标题", "exam.exe"))
        self.assertTrue(client.is_window_allowed("任意标题", "EXAM.EXE"))
        self.assertFalse(client.is_window_allowed("任意标题", "chrome.exe"))

    def test_title_match_without_process(self):
        # 进程名为空时仅看标题
        config.WHITE_LIST = ["考试"]
        config.WHITELIST_PROCESSES = ["exam.exe"]
        self.assertTrue(client.is_window_allowed("在线考试", ""))


if __name__ == "__main__":
    unittest.main()
