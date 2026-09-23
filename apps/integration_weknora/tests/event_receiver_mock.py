#!/usr/bin/env python3
"""Disposable Docker-network receiver for event_delivery_http_smoke.py."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


STATE = Path("/state")
PATH = "/api/v1/integrations/nextcloud/events"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path != PATH:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 1048576:
            self.send_error(413)
            return
        raw = self.rfile.read(length)
        request = {
            "body": raw.decode(),
            "timestamp": self.headers.get("X-Nextcloud-Timestamp"),
            "nonce": self.headers.get("X-Nextcloud-Nonce"),
            "connection_id": self.headers.get("X-Nextcloud-Connection-Id"),
            "key_id": self.headers.get("X-Nextcloud-Key-Id"),
            "signature": self.headers.get("X-Nextcloud-Signature"),
        }
        with (STATE / "requests.jsonl").open("a") as output:
            output.write(json.dumps(request, separators=(",", ":")) + "\n")
        mode = (STATE / "mode").read_text().strip()
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://metadata.invalid/private")
            self.end_headers()
            return
        if mode in ("503", "401"):
            self.send_response(int(mode))
            self.end_headers()
            return
        payload = json.loads(raw)
        receipt = {
            "connection_id": payload["connection_id"],
            "received_through_event_id": payload["events"][-1]["event_id"],
            "durable_receipt_only": True,
        }
        if mode == "bad_receipt":
            receipt["connection_id"] = "wrong-connection"
        if mode == "ahead_receipt":
            receipt["received_through_event_id"] = str(
                int(receipt["received_through_event_id"]) + 1
            )
        data = json.dumps(receipt, separators=(",", ":")).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
