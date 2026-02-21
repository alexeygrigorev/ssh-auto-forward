#!/usr/bin/env python3
"""Benchmark: buffer size sweep through ssh-auto-forward tunnel.

Tests different buffer sizes (4096, 16384, 65536) on a 100 MB file
download through nginx, using the real ssh-auto-forward --cli process.

The buffer size is patched in the forwarder before starting the CLI.

Usage:
    uv run python benchmarks/bench_buffer_sweep.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bench_common import (
    BENCH_DIR, SERVERS,
    alloc_port, docker_build, docker_start, docker_stop,
    start_our_tunnel_direct, stop_tunnel,
    download_single, print_header, print_row, print_sep,
)

BUFFER_SIZES = [4096, 16384, 65536]


def run_buffer_sweep():
    print_header("Buffer size sweep (nginx, 100 MB, ssh-auto-forward)")

    W = [10, 14, 10]
    print_row(["Buffer", "Throughput", "Time"], W)
    print_sep(W)

    rport = SERVERS["nginx"]
    results = []

    for buf in BUFFER_SIZES:
        lp = alloc_port()
        try:
            tun = start_our_tunnel_direct(rport, lp, buffer_size=buf)
            try:
                r = download_single(f"http://127.0.0.1:{lp}/100mb.bin")
            finally:
                stop_tunnel(tun)

            mbps = r["bytes"] / r["elapsed"] / 1024 / 1024
            print_row([str(buf), f"{mbps:.1f} MB/s", f"{r['elapsed']:.2f}s"], W)
            results.append({
                "test": "buffer_sweep", "buffer": buf,
                "mbps": round(mbps, 1), "elapsed": round(r["elapsed"], 2),
                "bytes": r["bytes"],
            })
        except Exception as exc:
            print_row([str(buf), "FAIL", str(exc)[:30]], W)

    return results


def main():
    try:
        docker_build()
        docker_start()

        results = run_buffer_sweep()

        out = os.path.join(BENCH_DIR, "results_buffer_sweep.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {out}")

    finally:
        docker_stop()


if __name__ == "__main__":
    main()
