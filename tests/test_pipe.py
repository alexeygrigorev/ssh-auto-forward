"""Unit tests for SSHTunnel._pipe (no SSH server required).

The local client side is a real socketpair; the SSH channel is a fake object
that mimics the slice of paramiko.Channel that _pipe uses. This pins down the
large-transfer truncation fix and the idle-teardown behaviour without needing
Docker or a network.
"""

import hashlib
import queue
import socket
import threading
import time

import pytest

from ssh_auto_forward.forwarder import SSHTunnel

_EOF = object()


class FakeChannel:
    """Minimal stand-in for paramiko.Channel as used by _pipe.

    Bytes the "remote" sends to us are fed via feed()/feed_eof() and surface
    through recv(). Bytes _pipe sends to the remote land in .sent. recv honours
    settimeout() and raises socket.timeout, like a real channel.
    """

    def __init__(self):
        self._inbox = queue.Queue()
        self._eof = False
        self._timeout = None
        self.sent = bytearray()
        self.write_shutdown = False
        self.closed = False

    # remote -> local
    def feed(self, data):
        self._inbox.put(data)

    def feed_eof(self):
        self._inbox.put(_EOF)

    def settimeout(self, t):
        self._timeout = t

    def recv(self, n):
        if self._eof:
            return b""
        try:
            item = self._inbox.get(timeout=self._timeout)
        except queue.Empty:
            raise socket.timeout() from None
        if item is _EOF:
            self._eof = True
            return b""
        return item[:n]

    # local -> remote
    def sendall(self, data):
        if self.write_shutdown:
            raise OSError("channel write shutdown")
        self.sent.extend(data)

    def shutdown_write(self):
        self.write_shutdown = True

    def close(self):
        self.closed = True


def _make_tunnel():
    t = SSHTunnel.__new__(SSHTunnel)  # bypass __init__/SSH setup
    t.bytes_sent = 0
    t.bytes_received = 0
    t.last_activity = 0.0
    return t


def _run_pipe(tunnel, sock, chan):
    done = threading.Event()

    def target():
        tunnel._pipe(sock, chan)
        done.set()

    threading.Thread(target=target, daemon=True).start()
    return done


def _drain(sock, into, stop):
    sock.settimeout(30)
    try:
        while True:
            chunk = sock.recv(1 << 16)
            if not chunk:
                break
            into.extend(chunk)
    except OSError:
        pass
    finally:
        stop.set()


@pytest.mark.parametrize("size", [1024, 1_000_000, 16 * 1024 * 1024])
def test_large_download_not_truncated(size):
    """A full response streamed then closed (HTTP/1.0 style) arrives intact."""
    tunnel = _make_tunnel()
    browser, fwd = socket.socketpair()
    chan = FakeChannel()

    payload = bytes((i * 1103515245 + 12345) & 0xFF for i in range(size))
    expected = hashlib.sha256(payload).hexdigest()

    received = bytearray()
    stop = threading.Event()
    reader = threading.Thread(target=_drain, args=(browser, received, stop), daemon=True)
    reader.start()

    browser.sendall(b"GET /big HTTP/1.0\r\n\r\n")
    done = _run_pipe(tunnel, fwd, chan)

    # Remote streams the whole body, then closes its write side.
    for off in range(0, len(payload), 1 << 16):
        chan.feed(payload[off:off + (1 << 16)])
    chan.feed_eof()

    assert done.wait(timeout=30), "_pipe did not return"
    reader.join(timeout=5)
    browser.close()

    assert len(received) == size
    assert hashlib.sha256(bytes(received)).hexdigest() == expected
    assert bytes(chan.sent) == b"GET /big HTTP/1.0\r\n\r\n"


def test_slow_drip_transfer_survives_idle_timeout(monkeypatch):
    """A slow transfer whose total time exceeds the idle timeout still completes,
    because each byte resets the idle clock."""
    monkeypatch.setenv("SSH_FORWARD_IDLE_TIMEOUT", "1")
    tunnel = _make_tunnel()
    browser, fwd = socket.socketpair()
    chan = FakeChannel()

    received = bytearray()
    stop = threading.Event()
    threading.Thread(target=_drain, args=(browser, received, stop), daemon=True).start()

    browser.sendall(b"GET /slow HTTP/1.0\r\n\r\n")
    done = _run_pipe(tunnel, fwd, chan)

    chunks = 8
    for i in range(chunks):
        chan.feed(bytes([i]) * 100)
        time.sleep(0.3)  # gap < idle timeout, total (~2.4s) > idle timeout
    chan.feed_eof()

    assert done.wait(timeout=10), "_pipe killed a slow but active transfer"
    assert len(received) == chunks * 100


def test_idle_keepalive_connection_is_reclaimed(monkeypatch):
    """An abandoned keep-alive connection (remote never sends EOF, client has
    half-closed) is torn down by the idle timeout instead of leaking forever."""
    monkeypatch.setenv("SSH_FORWARD_IDLE_TIMEOUT", "1")
    tunnel = _make_tunnel()
    browser, fwd = socket.socketpair()
    chan = FakeChannel()

    received = bytearray()
    stop = threading.Event()
    threading.Thread(target=_drain, args=(browser, received, stop), daemon=True).start()

    browser.sendall(b"GET / HTTP/1.1\r\n\r\n")
    done = _run_pipe(tunnel, fwd, chan)

    # Remote answers but keeps the connection open (no EOF); client finishes
    # its request and half-closes its write side.
    chan.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
    time.sleep(0.2)
    browser.shutdown(socket.SHUT_WR)

    # No further activity in either direction -> idle teardown within a few
    # seconds (idle timeout 1s + the 2s recv poll backstop).
    assert done.wait(timeout=10), "idle keep-alive connection was not reclaimed"
    assert bytes(received).endswith(b"hi")


def test_idle_timeout_disabled_keeps_waiting(monkeypatch):
    """With the idle timeout disabled (0), an idle connection is NOT torn down."""
    monkeypatch.setenv("SSH_FORWARD_IDLE_TIMEOUT", "0")
    tunnel = _make_tunnel()
    browser, fwd = socket.socketpair()
    chan = FakeChannel()

    received = bytearray()
    stop = threading.Event()
    threading.Thread(target=_drain, args=(browser, received, stop), daemon=True).start()

    browser.sendall(b"GET / HTTP/1.1\r\n\r\n")
    done = _run_pipe(tunnel, fwd, chan)

    chan.feed(b"hi")
    time.sleep(0.2)
    browser.shutdown(socket.SHUT_WR)

    # Idle teardown disabled -> _pipe stays alive waiting on the remote.
    assert not done.wait(timeout=4), "_pipe should not tear down when idle timeout is disabled"

    # A subsequent remote EOF still ends it cleanly.
    chan.feed_eof()
    assert done.wait(timeout=10)
    browser.close()
