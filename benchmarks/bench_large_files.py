#!/usr/bin/env python3
"""Benchmark: large file downloads through SSH tunnels.

Tests downloading pre-generated files (10 MB, 100 MB, 1 GB) and a
10 GB stream generated on the fly. Compares ssh-auto-forward vs native ssh -L.

Architecture:
  Process 1: Docker container with SSH + HTTP servers (only port 22 exposed)
  Process 2: Tunnel process (ssh-auto-forward --cli, or ssh -L)
  Process 3: Download client (separate Python process, like a browser)

Usage:
    uv run python benchmarks/bench_large_files.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bench_common import (
    BENCH_DIR, SERVERS, STREAM_PORT,
    alloc_port, docker_build, docker_start, docker_stop,
    start_our_tunnel, start_native_tunnel, stop_tunnel,
    download_single, print_header, print_row, print_sep,
)

FILES = [
    ("10mb.bin",  "10 MB"),
    ("100mb.bin", "100 MB"),
    ("1gb.bin",   "1 GB"),
]


def main():
    try:
        docker_build()
        docker_start()
        all_results = []

        # ==============================================================
        # ssh-auto-forward: single tunnel for ALL tests (files + stream)
        # ==============================================================
        all_remote = list(SERVERS.values()) + [STREAM_PORT]
        our_tun = None
        try:
            our_tun = start_our_tunnel(all_remote, all_remote)
        except Exception as exc:
            print(f"  [!] ssh-auto-forward tunnel failed: {exc}")

        if our_tun:
            # --- File downloads ---
            print_header("Large file downloads — ssh-auto-forward")
            W = [12, 8, 14, 10]
            print_row(["Server", "File", "Throughput", "Time"], W)
            print_sep(W)

            for server, rport in SERVERS.items():
                for fname, label in FILES:
                    try:
                        r = download_single(f"http://127.0.0.1:{rport}/{fname}")
                        mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
                        print_row([server, label, f"{mbps:.1f} MB/s",
                                   f"{r['elapsed']:.2f}s"], W)
                        all_results.append({
                            "test": "large_file", "server": server,
                            "file": label, "tunnel": "ssh-auto-fwd",
                            "mbps": round(mbps, 1),
                            "elapsed": round(r["elapsed"], 2),
                            "bytes": r["bytes"],
                        })
                    except Exception as exc:
                        print_row([server, label, "FAIL", str(exc)[:30]], W)

            # --- Streaming 10 GB ---
            print_header("Streaming 10 GB — ssh-auto-forward")
            W2 = [14, 10]
            print_row(["Throughput", "Time"], W2)
            print_sep(W2)
            try:
                r = download_single(
                    f"http://127.0.0.1:{STREAM_PORT}/generate/10gb")
                mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
                print_row([f"{mbps:.1f} MB/s", f"{r['elapsed']:.1f}s"], W2)
                all_results.append({
                    "test": "streaming_10gb", "tunnel": "ssh-auto-fwd",
                    "mbps": round(mbps, 1),
                    "elapsed": round(r["elapsed"], 1),
                    "bytes": r["bytes"],
                })
            except Exception as exc:
                print_row(["FAIL", str(exc)[:40]], W2)

            stop_tunnel(our_tun)

        # ==============================================================
        # native ssh -L: one tunnel per test
        # ==============================================================
        print_header("Large file downloads — native ssh -L")
        W = [12, 8, 14, 10]
        print_row(["Server", "File", "Throughput", "Time"], W)
        print_sep(W)

        for server, rport in SERVERS.items():
            for fname, label in FILES:
                lp = alloc_port()
                try:
                    tun = start_native_tunnel(rport, lp)
                    try:
                        r = download_single(f"http://127.0.0.1:{lp}/{fname}")
                    finally:
                        stop_tunnel(tun)

                    mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
                    print_row([server, label, f"{mbps:.1f} MB/s",
                               f"{r['elapsed']:.2f}s"], W)
                    all_results.append({
                        "test": "large_file", "server": server,
                        "file": label, "tunnel": "native ssh",
                        "mbps": round(mbps, 1),
                        "elapsed": round(r["elapsed"], 2),
                        "bytes": r["bytes"],
                    })
                except Exception as exc:
                    print_row([server, label, "FAIL", str(exc)[:30]], W)

        # Streaming 10 GB via native ssh
        print_header("Streaming 10 GB — native ssh -L")
        W2 = [14, 10]
        print_row(["Throughput", "Time"], W2)
        print_sep(W2)

        lp = alloc_port()
        try:
            tun = start_native_tunnel(STREAM_PORT, lp)
            try:
                r = download_single(f"http://127.0.0.1:{lp}/generate/10gb")
            finally:
                stop_tunnel(tun)

            mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
            print_row([f"{mbps:.1f} MB/s", f"{r['elapsed']:.1f}s"], W2)
            all_results.append({
                "test": "streaming_10gb", "tunnel": "native ssh",
                "mbps": round(mbps, 1),
                "elapsed": round(r["elapsed"], 1),
                "bytes": r["bytes"],
            })
        except Exception as exc:
            print_row(["FAIL", str(exc)[:40]], W2)

        # Save results
        out = os.path.join(BENCH_DIR, "results_large_files.json")
        with open(out, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults saved to {out}")

    finally:
        docker_stop()


if __name__ == "__main__":
    main()
