"""Loopback-only UI. Azure CLI credentials stay on this server, not in the browser."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import traceback

from azure.core.exceptions import AzureError
import httpx

import demo

HERE = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, data: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self) -> bool:
        port = self.server.server_port
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        return (
            self.headers.get("Host") in hosts
            and self.headers.get("Origin") in (None, *(f"http://{host}" for host in hosts))
        )

    def do_GET(self) -> None:
        if not self.allowed():
            self.respond(403, b'{"error":"Loopback origin required"}')
        elif self.path == "/":
            self.respond(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            paths = {"agent": "agent.json", "last_live_test": "live-report.json"}
            data = {
                key: json.loads((demo.STATE / name).read_text()) if (demo.STATE / name).exists() else None
                for key, name in paths.items()
            }
            self.respond(200, json.dumps(data).encode())
        else:
            self.respond(404, b'{"error":"Not found"}')

    def do_POST(self) -> None:
        if not self.allowed():
            self.respond(403, b'{"error":"Loopback origin required"}')
            return
        if self.path != "/api/invoke":
            self.respond(404, b'{"error":"Not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("Request body must be 1-4096 bytes")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            result = demo.invoke(data.get("attendees"), data.get("session_id", ""))
            self.respond(200, json.dumps(result).encode())
        except ValueError as error:
            self.respond(400, json.dumps({"error": str(error)}).encode())
        except (AzureError, httpx.HTTPError, RuntimeError) as error:
            traceback.print_exc()
            self.respond(502, json.dumps({"error": str(error)}).encode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8036)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Lab 36 UI: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
