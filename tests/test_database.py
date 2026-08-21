"""
SQLite 持久层单元测试（临时目录，不污染工作区）。
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

import server


class TestDatabase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = server.Database(str(Path(self.tmpdir.name) / "test.db"))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_add_and_export_events(self):
        self.db.add_event("2024001", "register", "考生上线: 2024001")
        self.db.add_event("2024001", "violation", "切屏 -> 记事本")
        out = Path(self.tmpdir.name) / "log.csv"
        self.db.export_events_csv(str(out))
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("时间,考生,类型,详情", text)
        self.assertIn("考生上线: 2024001", text)
        self.assertIn("切屏 -> 记事本", text)

    def test_add_screenshot_and_cleanup(self):
        shot_file = Path(self.tmpdir.name) / "shot.jpg"
        shot_file.write_bytes(b"jpeg-data")
        self.db.add_screenshot("2024001", str(shot_file), "ab12")

        # 未过期不清理
        self.db.cleanup_old(7)
        self.assertTrue(shot_file.exists())

        # 把记录时间改为 30 天前，再清理
        old_ts = time.time() - 30 * 86400
        self.db._conn.execute(
            "UPDATE screenshots SET ts=? WHERE client_id='2024001'", (old_ts,)
        )
        self.db._conn.commit()
        self.db.cleanup_old(7)
        self.assertFalse(shot_file.exists())


    def test_enforce_screenshot_cap(self):
        files = []
        for i in range(5):
            p = Path(self.tmpdir.name) / f"cap{i}.jpg"
            p.write_bytes(b"x")
            files.append(p)
            self.db.add_screenshot("2024001", str(p), "")
        removed = self.db.enforce_screenshot_cap(3)
        self.assertEqual(removed, 2)
        self.assertFalse(files[0].exists())
        self.assertFalse(files[1].exists())
        self.assertTrue(files[2].exists())
        count = self.db._conn.execute("SELECT COUNT(*) FROM screenshots").fetchone()[0]
        self.assertEqual(count, 3)

    def test_enforce_cap_disabled(self):
        p = Path(self.tmpdir.name) / "keep.jpg"
        p.write_bytes(b"x")
        self.db.add_screenshot("2024001", str(p), "")
        self.assertEqual(self.db.enforce_screenshot_cap(0), 0)
        self.assertTrue(p.exists())


class TestScreenshotFilename(unittest.TestCase):

    def test_format_and_uniqueness(self):
        from datetime import datetime
        names = {
            server.screenshot_filename(datetime(2026, 8, 17, 16, 30, 5, 123456))
            for _ in range(3)
        }
        self.assertEqual(len(names), 1)
        self.assertEqual(next(iter(names)), "163005_123456.jpg")

    def test_microseconds_make_same_second_unique(self):
        from datetime import datetime
        n1 = server.screenshot_filename(datetime(2026, 8, 17, 16, 30, 5, 111111))
        n2 = server.screenshot_filename(datetime(2026, 8, 17, 16, 30, 5, 222222))
        self.assertNotEqual(n1, n2)


if __name__ == "__main__":
    unittest.main()
