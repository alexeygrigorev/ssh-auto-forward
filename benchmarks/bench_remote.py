#!/usr/bin/env python3
"""Simple benchmark for remote SSH testing.

Usage:
    REMOTE_HOST=hetzner REMOTE_USER=alexey uv run python benchmarks/bench_remote.py
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))

# Config from env or CLI
REMOTE_HOST = os.getenv("REMOTE_HOST", "hetzner")
REMOTE_USER = os.getenv("REMOTE_USER", "alexey")
REMOTE_PORT = int(os.getenv("REMOTE_PORT", "22"))
TEST_PORT = 8080  # HTTP server on remote

# Test file sizes - small to large for comprehensive testing
FILES = [
    ("100kb", 100 * 1024),
    ("500kb", 500 * 1024),
    ("1mb", 1 * 1024 * 1024),
    ("5mb", 5 * 1024 * 1024),
    ("10mb", 10 * 1024 * 1024),
    ("50mb", 50 * 1024 * 1024),
    ("100mb", 100 * 1024 * 1024),
]


def ssh_cmd(cmd):
    """Run command on remote server via SSH."""
    full_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-n",  # prevent reading from stdin
        "-p", str(REMOTE_PORT),
        f"{REMOTE_USER}@{REMOTE_HOST}",
        cmd
    ]
    result = subprocess.run(full_cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return result


def wait_tcp(host, port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def setup_remote():
    """Setup test files and HTTP server on remote."""
    print(f"Setting up remote {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_PORT}...", flush=True)

    # Kill any existing test server
    ssh_cmd(f"pkill -f 'python3 -m http.server {TEST_PORT}' 2>/dev/null")
    time.sleep(0.5)

    # Create bench dir
    ssh_cmd(f"mkdir -p ~/bench")
    print("Created bench dir", flush=True)

    # Check if test files exist, generate if missing
    print("Checking test files on remote...", flush=True)
    has_files = True
    for name, size in FILES:
        result = ssh_cmd(f"test -f ~/bench/{name}.bin && echo yes || echo no")
        if "yes" not in result.stdout:
            has_files = False
            break

    if not has_files:
        print("Generating test files on remote...", flush=True)
        for name, size in FILES:
            print(f"  {name}...", end="", flush=True)
            # Use truncate for fast file creation
            ssh_cmd(f"truncate -s {size} ~/bench/{name}.bin")
            print(" done", flush=True)
    else:
        print("  Test files already present, using cache.", flush=True)

    # Start HTTP server - use Uvicorn with FastAPI (binds to 0.0.0.0 for detection)
    print(f"Starting HTTP server on port {TEST_PORT}...", flush=True)

    # Copy the HTTP server script to remote
    with open(os.path.join(BENCH_DIR, "http_server.py")) as f:
        server_script = f.read()

    # Use base64 to safely transfer the script
    import base64
    encoded = base64.b64encode(server_script.encode()).decode()
    ssh_cmd(f"echo {encoded} | base64 -d > ~/bench/http_server.py")

    # Kill any existing screen sessions and servers
    ssh_cmd(f"screen -S uvicorn_bench -X quit 2>/dev/null")
    time.sleep(1)
    ssh_cmd(f"fuser -k {TEST_PORT}/tcp 2>/dev/null")
    time.sleep(2)

    # Start uvicorn using module with explicit host binding
    ssh_cmd(f"screen -dm -S uvicorn_bench bash -c 'cd ~/bench && python3 -m uvicorn http_server:app --host 0.0.0.0 --port {TEST_PORT} --log-level warning'")

    # Wait for server to be ready
    for i in range(20):
        time.sleep(1)
        result = ssh_cmd(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{TEST_PORT}/ 2>/dev/null || echo 000")
        if result.stdout.strip() == "200":
            print(f"  Server ready after {i+1}s", flush=True)
            break
    print("Remote setup complete.\n", flush=True)


def start_native_tunnel(remote_port, local_port):
    """Start native ssh -L tunnel."""
    proc = subprocess.Popen(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", str(REMOTE_PORT),
            "-N",
            "-L", f"{local_port}:127.0.0.1:{remote_port}",
            f"{REMOTE_USER}@{REMOTE_HOST}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not wait_tcp("127.0.0.1", local_port, timeout=10):
        proc.kill()
        raise RuntimeError(f"Tunnel port {local_port} not reachable")

    return proc


def start_our_tunnel(remote_port, expected_local_port):
    """Start ssh-auto-forward tunnel and detect the actual local port used."""
    # Use the project Python directly from venv
    # Find the venv python
    import shutil
    venv_python = None
    for path in ["python", "python3", sys.executable]:
        try:
            result = subprocess.run(
                [path, "-c", "import paramiko; print('OK')"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                venv_python = path
                break
        except:
            pass

    if not venv_python:
        # Fall back to uvx
        venv_python = "uvx"

    host_arg = REMOTE_HOST

    if venv_python == "uvx":
        cmd = [
            "uvx", "ssh-auto-forward",
            "--cli",
            "-p", "3000:30000",  # Allow flexible port assignment
            host_arg,
        ]
    else:
        cmd = [
            venv_python, "-m", "ssh_auto_forward.cli",
            "--cli",
            "-p", "3000:30000",  # Allow flexible port assignment
            host_arg,
        ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,  # Important: prevent blocking on stdin
    )

    # Wait for the tunnel port to be available
    # Try the expected port first, then scan for alternatives
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        # Try expected port
        if wait_tcp("127.0.0.1", expected_local_port, timeout=0.5):
            return proc, expected_local_port

        # Scan nearby ports (ssh-auto-forward might pick a different port)
        for offset in range(0, 100):
            test_port = remote_port + offset
            if wait_tcp("127.0.0.1", test_port, timeout=0.1):
                print(f"    [Detected tunnel on port {test_port} instead of {expected_local_port}]", flush=True)
                return proc, test_port

        time.sleep(0.3)

    proc.kill()
    stdout, stderr = proc.communicate()
    error_msg = stderr.decode() if stderr else stdout.decode()
    raise RuntimeError(f"ssh-auto-forward tunnel failed: {error_msg[:500]}")


def ensure_server_running():
    """Make sure the HTTP server is running, restart if needed."""
    # Use localhost since we're checking from the remote machine itself
    result = ssh_cmd(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{TEST_PORT}/ 2>/dev/null || echo 000")
    code = result.stdout.strip()
    print(f"    [Health check: HTTP {code}]", flush=True)
    if code != "200":
        print("    [Server not responding, restarting with uvicorn...]", flush=True)
        ssh_cmd(f"screen -dm -S uvicorn_bench bash -c 'cd ~/bench && python3 -m uvicorn http_server:app --host 0.0.0.0 --port {TEST_PORT} --log-level warning'")
        time.sleep(3)


def download_file(local_port, filename):
    """Download file and return stats."""
    # Ensure server is running before download
    ensure_server_running()

    url = f"http://127.0.0.1:{local_port}/{filename}"
    print(f"    [Downloading {url}]", flush=True)

    # Get file size from remote first
    size_result = ssh_cmd(f"stat -f%z ~/bench/{filename} 2>/dev/null || stat -c%s ~/bench/{filename} 2>/dev/null")
    size_output = size_result.stdout.strip()
    file_size = int(size_output) if size_output.isdigit() else 0

    # Test the tunnel first with a HEAD request
    test_result = subprocess.run(
        ["curl.exe", "-s", "-o", "NUL", "-w", "%{http_code}", f"http://127.0.0.1:{local_port}/"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    print(f"    [Tunnel test: HTTP {test_result.stdout.decode().strip()}]", flush=True)

    # Download to a temp file (Windows-specific workaround for subprocess+curl issues)
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:
        start = time.monotonic()
        proc = subprocess.run(
            ["curl.exe", "-s", "-o", tmp_path, url],
            capture_output=True,
            timeout=60,
        )
        elapsed = time.monotonic() - start

        if proc.returncode != 0:
            stderr = proc.stderr.decode() if proc.stderr else ""
            raise RuntimeError(f"curl failed (code {proc.returncode}): {stderr[:100]}")

        return {
            "bytes": file_size,
            "elapsed": elapsed,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass


def print_row(cols, widths):
    print("  " + " | ".join(str(c).ljust(w) for c, w in zip(cols, widths)))


def print_sep(widths):
    print("  " + "-+-".join("-" * w for w in widths))


def main(host=REMOTE_HOST, user=REMOTE_USER, port=REMOTE_PORT, our_only=False, native_only=False):
    """Run benchmark with given parameters."""
    # Update globals from parameters
    global REMOTE_HOST, REMOTE_USER, REMOTE_PORT
    REMOTE_HOST = host
    REMOTE_USER = user
    REMOTE_PORT = port

    try:
        setup_remote()
        results = []

        # Verify server is still running after setup
        print("Verifying server is running...", flush=True)
        ensure_server_running()

        # Next available local port
        next_local = 19000

        # ==================== ssh-auto-forward ====================
        if not native_only:
            print("=" * 60, flush=True)
            print("  ssh-auto-forward benchmark", flush=True)
            print("=" * 60, flush=True)

            our_local = next_local
            next_local += 1

            print(f"Starting ssh-auto-forward tunnel (remote {TEST_PORT} -> local {our_local})...", flush=True)
            try:
                our_tunnel, actual_local = start_our_tunnel(TEST_PORT, our_local)
                our_local = actual_local  # Use the actual port assigned
                print("Tunnel established!", flush=True)

                W = [12, 14, 10]
                print_row(["File", "Throughput", "Time"], W)
                print_sep(W)

                for fname, fsize in FILES:
                    try:
                        r = download_file(our_local, f"{fname}.bin")
                        mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
                        print_row([fname, f"{mbps:.1f} MB/s", f"{r['elapsed']:.2f}s"], W)
                        results.append({
                            "tool": "ssh-auto-forward",
                            "file": fname,
                            "mbps": round(mbps, 1),
                            "elapsed": round(r["elapsed"], 2),
                        })
                    except Exception as e:
                        print_row([fname, "FAIL", str(e)[:30]], W)

                our_tunnel.terminate()
                our_tunnel.wait(timeout=5)
            except Exception as e:
                print(f"ssh-auto-forward test failed: {e}")

        # ==================== native ssh -L ====================
        if not our_only:
            print("\n" + "=" * 60)
            print("  native ssh -L benchmark")
            print("=" * 60)

            W = [12, 14, 10]
            print_row(["File", "Throughput", "Time"], W)
            print_sep(W)

            for fname, fsize in FILES:
                native_local = next_local
                next_local += 1

                try:
                    tun = start_native_tunnel(TEST_PORT, native_local)
                    try:
                        r = download_file(native_local, f"{fname}.bin")
                        mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
                        print_row([fname, f"{mbps:.1f} MB/s", f"{r['elapsed']:.2f}s"], W)
                        results.append({
                            "tool": "native ssh",
                            "file": fname,
                            "mbps": round(mbps, 1),
                            "elapsed": round(r["elapsed"], 2),
                        })
                    finally:
                        tun.terminate()
                        tun.wait(timeout=5)
                except Exception as e:
                    print_row([fname, "FAIL", str(e)[:30]], W)

        # Save results
        out = os.path.join(BENCH_DIR, "results_remote.json")
        with open(out, "w") as f:
            json.dump({
                "host": REMOTE_HOST,
                "results": results,
            }, f, indent=2)
        print(f"\nResults saved to {out}")

        # Summary comparison
        if not our_only and not native_only:
            print("\n" + "=" * 60)
            print("  Summary")
            print("=" * 60)
            for fname, _ in FILES:
                our = next((r for r in results if r["file"] == fname and r["tool"] == "ssh-auto-forward"), None)
                native = next((r for r in results if r["file"] == fname and r["tool"] == "native ssh"), None)
                if our and native:
                    diff = ((our["mbps"] - native["mbps"]) / native["mbps"]) * 100
                    faster = "faster" if diff > 0 else "slower"
                    print(f"  {fname}: {our['mbps']:.1f} vs {native['mbps']:.1f} MB/s ({abs(diff):.1f}% {faster})")

    finally:
        # Cleanup remote server
        print("\nCleaning up remote...")
        ssh_cmd(f"pkill -f 'python3 -m http.server {TEST_PORT}' 2>/dev/null")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remote SSH benchmark")
    parser.add_argument("--host", default=REMOTE_HOST, help="Remote host (default: from env or hetzner)")
    parser.add_argument("--user", default=REMOTE_USER, help="Remote user (default: from env or alexey)")
    parser.add_argument("--port", type=int, default=REMOTE_PORT, help="Remote SSH port (default: 22)")
    parser.add_argument("--our-only", action="store_true", help="Only test ssh-auto-forward")
    parser.add_argument("--native-only", action="store_true", help="Only test native ssh -L")
    args = parser.parse_args()

    main(host=args.host, user=args.user, port=args.port, our_only=args.our_only, native_only=args.native_only)
