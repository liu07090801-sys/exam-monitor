"""
感知哈希与汉明距离单元测试。
"""
import unittest

from PIL import Image

import client


class TestImageHash(unittest.TestCase):

    def test_same_image_same_hash(self):
        img = Image.new("RGB", (64, 64), (128, 128, 128))
        self.assertEqual(client.image_hash(img), client.image_hash(img))

    def test_hash_length(self):
        # 16x16 bit -> 32 bytes
        img = Image.new("RGB", (64, 64), (200, 10, 10))
        self.assertEqual(len(client.image_hash(img)), 32)

    def test_hash_diff_identical_is_zero(self):
        h = client.image_hash(Image.new("RGB", (64, 64), (10, 20, 30)))
        self.assertEqual(client.hash_diff(h, h), 0)

    def test_hash_diff_upper_bound(self):
        # 256 bit 哈希，汉明距离最大 256
        h1 = bytes(32)
        h2 = bytes([0xFF]) * 32
        self.assertEqual(client.hash_diff(h1, h2), 256)

    def test_hash_diff_none_input(self):
        self.assertGreaterEqual(client.hash_diff(None, b"x"), 10**9)
        self.assertGreaterEqual(client.hash_diff(b"x", None), 10**9)

    def test_different_images_different_hash(self):
        # 注意：纯黑 vs 纯白是均值哈希的已知盲区（纯色图无结构信息），
        # 此处使用左右镜像结构图，均值相同但分布不同，哈希必须不同。
        img1 = Image.new("L", (64, 64), 0)  # 左白右黑
        img2 = Image.new("L", (64, 64), 0)  # 左黑右白
        for y in range(64):
            for x in range(32):
                img1.putpixel((x, y), 255)
                img2.putpixel((x + 32, y), 255)
        self.assertNotEqual(client.image_hash(img1), client.image_hash(img2))


if __name__ == "__main__":
    unittest.main()
