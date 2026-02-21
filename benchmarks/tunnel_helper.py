#!/usr/bin/env python3
"""Helper: start ssh-auto-forward (the real CLI) pointing at the Docker container.

Usage:
    python tunnel_helper.py <ssh_port> <expected_local_ports_csv>

Creates a temporary SSH config, launches `ssh-auto-forward` in --cli mode,
waits until the expected local ports are reachable, then prints "READY".
Blocks until SIGTERM/SIGINT.

For buffer_size testing, falls back to direct SSHTunnel with custom buffer:
    python tunnel_helper.py <ssh_port> <remote_port> <local_port> --direct [buffer_size]
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BENCH_DIR)


def wait_tcp(host, port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def run_cli_mode(ssh_port, expected_ports):
    """Launch the real ssh-auto-forward --cli against Docker."""

    # Create temporary SSH config
    config = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ssh_config", delete=False, prefix="bench_"
    )
    config.write(
        f"Host bench-docker\n"
        f"    HostName 127.0.0.1\n"
        f"    Port {ssh_port}\n"
        f"    User root\n"
        f"    StrictHostKeyChecking no\n"
        f"    UserKnownHostsFile /dev/null\n"
    )
    config.close()

    # Launch ssh-auto-forward --cli
    env = os.environ.copy()
    env["PYTHONPATH"] = PROJECT_DIR
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "ssh_auto_forward.cli",
            "bench-docker",
            "--cli",
            "--config", config.name,
            "--interval", "2",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    # Wait for expected local ports to be reachable
    all_ready = True
    for port in expected_ports:
        if not wait_tcp("127.0.0.1", port, timeout=30):
            all_ready = False
            break

    if not all_ready:
        print("FAIL: ports not reachable", flush=True)
        proc.terminate()
        os.unlink(config.name)
        sys.exit(1)

    # Verify tunnel actually forwards data (not just that the socket is bound)
    import urllib.request
    verified = False
    for attempt in range(10):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{expected_ports[0]}/", timeout=3
            ).read(1)
            verified = True
            break
        except Exception:
            time.sleep(1)

    if not verified:
        print("FAIL: tunnel ports bound but not forwarding", flush=True)
        proc.terminate()
        os.unlink(config.name)
        sys.exit(1)

    print("READY", flush=True)

    # Block until signal
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    os.unlink(config.name)


def run_direct_mode(ssh_port, remote_port, local_port, buffer_size):
    """Direct SSHTunnel for buffer_size sweep tests."""
    sys.path.insert(0, PROJECT_DIR)
    import select as _select

    import paramiko
    from ssh_auto_forward.forwarder import SSHTunnel

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", port=ssh_port, username="root",
                   allow_agent=True, timeout=10)

    tunnel = SSHTunnel(
        ssh_client=client,
        remote_host="127.0.0.1",
        remote_port=remote_port,
        local_port=local_port,
    )

    if buffer_size != 65536:
        def _pipe(sock, chan):
            try:
                while tunnel.active:
                    if chan.closed or chan.eof_received:
                        break
                    r, _, _ = _select.select([sock, chan], [], [], 1.0)
                    if sock in r:
                        data = sock.recv(buffer_size)
                        if not data:
                            break
                        chan.sendall(data)
                        tunnel.bytes_sent += len(data)
                        tunnel.last_activity = time.monotonic()
                    if chan in r:
                        data = chan.recv(buffer_size)
                        if not data:
                            break
                        sock.sendall(data)
                        tunnel.bytes_received += len(data)
                        tunnel.last_activity = time.monotonic()
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
                try:
                    chan.close()
                except Exception:
                    pass

        tunnel._pipe = _pipe

    if not tunnel.start():
        print("FAIL", flush=True)
        client.close()
        sys.exit(1)

    if not wait_tcp("127.0.0.1", local_port, timeout=10):
        print("FAIL: port not reachable", flush=True)
        tunnel.stop()
        client.close()
        sys.exit(1)

    print("READY", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    tunnel.stop()
    client.close()


def main():
    if "--direct" in sys.argv:
        # Direct mode: tunnel_helper.py <ssh_port> <remote_port> <local_port> --direct [buffer]
        ssh_port = int(sys.argv[1])
        remote_port = int(sys.argv[2])
        local_port = int(sys.argv[3])
        buf = int(sys.argv[5]) if len(sys.argv) > 5 else 65536
        run_direct_mode(ssh_port, remote_port, local_port, buf)
    else:
        # CLI mode: tunnel_helper.py <ssh_port> <expected_ports_csv>
        ssh_port = int(sys.argv[1])
        expected_ports = [int(p) for p in sys.argv[2].split(",")]
        run_cli_mode(ssh_port, expected_ports)


if __name__ == "__main__":
    main()
