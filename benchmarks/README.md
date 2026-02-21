# Benchmarks

Measures the throughput and latency of ssh-auto-forward compared to native `ssh -L` port forwarding.

## Architecture

The benchmarks use three separate processes to mirror real-world usage:

```
Process 1: Docker container           Process 2: Tunnel              Process 3: Client
+--------------------------+          +---------------------+        +------------------+
| SSH server (port 22)     |<-------->| ssh-auto-forward    |        | Python HTTP      |
| nginx        (port 8081) |  SSH     | --cli               |        | client           |
| darkhttpd    (port 8082) |  tunnel  | (or native ssh -L)  |<------>| (urllib)         |
| python http  (port 8080) |          +---------------------+        +------------------+
| stream gen   (port 8083) |          binds local ports              downloads through
+--------------------------+          8080-8083                       localhost tunnel
     Only port 22 exposed
```

Only SSH port 22 is exposed from the Docker container. All HTTP traffic goes through the SSH tunnel, just like a real user would experience.

- Process 1 (Docker): Runs `panubo/sshd` with nginx, darkhttpd, python http.server, and a streaming generator
- Process 2 (Tunnel): Either `ssh-auto-forward --cli` (our tool with dashboard) or native `ssh -L` (baseline)
- Process 3 (Client): Python `urllib` making HTTP requests through the tunnel, like a browser would

## Test Data

| File | Size | Source |
|------|------|--------|
| `10mb.bin` | 10 MB | Pre-generated on disk (`dd if=/dev/urandom`) |
| `100mb.bin` | 100 MB | Pre-generated on disk |
| `1gb.bin` | 1 GB | Pre-generated on disk |
| 10 GB stream | 10 GB | Generated on the fly by `stream_generator.py` |
| `small/page_*.html` | ~10 KB each, 1000 files | Pre-generated HTML with random content |

## Benchmarks

### 1. Large File Downloads (`bench_large_files.py`)

Downloads pre-generated files (10 MB, 100 MB, 1 GB) and a 10 GB on-the-fly stream through each HTTP server.

One ssh-auto-forward tunnel stays open for all tests to match real usage (the user starts the dashboard once and uses it for all downloads).

| Server | File | ssh-auto-forward | native ssh -L |
|--------|------|-----------------|---------------|
| nginx | 10 MB | 121.7 MB/s | 164.4 MB/s |
| nginx | 100 MB | 158.1 MB/s | 287.1 MB/s |
| nginx | 1 GB | 141.6 MB/s | 269.9 MB/s |
| darkhttpd | 10 MB | 106.1 MB/s | 235.3 MB/s |
| darkhttpd | 100 MB | 192.8 MB/s | 299.4 MB/s |
| darkhttpd | 1 GB | 152.6 MB/s | 316.9 MB/s |
| python | 10 MB | 131.4 MB/s | 191.8 MB/s |
| python | 100 MB | 192.9 MB/s | 353.0 MB/s |
| python | 1 GB | 149.9 MB/s | 383.9 MB/s |

10 GB streaming:

| Tunnel | Throughput | Time |
|--------|-----------|------|
| ssh-auto-forward | 151.6 MB/s | 67.6s |
| native ssh -L | 220.7 MB/s | 46.4s |

ssh-auto-forward achieves 45-70% of native ssh throughput for large transfers. The overhead comes from paramiko's Python-based SSH implementation vs OpenSSH's C implementation.

### 2. HTML Stress Test (`bench_html_stress.py`)

Fires HTTP requests for ~10 KB HTML pages at a controlled rate. Tests whether the tunnel can sustain the target request rate without failures.

Realistic browsing (1 req/s, 30 requests):

| Server | Tunnel | Actual RPS | p50 | p95 | p99 | Failed |
|--------|--------|-----------|-----|-----|-----|--------|
| nginx | ssh-auto-fwd | 1.0 r/s | 3 ms | 18 ms | 25 ms | 0 |
| darkhttpd | ssh-auto-fwd | 1.0 r/s | 3 ms | 7 ms | 20 ms | 0 |
| python | ssh-auto-fwd | 1.0 r/s | 3 ms | 4 ms | 19 ms | 0 |
| nginx | native ssh | 1.0 r/s | 2 ms | 2 ms | 3 ms | 0 |
| darkhttpd | native ssh | 1.0 r/s | 2 ms | 2 ms | 3 ms | 0 |
| python | native ssh | 1.0 r/s | 2 ms | 3 ms | 4 ms | 0 |

Stress test (10 req/s, 100 requests):

| Server | Tunnel | Actual RPS | p50 | p95 | p99 | Failed |
|--------|--------|-----------|-----|-----|-----|--------|
| nginx | ssh-auto-fwd | 10.1 r/s | 3 ms | 4 ms | 34 ms | 0 |
| darkhttpd | ssh-auto-fwd | 10.1 r/s | 2 ms | 5 ms | 21 ms | 0 |
| python | ssh-auto-fwd | 10.1 r/s | 3 ms | 9 ms | 38 ms | 0 |
| nginx | native ssh | 10.1 r/s | 2 ms | 2 ms | 2 ms | 0 |
| darkhttpd | native ssh | 10.1 r/s | 1 ms | 2 ms | 2 ms | 0 |
| python | native ssh | 10.1 r/s | 2 ms | 2 ms | 3 ms | 0 |

Both tunnels handle 10 req/s with zero failures. ssh-auto-forward adds ~1 ms median latency overhead. Tail latency (p99) is higher at ~20-38 ms vs ~2-3 ms for native ssh, likely due to paramiko's Python event loop.

### 3. Buffer Size Sweep (`bench_buffer_sweep.py`)

Tests the impact of the internal pipe buffer size on throughput (100 MB file through nginx).

| Buffer Size | Throughput | Time |
|------------|-----------|------|
| 4,096 bytes | 65.1 MB/s | 1.53s |
| 16,384 bytes | 151.9 MB/s | 0.66s |
| 65,536 bytes | 179.6 MB/s | 0.56s |

Increasing the buffer from 4 KB to 64 KB gives a 2.8x throughput improvement. The default was changed from 4096 to 65536 as part of the forwarder fix.

## Running

Prerequisites: Docker, SSH key in `~/.ssh/`

```bash
# Run all benchmarks
uv run python benchmarks/bench_large_files.py
uv run python benchmarks/bench_html_stress.py
uv run python benchmarks/bench_buffer_sweep.py
```

Results are saved as JSON in `benchmarks/results_*.json`.

## Files

| File | Purpose |
|------|---------|
| `bench_large_files.py` | Large file download benchmark (10 MB - 10 GB) |
| `bench_html_stress.py` | HTML page stress test (1 and 10 req/s) |
| `bench_buffer_sweep.py` | Buffer size comparison (4 KB, 16 KB, 64 KB) |
| `bench_common.py` | Shared infrastructure: Docker, tunnels, download clients |
| `tunnel_helper.py` | Subprocess that runs the ssh-auto-forward CLI or direct SSHTunnel |
| `download_helper.py` | Subprocess that downloads files (single, many, stress modes) |
| `Dockerfile` | Docker image with SSH + HTTP servers and test data |
| `nginx-benchmark.conf` | nginx configuration for benchmark serving |
| `stream_generator.py` | HTTP server that generates large responses on the fly |

## Key Findings

1. ssh-auto-forward works correctly -zero failures across all tests, no truncated responses (after the `sendall` fix)
2. Throughput is 45-70% of native ssh for bulk transfers -the overhead is paramiko (Python) vs OpenSSH (C)
3. Latency is excellent for interactive use -2-3 ms median for HTML page requests
4. Buffer size matters -increasing from 4 KB to 64 KB gave 2.8x throughput improvement
5. Handles 10 req/s stress test with zero failures -more than sufficient for web browsing use cases
