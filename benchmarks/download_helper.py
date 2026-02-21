#!/usr/bin/env python3
"""Helper: download file(s) through a tunnel and report results as JSON.

Usage:
    python download_helper.py single <url>
    python download_helper.py many <base_url> <count>
    python download_helper.py stress <base_url> <count> <rps>

Output (JSON on stdout):
    single: {"elapsed": 1.23, "bytes": 104857600}
    many:   {"elapsed": 5.67, "files_ok": 500, "bytes": 5120000}
    stress: {"elapsed": 12.3, "files_ok": 100, "failed": 2, "bytes": 1024000,
             "target_rps": 10, "actual_rps": 8.3,
             "latencies_ms": [12.1, 15.3, ...]}
"""

import json
import sys
import threading
import time
import urllib.request


def download_single(url):
    t0 = time.monotonic()
    total = 0
    resp = urllib.request.urlopen(url, timeout=600)
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
    elapsed = time.monotonic() - t0
    return {"elapsed": elapsed, "bytes": total}


def download_many(base_url, count):
    t0 = time.monotonic()
    total = 0
    ok = 0
    for i in range(count):
        url = f"{base_url}/small/page_{i}.html"
        try:
            resp = urllib.request.urlopen(url, timeout=30)
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
            ok += 1
        except Exception:
            pass
    elapsed = time.monotonic() - t0
    return {"elapsed": elapsed, "files_ok": ok, "bytes": total}


def download_stress(base_url, count, target_rps):
    """Fire requests at a fixed rate (target_rps), each in its own thread."""
    interval = 1.0 / target_rps
    results_lock = threading.Lock()
    latencies = []
    total_bytes = 0
    ok = 0
    failed = 0

    def fetch(i):
        nonlocal total_bytes, ok, failed
        url = f"{base_url}/small/page_{i % 1000}.html"
        t0 = time.monotonic()
        try:
            nbytes = 0
            resp = urllib.request.urlopen(url, timeout=30)
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                nbytes += len(chunk)
            lat = (time.monotonic() - t0) * 1000
            with results_lock:
                latencies.append(lat)
                total_bytes += nbytes
                ok += 1
        except Exception:
            with results_lock:
                failed += 1

    threads = []
    t_start = time.monotonic()
    for i in range(count):
        # Schedule at fixed rate
        target_time = t_start + i * interval
        now = time.monotonic()
        if target_time > now:
            time.sleep(target_time - now)

        t = threading.Thread(target=fetch, args=(i,))
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=60)

    elapsed = time.monotonic() - t_start
    actual_rps = ok / elapsed if elapsed > 0 else 0

    latencies.sort()
    return {
        "elapsed": round(elapsed, 2),
        "files_ok": ok,
        "failed": failed,
        "bytes": total_bytes,
        "target_rps": target_rps,
        "actual_rps": round(actual_rps, 1),
        "latencies_ms": [round(x, 1) for x in latencies],
    }


def main():
    mode = sys.argv[1]
    if mode == "single":
        result = download_single(sys.argv[2])
    elif mode == "many":
        result = download_many(sys.argv[2], int(sys.argv[3]))
    elif mode == "stress":
        result = download_stress(sys.argv[2], int(sys.argv[3]),
                                 float(sys.argv[4]))
    else:
        print(json.dumps({"error": f"unknown mode: {mode}"}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
