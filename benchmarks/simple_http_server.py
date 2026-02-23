#!/usr/bin/env python3
"""A simple HTTP server that handles broken pipes gracefully."""

import http.server
import socketserver
import sys
import signal
import os

PORT = 8080
DIRECTORY = os.path.expanduser("~/bench")

class QuietHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Request handler that suppresses broken pipe errors."""

    def log_message(self, format, *args):
        """Suppress log messages."""
        pass

    def do_GET(self):
        """Handle GET request with broken pipe handling."""
        try:
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        except BrokenPipeError:
            # Client closed connection, just return
            sys.exit(0)
        except ConnectionResetError:
            # Connection reset by client
            sys.exit(0)

    def end_headers(self):
        """Add headers to prevent caching."""
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def copyfile(self, source, outputfile):
        """Copy file to output with error handling."""
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            # Client closed connection during transfer
            sys.exit(0)

def run_server():
    """Run the HTTP server."""
    os.chdir(DIRECTORY)

    class ReusableServer(socketserver.TCPServer):
        allow_reuse_address = True

    # Bind to 0.0.0.0 so ssh-auto-forward can detect it
    with ReusableServer(("0.0.0.0", PORT), QuietHTTPRequestHandler) as httpd:
        print(f"Serving {DIRECTORY} on port {PORT}", file=sys.stderr)
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
