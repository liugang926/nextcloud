#!/usr/bin/env python3
"""One-shot local fault relay for local-source-pairing-abort-smoke.py.

Run only in a disposable WeKnora app container's network namespace. The
listener is loopback-only, forwards to the fixed `nextcloud` Compose service,
and never logs requests, headers, bodies, or responses.
"""

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


API = "/index.php/apps/integration_weknora/api/v1"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}
MAX_BODY = 1 << 20
MAX_RESPONSE = 4 << 20


def make_server(binding, operation_id, port, upstream_host="nextcloud", upstream_port=80):
    commit_path = f"{API}/bindings/{binding}/source-pairing/commit"
    lock = threading.Lock()
    faulted = False

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_request(self):
            nonlocal faulted
            if self.command == "GET" and self.path == "/__relay_health":
                with lock:
                    blocked = faulted
                self.reply(200, json.dumps({"ready": True, "blocked": blocked}).encode())
                return
            if self.headers.get("Transfer-Encoding") or not self.headers.get("Content-Length", "0").isdigit():
                self.reply(400, b"{}")
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size > MAX_BODY:
                self.reply(413, b"{}")
                return
            body = self.rfile.read(size)
            if self.command == "POST" and self.path == commit_path:
                try:
                    operation = json.loads(body).get("operation_id")
                except (ValueError, AttributeError):
                    operation = None
                with lock:
                    if operation == operation_id and not faulted:
                        faulted = True
                        self.reply(503, b'{"error":"synthetic_commit_fault"}')
                        return
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in HOP_HEADERS}
            headers["Host"] = upstream_host
            headers["Content-Length"] = str(len(body))
            conn = http.client.HTTPConnection(upstream_host, upstream_port, timeout=20)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    self.reply(502, b"{}")
                    return
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, http.client.HTTPException):
                self.reply(502, b"{}")
            finally:
                conn.close()

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    make_server(args.binding, args.operation_id, args.port).serve_forever()
