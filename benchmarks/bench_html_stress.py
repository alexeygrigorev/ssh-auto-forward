#!/usr/bin/env python3
"""Benchmark: HTML page stress test through SSH tunnels.

Two test rates:
  - 1 req/s (realistic browsing simulation, 30 requests)
  - 10 req/s (stress test, 100 requests)

Measures whether the tunnel can sustain the target rate, and reports
actual throughput, latency percentiles, and failure rate.

Compares ssh-auto-forward vs native ssh -L.

Usage:
    uv run python benchmarks/bench_html_stress.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bench_common import (
    BENCH_DIR, SERVERS, STREAM_PORT,
    alloc_port, docker_build, docker_start, docker_stop,
    start_our_tunnel, start_native_tunnel, stop_tunnel,
    download_stress, print_header, print_row, print_sep,
)

SCENARIOS = [
    {"label": "Realistic browsing", "rps": 1, "total": 30},
    {"label": "Stress test",        "rps": 10, "total": 100},
]


def percentile(sorted_list, p):
    if not sorted_list:
        return 0
    k = (len(sorted_list) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_list):
        return sorted_list[f]
    return sorted_list[f] + (k - f) * (sorted_list[c] - sorted_list[f])


def _format_result(server, tunnel_label, r, W):
    lats = r.get("latencies_ms", [])
    lats.sort()
    p50 = percentile(lats, 50)
    p95 = percentile(lats, 95)
    p99 = percentile(lats, 99)

    print_row([
        server, tunnel_label,
        f"{r['actual_rps']:.1f} r/s",
        f"{p50:.0f}", f"{p95:.0f}", f"{p99:.0f}",
        str(r["failed"]), f"{r['elapsed']:.1f}s",
    ], W)

    return {
        "test": "html_stress", "server": server,
        "tunnel": tunnel_label, "target_rps": r["target_rps"],
        "actual_rps": r["actual_rps"],
        "p50_ms": round(p50, 1), "p95_ms": round(p95, 1),
        "p99_ms": round(p99, 1),
        "failed": r["failed"], "files_ok": r["files_ok"],
        "elapsed": r["elapsed"],
    }


def run_scenario(scenario):
    rps = scenario["rps"]
    total = scenario["total"]

    print_header(f"{scenario['label']} ({total} requests at {rps} req/s)")

    W = [12, 14, 12, 10, 10, 10, 10, 8]
    print_row(["Server", "Tunnel", "Actual RPS", "p50 ms", "p95 ms", "p99 ms",
               "Failed", "Time"], W)
    print_sep(W)

    results = []
    all_remote = list(SERVERS.values()) + [STREAM_PORT]

    # --- ssh-auto-forward: single tunnel for all servers ---
    our_tun = None
    try:
        our_tun = start_our_tunnel(all_remote, all_remote)
    except Exception as exc:
        print(f"  [!] ssh-auto-forward tunnel failed: {exc}")

    if our_tun:
        for server, rport in SERVERS.items():
            try:
                base = f"http://127.0.0.1:{rport}"
                r = download_stress(base, total, rps)
                results.append(_format_result(server, "ssh-auto-fwd", r, W))
            except Exception as exc:
                print_row([server, "ssh-auto-fwd", "FAIL", "-", "-", "-",
                           "-", str(exc)[:20]], W)
        stop_tunnel(our_tun)

    # --- native ssh -L: one tunnel per server ---
    for server, rport in SERVERS.items():
        lp = alloc_port()
        try:
            tun = start_native_tunnel(rport, lp)
            try:
                base = f"http://127.0.0.1:{lp}"
                r = download_stress(base, total, rps)
                results.append(_format_result(server, "native ssh", r, W))
            finally:
                stop_tunnel(tun)
        except Exception as exc:
            print_row([server, "native ssh", "FAIL", "-", "-", "-",
                       "-", str(exc)[:20]], W)

    return results


def main():
    try:
        docker_build()
        docker_start()

        all_results = []
        for scenario in SCENARIOS:
            all_results.extend(run_scenario(scenario))
            print()

        out = os.path.join(BENCH_DIR, "results_html_stress.json")
        with open(out, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"Results saved to {out}")

    finally:
        docker_stop()


if __name__ == "__main__":
    main()
