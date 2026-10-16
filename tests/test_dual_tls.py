"""http + https and ws + wss on one port (services/dual_tls.py).

    python3 -m unittest tests.test_dual_tls
"""
import asyncio
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request  # noqa: E402

from services import dual_tls  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DualTlsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ['PIBOX_TLS_DIR'] = cls.tmp
        script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'make_tls_cert.sh')
        subprocess.run(['bash', script], check=True, capture_output=True,
                       env=dict(os.environ, PIBOX_TLS_DIR=cls.tmp))
        cls.ctx = dual_tls.server_context()
        cls.client_ctx = ssl.create_default_context(cafile=os.path.join(cls.tmp, 'ca.crt'))

        app = Flask(__name__)

        @app.route('/who')
        def who():
            return f"{request.scheme} {request.remote_addr}"

        cls.http_port = free_port()
        cls.httpd = dual_tls.make_http_server('127.0.0.1', cls.http_port, app, cls.ctx)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

        # websockets server on loopback + relay on the public port
        import websockets
        cls.ws_port, cls.inner_port = free_port(), free_port()

        async def echo(ws, path=None):
            peer = dual_tls.real_peer(ws.remote_address)
            async for msg in ws:
                await ws.send(f"{msg} from {peer[0]}")

        ready = threading.Event()

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def main():
                async with websockets.serve(echo, '127.0.0.1', cls.inner_port):
                    ready.set()
                    await asyncio.Future()
            loop.run_until_complete(main())

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)
        cls.relay = dual_tls.WsRelay('127.0.0.1', cls.ws_port, cls.inner_port, cls.ctx)
        cls.relay.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.relay.stop()

    def test_http_plain(self):
        body = urllib.request.urlopen(f"http://127.0.0.1:{self.http_port}/who").read()
        self.assertEqual(body, b"http 127.0.0.1")

    def test_https_trusted_by_our_ca(self):
        for host in ('127.0.0.1', 'localhost'):
            body = urllib.request.urlopen(f"https://{host}:{self.http_port}/who",
                                          context=self.client_ctx).read()
            self.assertEqual(body, b"https 127.0.0.1")

    def test_https_untrusted_without_ca(self):
        with self.assertRaises(Exception):
            urllib.request.urlopen(f"https://127.0.0.1:{self.http_port}/who",
                                   context=ssl.create_default_context())

    def test_silent_client_does_not_block_others(self):
        idle = socket.create_connection(('127.0.0.1', self.http_port))
        try:
            self.test_http_plain()
        finally:
            idle.close()

    def _ws(self, url, ssl_ctx=None):
        import websockets

        async def go():
            async with websockets.connect(url, ssl=ssl_ctx) as ws:
                await ws.send("hi")
                return await ws.recv()
        return asyncio.run(go())

    def test_ws_plain(self):
        self.assertEqual(self._ws(f"ws://127.0.0.1:{self.ws_port}"), "hi from 127.0.0.1")

    def test_wss(self):
        self.assertEqual(self._ws(f"wss://localhost:{self.ws_port}", self.client_ctx),
                         "hi from 127.0.0.1")

    def test_real_peer_maps_relay_port(self):
        with dual_tls._peers_lock:
            dual_tls._peers[55555] = ('192.168.1.100', 4000)
        try:
            self.assertEqual(dual_tls.real_peer(('127.0.0.1', 55555))[0], '192.168.1.100')
            self.assertEqual(dual_tls.real_peer(('192.168.1.7', 1))[0], '192.168.1.7')
        finally:
            dual_tls._peers.pop(55555, None)

    def test_no_cert_means_no_tls(self):
        os.environ['PIBOX_TLS_DIR'] = tempfile.mkdtemp()
        try:
            self.assertIsNone(dual_tls.server_context())
        finally:
            os.environ['PIBOX_TLS_DIR'] = self.tmp


if __name__ == '__main__':
    unittest.main()
