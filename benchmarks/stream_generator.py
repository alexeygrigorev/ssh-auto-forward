#!/usr/bin/env python3
"""HTTP server that generates large responses on the fly (no disk storage).

Endpoints:
    /generate/<size>    - Generate <size> bytes of random data.
                          Examples: /generate/10gb, /generate/500mb, /generate/1gb

Used for benchmarking large transfers without storing files on disk.
"""

import http.server
import re
import os

CHUNK_SIZE = 65536


def parse_size(s):
    """Parse a human size string like '10gb' or '500mb' into bytes."""
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb)$', s.lower())
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    multipliers = {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4}
    return int(num * multipliers[unit])


class StreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        m = re.match(r'^/generate/(.+)$', self.path)
        if not m:
            self.send_error(404, "Use /generate/<size> e.g. /generate/10gb")
            return

        total = parse_size(m.group(1))
        if total is None:
            self.send_error(400, f"Bad size: {m.group(1)}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.end_headers()

        sent = 0
        while sent < total:
            chunk_len = min(CHUNK_SIZE, total - sent)
            try:
                self.wfile.write(os.urandom(chunk_len))
            except BrokenPipeError:
                return
            sent += chunk_len

    def log_message(self, format, *args):
        pass  # silence logs


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8083
    server = http.server.HTTPServer(("0.0.0.0", port), StreamHandler)
    print(f"Stream generator listening on :{port}")
    server.serve_forever()
