"""Cross-platform bidirectional pipe for socket-to-channel forwarding.

This module provides efficient data forwarding between a local socket and a Paramiko
SSH channel, working around Windows limitations where select.select() doesn't work
with Paramiko channels.
"""

import os
import select
import socket
import threading
import time
from typing import Optional, Tuple

# Platform detection
IS_WINDOWS = os.name == "nt"


def bidirectional_pipe(
    sock: socket.socket,
    chan,
    timeout: float = 300.0,
    chunk_size: int = 65536,
    stats_callback=None,
) -> Tuple[int, int]:
    """Bidirectionally pipe data between socket and SSH channel.

    Args:
        sock: Local client socket
        chan: Paramiko SSH channel
        timeout: Maximum time in seconds (0 = no limit)
        chunk_size: Buffer size for data transfer
        stats_callback: Optional callback(stats_dict) for progress updates

    Returns:
        Tuple of (bytes_sent, bytes_received)
    """
    bytes_sent = 0
    bytes_received = 0
    stop_event = threading.Event()
    stats_lock = threading.Lock()
    errors = []

    def get_stats():
        with stats_lock:
            return bytes_sent, bytes_received

    if stats_callback:
        stats_callback({"sent": 0, "received": 0})

    if IS_WINDOWS:
        # Windows: Use separate threads with blocking I/O
        # This is necessary because select() doesn't work with Paramiko channels on Windows
        def forward_socket_to_channel():
            nonlocal bytes_sent
            try:
                sock.settimeout(1.0)  # Short timeout to allow checking stop_event
                while not stop_event.is_set():
                    try:
                        data = sock.recv(chunk_size)
                        if not data:
                            break
                        chan.sendall(data)
                        with stats_lock:
                            bytes_sent += len(data)
                        if stats_callback:
                            stats_callback({"sent": bytes_sent, "received": bytes_received})
                    except socket.timeout:
                        continue
                    except OSError:
                        break
            except Exception as e:
                errors.append(f"sock_to_chan: {e}")
            finally:
                stop_event.set()

        def forward_channel_to_socket():
            nonlocal bytes_received
            try:
                while not stop_event.is_set():
                    if chan.closed or chan.eof_received:
                        break
                    # Use recv_ready() to avoid blocking
                    if chan.recv_ready():
                        data = chan.recv(chunk_size)
                        if not data:
                            break
                        sock.sendall(data)
                        with stats_lock:
                            bytes_received += len(data)
                        if stats_callback:
                            stats_callback({"sent": bytes_sent, "received": bytes_received})
                    else:
                        time.sleep(0.001)  # Small sleep to prevent busy-wait
            except Exception as e:
                errors.append(f"chan_to_sock: {e}")
            finally:
                stop_event.set()

        t1 = threading.Thread(target=forward_socket_to_channel, daemon=True)
        t2 = threading.Thread(target=forward_channel_to_socket, daemon=True)
        t1.start()
        t2.start()

        # Wait for completion or timeout
        start_time = time.monotonic()
        while t1.is_alive() or t2.is_alive():
            if timeout > 0 and time.monotonic() - start_time > timeout:
                stop_event.set()
                break
            if stop_event.is_set():
                break
            time.sleep(0.01)

        stop_event.set()

        # Try to close socket to unblock threads
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except:
            pass

        t1.join(timeout=1)
        t2.join(timeout=1)

    else:
        # Unix/Linux/Mac: Use select() which works with both sockets and Paramiko channels
        sock.setblocking(True)

        start_time = time.monotonic()
        while True:
            # Check timeout
            if timeout > 0 and time.monotonic() - start_time > timeout:
                break

            # Check channel status
            if chan.closed or chan.eof_received:
                break

            # Use select to wait for data on either end
            try:
                r, _, _ = select.select([sock, chan], [], [], 1.0)
            except ValueError:
                # Paramiko channel doesn't have valid fileno on some platforms
                break

            if sock in r:
                data = sock.recv(chunk_size)
                if not data:
                    break
                chan.sendall(data)
                bytes_sent += len(data)
                if stats_callback:
                    stats_callback({"sent": bytes_sent, "received": bytes_received})

            if chan in r:
                data = chan.recv(chunk_size)
                if not data:
                    break
                sock.sendall(data)
                bytes_received += len(data)
                if stats_callback:
                    stats_callback({"sent": bytes_sent, "received": bytes_received})

    return bytes_sent, bytes_received
