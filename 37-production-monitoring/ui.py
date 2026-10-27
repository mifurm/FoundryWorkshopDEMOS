"""Loopback-only controller for real deployed agents and native evaluation results."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
import traceback

from azure.core.exceptions import AzureError
import httpx
from openai import OpenAIError

import demo

HERE = Path(__file__).resolve().parent
operation = Lock()


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
        return self.headers.get("Host") in hosts and self.headers.get("Origin") in (
            None, *(f"http://{host}" for host in hosts),
        )

    def do_GET(self) -> None:
        if not self.allowed():
            self.respond(403, b'{"error":"Loopback origin required"}')
            return
        if self.path == "/":
            self.respond(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        try:
            if self.path == "/api/status":
                report = demo.STATE / "live-report.json"
                result = {
                    "configuration": demo.configuration(),
                    "last_live_test": json.loads(report.read_text()) if report.exists() else None,
                }
            elif self.path == "/api/evaluations":
                result = {"evaluations": demo.evaluation_results()}
            elif self.path == "/api/traces":
                result = demo.telemetry()
            else:
                self.respond(404, b'{"error":"Not found"}')
                return
            self.respond(200, json.dumps(result).encode())
        except (AzureError, httpx.HTTPError, OpenAIError, RuntimeError) as error:
            traceback.print_exc()
            self.respond(502, json.dumps({"error": str(error)}).encode())

    def do_POST(self) -> None:
        if not self.allowed():
            self.respond(403, b'{"error":"Loopback origin required"}')
            return
        if self.path != "/api/traffic":
            self.respond(404, b'{"error":"Not found"}')
            return
        if not operation.acquire(blocking=False):
            self.respond(409, b'{"error":"Another scenario is running"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("Request body must be 1-4096 bytes")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            records = demo.traffic(data.get("mode"))
            self.respond(200, json.dumps({"requests": records}).encode())
        except ValueError as error:
            self.respond(400, json.dumps({"error": str(error)}).encode())
        except (AzureError, httpx.HTTPError, OpenAIError, RuntimeError) as error:
            traceback.print_exc()
            self.respond(502, json.dumps({"error": str(error)}).encode())
        finally:
            operation.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8037)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Lab 37 UI: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        demo.set_mode("recovery")


if __name__ == "__main__":
    main()
