"""Shared infrastructure for benchmark scripts.

Handles Docker lifecycle, tunnel management, and download client processes.
"""

import json
import os
import socket
import subprocess
import sys
import time

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BENCH_DIR)
PYTHON = sys.executable

CONTAINER_NAME = "ssh-auto-forward-bench"
IMAGE_NAME = "ssh-auto-forward-bench"
SSH_PORT = 2299

# Remote HTTP ports inside the container (not exposed to host)
SERVERS = {
    "nginx":     8081,
    "darkhttpd": 8082,
    "python":    8080,
}
STREAM_PORT = 8083

_next_local = 19000


def alloc_port():
    global _next_local
    p = _next_local
    _next_local += 1
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wait_tcp(host, port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def run_cmd(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

def docker_build():
    print("Building Docker image …")
    run_cmd(["docker", "build", "-t", IMAGE_NAME, BENCH_DIR])


def _get_ssh_pubkey_path():
    """Write the host's SSH public key to a temp file and return its path."""
    import glob as _glob
    import tempfile

    pubkey = None
    # Try .pub files first
    for pattern in ["~/.ssh/id_*.pub"]:
        for path in _glob.glob(os.path.expanduser(pattern)):
            with open(path) as f:
                pubkey = f.read().strip()
            break
    # Derive from private key
    if not pubkey:
        for keyfile in ["~/.ssh/id_ed25519", "~/.ssh/id_rsa", "~/.ssh/id_ecdsa"]:
            path = os.path.expanduser(keyfile)
            if os.path.exists(path):
                result = subprocess.run(
                    ["ssh-keygen", "-y", "-f", path],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    pubkey = result.stdout.strip()
                    break
    if not pubkey:
        raise RuntimeError("No SSH public key found. Generate one with: ssh-keygen")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pub", prefix="bench_key_", delete=False,
    )
    tmp.write(pubkey + "\n")
    tmp.close()
    os.chmod(tmp.name, 0o600)
    return tmp.name


def docker_start():
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)

    print("Starting container (SSH-only) …")
    run_cmd([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{SSH_PORT}:22",
        "-e", "SSH_ENABLE_ROOT=true",
        IMAGE_NAME,
    ])

    print(f"Waiting for SSH on :{SSH_PORT} …")
    if not wait_tcp("127.0.0.1", SSH_PORT):
        raise RuntimeError("SSH not reachable")

    # Inject SSH public key via docker cp (volume mounts have permission issues)
    pubkey_path = _get_ssh_pubkey_path()
    try:
        run_cmd(["docker", "exec", CONTAINER_NAME, "mkdir", "-p", "/root/.ssh"])
        run_cmd(["docker", "cp", pubkey_path,
                 f"{CONTAINER_NAME}:/root/.ssh/authorized_keys"])
        run_cmd(["docker", "exec", CONTAINER_NAME,
                 "chmod", "600", "/root/.ssh/authorized_keys"])
        run_cmd(["docker", "exec", CONTAINER_NAME,
                 "chown", "root:root", "/root/.ssh/authorized_keys"])
    finally:
        os.unlink(pubkey_path)

    # Enable TCP forwarding (panubo/sshd disables it by default)
    run_cmd(["docker", "exec", CONTAINER_NAME, "sed", "-i",
             "s/AllowTcpForwarding no/AllowTcpForwarding yes/",
             "/etc/ssh/sshd_config"])
    # Restart sshd to pick up the config change
    run_cmd(["docker", "exec", CONTAINER_NAME, "sh", "-c",
             "pkill sshd; sleep 0.5; /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config &"])
    time.sleep(2)
    if not wait_tcp("127.0.0.1", SSH_PORT):
        raise RuntimeError("SSH not reachable after sshd restart")

    for cmd in [
        "nohup python3 -m http.server 8080 --directory /srv/bench >/dev/null 2>&1 &",
        "nohup nginx -c /etc/nginx/nginx-benchmark.conf >/dev/null 2>&1 &",
        "nohup darkhttpd /srv/bench --port 8082 >/dev/null 2>&1 &",
        "nohup python3 /srv/bench/stream_generator.py 8083 >/dev/null 2>&1 &",
    ]:
        run_cmd(["docker", "exec", CONTAINER_NAME, "sh", "-c", cmd])

    time.sleep(1)
    print("Container ready.\n")


def docker_stop():
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


# ---------------------------------------------------------------------------
# Tunnel management (separate processes, like real usage)
# ---------------------------------------------------------------------------

def start_our_tunnel(remote_ports, local_ports):
    """Start ssh-auto-forward --cli as a real subprocess.

    remote_ports and local_ports can be single ints or lists.
    When using the real CLI, remote_ports are auto-discovered and local_ports
    should match (the tool maps remote:N -> local:N when the port is free).
    Returns Popen.
    """
    if isinstance(remote_ports, int):
        remote_ports = [remote_ports]
    if isinstance(local_ports, int):
        local_ports = [local_ports]

    expected_csv = ",".join(str(p) for p in local_ports)
    args = [PYTHON, os.path.join(BENCH_DIR, "tunnel_helper.py"),
            str(SSH_PORT), expected_csv]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    line = proc.stdout.readline().decode().strip()
    if line != "READY":
        proc.kill()
        stderr = proc.stderr.read().decode()
        raise RuntimeError(f"tunnel_helper failed: {line} {stderr[:200]}")

    return proc


def start_our_tunnel_direct(remote_port, local_port, buffer_size=65536):
    """Start a single SSHTunnel directly (for buffer sweep tests).
    Returns Popen.
    """
    args = [PYTHON, os.path.join(BENCH_DIR, "tunnel_helper.py"),
            str(SSH_PORT), str(remote_port), str(local_port),
            "--direct", str(buffer_size)]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    line = proc.stdout.readline().decode().strip()
    if line != "READY":
        proc.kill()
        stderr = proc.stderr.read().decode()
        raise RuntimeError(f"tunnel_helper direct failed: {line} {stderr[:200]}")

    return proc


def start_native_tunnel(remote_port, local_port):
    """Start native ssh -L as a subprocess. Returns Popen."""
    proc = subprocess.Popen(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", str(SSH_PORT),
            "-N",
            "-L", f"{local_port}:127.0.0.1:{remote_port}",
            "root@127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not wait_tcp("127.0.0.1", local_port, timeout=10):
        proc.kill()
        raise RuntimeError(f"native ssh tunnel port {local_port} not reachable")

    return proc


def stop_tunnel(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Download client (separate process, like a browser)
# ---------------------------------------------------------------------------

def download_single(url, timeout=600):
    """Run download_helper.py single as a subprocess. Returns dict."""
    result = subprocess.run(
        [PYTHON, os.path.join(BENCH_DIR, "download_helper.py"), "single", url],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"download failed: {result.stderr[:200]}")
    return json.loads(result.stdout.strip())


def download_many(base_url, count, timeout=600):
    """Run download_helper.py many as a subprocess. Returns dict."""
    result = subprocess.run(
        [PYTHON, os.path.join(BENCH_DIR, "download_helper.py"),
         "many", base_url, str(count)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"download failed: {result.stderr[:200]}")
    return json.loads(result.stdout.strip())


def download_stress(base_url, count, rps, timeout=600):
    """Run download_helper.py stress as a subprocess. Returns dict."""
    result = subprocess.run(
        [PYTHON, os.path.join(BENCH_DIR, "download_helper.py"),
         "stress", base_url, str(count), str(rps)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"download failed: {result.stderr[:200]}")
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# Table printing
# ---------------------------------------------------------------------------

def print_row(cols, widths):
    print("  " + " | ".join(str(c).ljust(w) for c, w in zip(cols, widths)))


def print_sep(widths):
    print("  " + "-+-".join("-" * w for w in widths))


def print_header(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
