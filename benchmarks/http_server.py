#!/usr/bin/env python3
"""Simple HTTP file server using Uvicorn (ASGI).

Uvicorn is production-grade and handles connections much better than
Python's built-in http.server.
"""

import os
import sys
from pathlib import Path

# Configuration
PORT = 8080
DIRECTORY = os.path.expanduser("~/bench")

# Try to import FastAPI/Uvicorn
try:
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, Response
    import uvicorn

    app = FastAPI()

    @app.get("/{file_path:path}")
    async def serve_file(file_path: str):
        """Serve a file from the bench directory."""
        file_path = Path(DIRECTORY) / file_path

        if not file_path.exists():
            return Response(status_code=404)

        if file_path.is_file():
            return FileResponse(
                file_path,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )

        # Directory listing
        files = []
        for f in file_path.iterdir():
            if f.is_file():
                files.append(f.name)

        return Response(
            content="\n".join(files) + "\n",
            media_type="text/plain",
            headers={"Cache-Control": "no-cache"},
        )

    HAS_FASTAPI = True

except ImportError:
    HAS_FASTAPI = False
    app = None


def main():
    """Run the Uvicorn server."""
    if not HAS_FASTAPI:
        print("FastAPI/Uvicorn not available, using fallback...", file=sys.stderr)
        import http.server
        import socketserver

        class NoCacheHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
            def end_headers(self):
                self.send_header("Cache-Control", "no-cache")
                super().end_headers()

            def log_message(self, *args):
                pass  # Suppress logging

        os.makedirs(DIRECTORY, exist_ok=True)
        os.chdir(DIRECTORY)
        print(f"Starting fallback server on port {PORT}", file=sys.stderr)

        class ReusableServer(socketserver.TCPServer):
            allow_reuse_address = True

        with ReusableServer(("0.0.0.0", PORT), NoCacheHTTPRequestHandler) as httpd:
            httpd.serve_forever()
        return

    # FastAPI/Uvicorn path
    os.makedirs(DIRECTORY, exist_ok=True)
    os.chdir(DIRECTORY)

    print(f"Starting Uvicorn server on port {PORT}, serving {DIRECTORY}", file=sys.stderr)
    print(f"Access via: http://0.0.0.0:{PORT}/", file=sys.stderr)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
