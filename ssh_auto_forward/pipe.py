"""Cross-platform bidirectional pipe for socket-to-channel forwarding.

This module provides efficient data forwarding between a local socket and a Paramiko
SSH channel using threading with blocking I/O, which works reliably on all platforms.
"""

import socket
import threading
import time
from typing import Optional, Tuple


def bidirectional_pipe(
    sock: socket.socket,
    chan,
    timeout: float = 300.0,
    chunk_size: int = 65536,
    stats_callback=None,
) -> Tuple[int, int]:
    """Bidirectionally pipe data between socket and SSH channel.

    Uses separate threads with blocking I/O for reliable cross-platform operation.

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

    if stats_callback:
        stats_callback({"sent": 0, "received": 0})

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

    return bytes_sent, bytes_received
