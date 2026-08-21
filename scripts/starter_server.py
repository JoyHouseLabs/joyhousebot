#!/usr/bin/env python3
"""Serve the local Starter App and proxy its versioned Runtime API calls.

This deliberately has no web-framework dependency.  The local UI is bound to
loopback and uses the same origin for static files and `/v2` / `/control`, so
the development Runtime does not need a broad CORS allowance.
"""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "apps" / "starter" / "dist"
PROXY_PREFIXES = ("/v2", "/control", "/healthz", "/readyz")
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class StarterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    runtime_host = "127.0.0.1"
    runtime_port = 18790

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle(self) -> None:
        path = urlsplit(self.path).path
        if path == "/starter-config":
            self._send_json(
                200,
                {"console_url": f"http://{self.runtime_host}:{self.runtime_port}/ui/"},
            )
            return
        if path.startswith(PROXY_PREFIXES):
            self._proxy()
            return
        if self.command != "GET":
            self._send_error(405, "Static assets only support GET")
            return
        self._serve_static(path)

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR not in candidate.parents and candidate != STATIC_DIR:
            self._send_error(403, "Invalid static path")
            return
        if not candidate.is_file():
            if not STATIC_DIR.exists():
                self._send_error(503, "Starter has not been built; run npm --prefix apps/starter run build")
                return
            self._send_error(404, "Static asset not found")
            return
        payload = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _proxy(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None
        request_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS | {"host"}
        }
        connection = http.client.HTTPConnection(
            self.runtime_host, self.runtime_port, timeout=120
        )
        try:
            connection.request(self.command, self.path, body=body, headers=request_headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError as exc:
            self._send_error(502, f"Runtime proxy unavailable: {exc}")
        finally:
            connection.close()

    def _send_error(self, status: int, detail: str) -> None:
        self._send_json(status, {"detail": detail})

    def _send_json(self, status: int, value: dict[str, str]) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[starter] {self.address_string()} {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5179)
    parser.add_argument("--runtime-host", default="127.0.0.1")
    parser.add_argument("--runtime-port", type=int, default=18790)
    args = parser.parse_args()
    StarterHandler.runtime_host = args.runtime_host
    StarterHandler.runtime_port = args.runtime_port
    server = ThreadingHTTPServer((args.host, args.port), StarterHandler)
    print(f"[starter] ready: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
