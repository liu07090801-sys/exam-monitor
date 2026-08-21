"""
多显示器拼接布局单元测试（纯函数 compose_monitor_images）。
"""
import unittest

from PIL import Image

import client


def _img(w, h, color):
    return Image.new("RGB", (w, h), color)


class TestComposeMonitors(unittest.TestCase):

    def test_single_monitor_identity(self):
        img = _img(100, 50, (1, 2, 3))
        out = client.compose_monitor_images([(0, 0, img)])
        self.assertEqual(out.size, (100, 50))
        self.assertEqual(out.getpixel((10, 10)), (1, 2, 3))

    def test_horizontal_join(self):
        a = _img(100, 50, (255, 0, 0))
        b = _img(100, 50, (0, 255, 0))
        out = client.compose_monitor_images([(0, 0, a), (100, 0, b)])
        self.assertEqual(out.size, (200, 50))
        self.assertEqual(out.getpixel((50, 25)), (255, 0, 0))
        self.assertEqual(out.getpixel((150, 25)), (0, 255, 0))

    def test_negative_offsets(self):
        # 副屏在主屏左侧（left 为负）
        a = _img(100, 50, (255, 0, 0))
        b = _img(100, 50, (0, 255, 0))
        out = client.compose_monitor_images([(0, 0, a), (-100, 0, b)])
        self.assertEqual(out.size, (200, 50))
        self.assertEqual(out.getpixel((100, 25)), (255, 0, 0))  # 主屏右移
        self.assertEqual(out.getpixel((50, 25)), (0, 255, 0))   # 副屏在最左

    def test_vertical_stack(self):
        a = _img(100, 50, (255, 0, 0))
        b = _img(100, 50, (0, 255, 0))
        out = client.compose_monitor_images([(0, 0, a), (0, 50, b)])
        self.assertEqual(out.size, (100, 100))
        self.assertEqual(out.getpixel((50, 75)), (0, 255, 0))

    def test_empty_fallback(self):
        out = client.compose_monitor_images([])
        self.assertEqual(out.size, (1, 1))


if __name__ == "__main__":
    unittest.main()
