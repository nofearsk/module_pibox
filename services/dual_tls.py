"""
HTTP + HTTPS (and WS + WSS) on the same port.

When /var/pibox/tls/server.crt and server.key exist, each new connection is
sniffed: a TLS ClientHello (first byte 0x16) gets a TLS handshake, anything
else is served in the clear exactly as before. Without the files nothing
changes. The files come from scripts/make_tls_cert.sh (our own CA).

HTTP: the werkzeug server does the sniff in the per-connection thread, so the
caller's IP (request.remote_addr, used to identify ANPR cameras) is kept.
WS: the websockets server moves to 127.0.0.1 and a small relay owns the public
port; real_peer() maps the relay's loopback port back to the caller's IP.
"""
import logging
import os
import socket
import ssl
import threading

logger = logging.getLogger(__name__)

TLS_HANDSHAKE = 0x16
SNIFF_TIMEOUT = 10  # seconds a client may stay silent before its first byte


def tls_dir():
    from config import DATA_DIR
    return os.environ.get('PIBOX_TLS_DIR', os.path.join(DATA_DIR, 'tls'))


def server_context():
    """SSLContext from the cert files, or None when TLS is not set up."""
    if os.environ.get('PIBOX_TLS', '').lower() in ('0', 'false', 'off'):
        return None
    crt = os.path.join(tls_dir(), 'server.crt')
    key = os.path.join(tls_dir(), 'server.key')
    if not (os.path.exists(crt) and os.path.exists(key)):
        return None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(crt, key)
        return ctx
    except (OSError, ssl.SSLError) as e:
        logger.error(f"TLS disabled - cannot load {crt}: {e}")
        return None


def sniff(sock, ctx):
    """Return the socket to serve: TLS-wrapped if the client opened with a
    ClientHello, else the plain socket. None if the client went away."""
    sock.settimeout(SNIFF_TIMEOUT)
    try:
        first = sock.recv(1, socket.MSG_PEEK)
        if not first:
            return None
        if first[0] == TLS_HANDSHAKE:
            sock = ctx.wrap_socket(sock, server_side=True)
    except (OSError, ssl.SSLError) as e:
        logger.debug(f"TLS sniff/handshake failed: {e}")
        return None
    sock.settimeout(None)
    return sock


def _close(sock):
    try:
        sock.close()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HTTP / HTTPS
# ---------------------------------------------------------------------------

def make_http_server(host, port, app, ctx):
    """Threaded werkzeug server serving http and https on one port."""
    from werkzeug.serving import ThreadedWSGIServer, WSGIRequestHandler

    class Handler(WSGIRequestHandler):
        def make_environ(self):
            environ = super().make_environ()
            if isinstance(self.connection, ssl.SSLSocket):
                environ['wsgi.url_scheme'] = 'https'
            return environ

    class DualServer(ThreadedWSGIServer):
        def finish_request(self, request, client_address):
            conn = sniff(request, ctx)
            if conn is None:
                return
            try:
                self.RequestHandlerClass(conn, client_address, self)
            finally:
                # wrap_socket detached the raw socket; socketserver will only
                # close that one, so the TLS socket must be closed here.
                if conn is not request:
                    _close(conn)

    return DualServer(host, port, app, handler=Handler)


# ---------------------------------------------------------------------------
# WS / WSS
# ---------------------------------------------------------------------------

_peers = {}  # relay's loopback port -> caller (ip, port)
_peers_lock = threading.Lock()


def real_peer(remote_address):
    """Caller address for a websocket accepted through the relay."""
    if remote_address and remote_address[0] in ('127.0.0.1', '::1'):
        with _peers_lock:
            return _peers.get(remote_address[1], remote_address)
    return remote_address


class WsRelay:
    """Owns the public WS port; plain or TLS clients are piped to the
    websockets server on 127.0.0.1:<inner_port>."""

    def __init__(self, host, port, inner_port, ctx):
        self.host, self.port, self.inner_port, self.ctx = host, port, inner_port, ctx
        self._sock = None
        self._running = False

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(100)
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True,
                         name='ws-tls-relay').start()
        logger.info(f"WebSocket ws:// + wss:// on {self.host}:{self.port}")

    def stop(self):
        self._running = False
        if self._sock:
            _close(self._sock)

    def _accept_loop(self):
        while self._running:
            try:
                client, addr = self._sock.accept()
            except OSError:
                if self._running:
                    logger.exception("WS relay accept failed")
                continue
            threading.Thread(target=self._serve, args=(client, addr),
                             daemon=True).start()

    def _serve(self, client, addr):
        conn = sniff(client, self.ctx)
        if conn is None:
            _close(client)
            return
        try:
            inner = socket.create_connection(('127.0.0.1', self.inner_port), timeout=5)
            inner.settimeout(None)
        except OSError as e:
            logger.error(f"WS relay cannot reach inner server: {e}")
            _close(conn)
            return
        local_port = inner.getsockname()[1]
        with _peers_lock:
            _peers[local_port] = addr
        try:
            t = threading.Thread(target=self._pipe, args=(inner, conn), daemon=True)
            t.start()
            self._pipe(conn, inner)
            t.join()
        finally:
            with _peers_lock:
                _peers.pop(local_port, None)
            _close(conn)
            _close(inner)

    @staticmethod
    def _pipe(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except (OSError, ssl.SSLError):
            pass
        finally:
            # One side ended: end both so the other thread's recv returns.
            # socket.socket.shutdown, not SSLSocket.shutdown, which would
            # drop the TLS state under the other thread.
            for s in (src, dst):
                try:
                    socket.socket.shutdown(s, socket.SHUT_RDWR)
                except OSError:
                    pass
