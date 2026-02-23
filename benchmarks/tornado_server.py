#!/usr/bin/env python3
"""A robust Tornado HTTP server for benchmarking.

Tornado handles connections much better than Python's built-in http.server,
especially for SSH tunnel scenarios.
"""

import tornado.ioloop
import tornado.web
import tornado.log
import os
import sys

# Configuration
PORT = 8080
DIRECTORY = os.path.expanduser("~/bench")

class FileHandler(tornado.web.StaticFileHandler):
    """Handler for serving static files."""

    def set_default_headers(self):
        """Set headers for all responses."""
        self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

    def on_connection_close(self):
        """Handle client disconnection gracefully."""
        # Don't log or raise errors when client closes
        pass


def make_app():
    """Create the Tornado application."""
    return tornado.web.Application([
        (r"/(.*)", FileHandler, {"path": DIRECTORY}),
    ])


def main():
    """Run the Tornado server."""
    # Ensure directory exists
    os.makedirs(DIRECTORY, exist_ok=True)
    os.chdir(DIRECTORY)

    # Enable Tornado logging to stderr (for screen session)
    tornado.log.enable_pretty_logging()

    app = make_app()
    print(f"Starting Tornado server on port {PORT}, serving {DIRECTORY}", file=sys.stderr)
    print(f"Access via: http://0.0.0.0:{PORT}/", file=sys.stderr)

    # Listen on all interfaces so ssh-auto-forward can detect it
    app.listen(PORT, address='0.0.0.0')

    # Start the event loop
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
