"""
TLS 端到端测试（headless）：
- 验证 build_server_ssl_context / build_client_ssl_context
- 用自签名证书完成一次真实 wss:// 握手 + 收发消息
- 本机无 openssl 时自动跳过
"""
import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import websockets

import client
import config
import server


def _have_openssl() -> bool:
    return shutil.which("openssl") is not None


@unittest.skipUnless(_have_openssl(), "openssl not available, skip TLS handshake test")
class TestTLS(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        base = Path(cls.tmpdir.name)
        cls.cert = base / "cert.pem"
        cls.key = base / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(cls.key), "-out", str(cls.cert),
                "-days", "1", "-nodes", "-subj", "//CN=localhost",
            ],
            check=True, capture_output=True,
        )
        # monkeypatch TLS 配置指向临时证书
        cls._old = (
            config.USE_TLS, config.TLS_CERT_FILE,
            config.TLS_KEY_FILE, config.TLS_CA_FILE,
        )
        config.USE_TLS = True
        config.TLS_CERT_FILE = str(cls.cert)
        config.TLS_KEY_FILE = str(cls.key)
        config.TLS_CA_FILE = str(base / "ca.pem")  # 不存在 -> 客户端不校验

    @classmethod
    def tearDownClass(cls):
        (config.USE_TLS, config.TLS_CERT_FILE,
         config.TLS_KEY_FILE, config.TLS_CA_FILE) = cls._old
        cls.tmpdir.cleanup()

    def test_server_context_built(self):
        ctx = server.build_server_ssl_context()
        self.assertIsNotNone(ctx)

    def test_client_context_falls_back_to_no_verify(self):
        ctx = client.build_client_ssl_context()
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.verify_mode, __import__("ssl").CERT_NONE)

    def test_missing_cert_raises(self):
        config.TLS_CERT_FILE = str(Path(self.tmpdir.name) / "nope.pem")
        try:
            with self.assertRaises(FileNotFoundError):
                server.build_server_ssl_context()
        finally:
            config.TLS_CERT_FILE = str(self.cert)

    def test_wss_handshake_and_echo(self):
        async def run():
            sctx = server.build_server_ssl_context()
            cctx = client.build_client_ssl_context()

            async def handler(ws, path=None):
                raw = await ws.recv()
                await ws.send(b"PONG:" + raw)

            srv = await websockets.serve(handler, "127.0.0.1", 0, ssl=sctx)
            port = srv.sockets[0].getsockname()[1]
            try:
                async with websockets.connect(
                    f"wss://127.0.0.1:{port}", ssl=cctx, open_timeout=5
                ) as ws:
                    await ws.send(b"hello-tls")
                    resp = await asyncio.wait_for(ws.recv(), timeout=5)
                    self.assertEqual(resp, b"PONG:hello-tls")
            finally:
                srv.close()
                await srv.wait_closed()

        asyncio.run(run())


class TestTLSSmokeWithoutConfig(unittest.TestCase):
    """未启用 TLS 时，两端都返回 None（保持明文行为）。"""

    def test_server_returns_none_when_tls_disabled(self):
        old = config.USE_TLS
        config.USE_TLS = False
        try:
            self.assertIsNone(server.build_server_ssl_context())
        finally:
            config.USE_TLS = old

    def test_client_returns_none_when_tls_disabled(self):
        old = config.USE_TLS
        config.USE_TLS = False
        try:
            self.assertIsNone(client.build_client_ssl_context())
        finally:
            config.USE_TLS = old


if __name__ == "__main__":
    unittest.main()
