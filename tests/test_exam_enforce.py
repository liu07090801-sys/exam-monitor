"""
考试网址 + 全屏强制判定单元测试（纯函数，不依赖 Win32/浏览器）。
"""
import time
import unittest

import client
import config


class TestUrlWhitelist(unittest.TestCase):

    def setUp(self):
        self._old = config.EXAM_URL_WHITELIST

    def tearDown(self):
        config.EXAM_URL_WHITELIST = self._old

    def test_domain_matches_subdomains(self):
        config.EXAM_URL_WHITELIST = ["luogu.com.cn"]
        self.assertTrue(client.is_url_allowed("https://www.luogu.com.cn/contest/123"))
        self.assertTrue(client.is_url_allowed("http://luogu.com.cn/"))
        self.assertTrue(client.is_url_allowed("https://www.luogu.com.cn/problem/P1001"))

    def test_domain_does_not_match_lookalike(self):
        config.EXAM_URL_WHITELIST = ["luogu.com.cn"]
        self.assertFalse(client.is_url_allowed("https://evil-luogu.com.cn.evil.com/x"))
        self.assertFalse(client.is_url_allowed("https://notluogu.com.cn/"))

    def test_path_prefix_entry(self):
        config.EXAM_URL_WHITELIST = ["luogu.com.cn/contest"]
        self.assertTrue(client.is_url_allowed("https://www.luogu.com.cn/contest/123/submit"))
        self.assertFalse(client.is_url_allowed("https://www.luogu.com.cn/problem/P1001"))

    def test_path_prefix_boundary(self):
        # /contest 不应匹配 /contest2
        config.EXAM_URL_WHITELIST = ["luogu.com.cn/contest"]
        self.assertFalse(client.is_url_allowed("https://www.luogu.com.cn/contest2/evil"))
        self.assertTrue(client.is_url_allowed("https://www.luogu.com.cn/contest"))
        self.assertTrue(client.is_url_allowed("https://www.luogu.com.cn/contest/"))

    def test_empty_or_non_url(self):
        config.EXAM_URL_WHITELIST = ["luogu.com.cn"]
        self.assertFalse(client.is_url_allowed(""))
        self.assertFalse(client.is_url_allowed("   "))
        self.assertFalse(client.is_url_allowed("搜索框里的文字"))

    def test_case_insensitive(self):
        config.EXAM_URL_WHITELIST = ["LUOGU.COM.CN/CONTEST"]
        self.assertTrue(client.is_url_allowed("https://www.luogu.com.cn/contest/1"))


class TestClassifyWindow(unittest.TestCase):
    OWN = 12345

    def setUp(self):
        self._old = (
            config.EXAM_ENABLED, config.EXAM_BROWSERS_LOWER,
            config.EXAM_FULLSCREEN_REQUIRED, config.EXAM_STRICT_URL_CHECK,
            config.WHITE_LIST, config.WHITELIST_PROCESSES,
            config.EXAM_URL_WHITELIST,
        )
        config.EXAM_ENABLED = True
        config.EXAM_BROWSERS_LOWER = ("chrome.exe", "msedge.exe")
        config.EXAM_FULLSCREEN_REQUIRED = True
        config.EXAM_STRICT_URL_CHECK = False
        config.WHITE_LIST = ["洛谷"]
        config.WHITELIST_PROCESSES = []
        config.EXAM_URL_WHITELIST = ["luogu.com.cn"]

    def tearDown(self):
        (config.EXAM_ENABLED, config.EXAM_BROWSERS_LOWER,
         config.EXAM_FULLSCREEN_REQUIRED, config.EXAM_STRICT_URL_CHECK,
         config.WHITE_LIST, config.WHITELIST_PROCESSES,
         config.EXAM_URL_WHITELIST) = self._old

    def _v(self, **kw):
        base = dict(own_hwnd=self.OWN, hwnd=1, title="洛谷 - 考试",
                    proc_name="chrome.exe", now=time.time(), grace_until=0.0,
                    url="", fullscreen=False)
        base.update(kw)
        return client.classify_window(base["own_hwnd"], base["hwnd"], base["title"],
                                      base["proc_name"], base["now"], base["grace_until"],
                                      base["url"], base["fullscreen"])

    def test_self_window_exempt(self):
        v = self._v(hwnd=self.OWN, title="考试监控客户端", proc_name="python.exe")
        self.assertEqual(v.status, "self")

    def test_desktop_exempt(self):
        v = self._v(title="", proc_name="explorer.exe")
        self.assertEqual(v.status, "desktop")

    def test_browser_fullscreen_allowed_url(self):
        v = self._v(url="https://www.luogu.com.cn/contest/1", fullscreen=True)
        self.assertEqual(v.status, "allowed")
        self.assertTrue(v.is_browser)

    def test_browser_not_fullscreen_violation(self):
        v = self._v(url="https://www.luogu.com.cn/contest/1", fullscreen=False)
        self.assertEqual(v.status, "violation")
        self.assertIn("未全屏", v.reason)

    def test_browser_wrong_url_violation(self):
        v = self._v(url="https://www.baidu.com", fullscreen=True)
        self.assertEqual(v.status, "violation")
        self.assertIn("网址不符", v.reason)

    def test_grace_period_allows(self):
        v = self._v(url="", fullscreen=False, grace_until=time.time() + 60)
        self.assertEqual(v.status, "allowed")
        self.assertIn("准备", v.reason)

    def test_unreadable_url_strict_violation(self):
        config.EXAM_STRICT_URL_CHECK = True
        v = self._v(url="", fullscreen=True, title="百度一下")
        self.assertEqual(v.status, "violation")
        self.assertIn("网址", v.reason)

    def test_unreadable_url_falls_back_to_title(self):
        config.EXAM_STRICT_URL_CHECK = False
        v = self._v(url="", fullscreen=True, title="洛谷 - 考试")
        self.assertEqual(v.status, "allowed")
        v2 = self._v(url="", fullscreen=True, title="百度一下")
        self.assertEqual(v2.status, "violation")

    def test_non_browser_window_uses_whitelist(self):
        v = self._v(proc_name="notepad.exe", title="记事本")
        self.assertEqual(v.status, "violation")
        config.WHITELIST_PROCESSES = ["exam.exe"]
        v2 = self._v(proc_name="exam.exe", title="考试系统")
        self.assertEqual(v2.status, "allowed")


class TestPickUrlFromEdits(unittest.TestCase):
    """地址栏候选选取：页面编辑框里的 URL 文本不得覆盖真实地址栏。"""

    def test_address_bar_named_wins(self):
        edits = [
            ("代码编辑器", "https://leetcode.com/problems/1"),
            ("Address and search bar", "https://www.luogu.com.cn/contest/1"),
        ]
        self.assertEqual(
            client._pick_url_from_edits(edits),
            "https://www.luogu.com.cn/contest/1",
        )

    def test_page_url_text_used_as_fallback(self):
        # 没有地址栏命名的控件时，退回任一像 URL 的值
        edits = [("代码编辑器", "https://www.baidu.com")]
        self.assertEqual(client._pick_url_from_edits(edits), "https://www.baidu.com")

    def test_address_named_non_url(self):
        # about:blank 不像 URL，但控件名是地址栏，仍应返回
        edits = [("地址和搜索栏", "about:blank")]
        self.assertEqual(client._pick_url_from_edits(edits), "about:blank")

    def test_empty_edits(self):
        self.assertEqual(client._pick_url_from_edits([]), "")
        self.assertEqual(client._pick_url_from_edits([("x", "   ")]), "")

    def test_chinese_address_bar_name(self):
        edits = [
            ("代码编辑器", "https://example.com"),
            ("地址和搜索栏", "https://www.luogu.com.cn/contest/9"),
        ]
        self.assertEqual(
            client._pick_url_from_edits(edits),
            "https://www.luogu.com.cn/contest/9",
        )


class TestRectNear(unittest.TestCase):

    def test_equal_rects(self):
        self.assertTrue(client._rect_near((0, 0, 1920, 1080), (0, 0, 1920, 1080)))

    def test_within_tolerance(self):
        self.assertTrue(client._rect_near((0, 0, 1920, 1080), (2, -1, 1919, 1082), tol=3))

    def test_outside_tolerance(self):
        self.assertFalse(client._rect_near((0, 0, 1920, 1080), (10, 0, 1920, 1080)))


if __name__ == "__main__":
    unittest.main()
