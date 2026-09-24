#!/usr/bin/env python3
"""Disposable Docker-network receiver for event_delivery_http_smoke.py."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
from pathlib import Path
import time
from urllib.parse import urlsplit


STATE = Path("/state")
PATH = "/api/v1/integrations/nextcloud/events"
STATUS_PATH = PATH + "/status"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        parts = urlsplit(self.path)
        if parts.path != STATUS_PATH:
            self.send_error(404)
            return
        mode = (STATE / "mode").read_text().strip()
        if mode != "accept":
            self.send_error(503)
            return
        config = json.loads((STATE / "config.json").read_text())
        connection_id = config["connection_id"]
        if parts.query != "connection_id=" + connection_id:
            self.send_error(400)
            return
        timestamp = self.headers.get("X-Nextcloud-Timestamp", "")
        nonce = self.headers.get("X-Nextcloud-Nonce", "")
        signature = self.headers.get("X-Nextcloud-Signature", "")
        canonical = "\n".join([
            "nextcloud-event-status-hmac-sha256-v1", "GET", STATUS_PATH,
            parts.query, hashlib.sha256(b"").hexdigest(), timestamp, nonce,
            connection_id, config["key_id"],
        ]).encode()
        expected = hmac.new(config["secret"].encode(), canonical,
                            hashlib.sha256).hexdigest()
        if (self.headers.get("X-Nextcloud-Connection-Id") != connection_id or
                self.headers.get("X-Nextcloud-Key-Id") != config["key_id"] or
                not timestamp.isdecimal() or abs(int(timestamp) - time.time()) > 300 or
                len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce) or
                not hmac.compare_digest(signature, expected)):
            self.send_error(401)
            return
        with (STATE / "status_requests.jsonl").open("a") as output:
            output.write(json.dumps({"query": parts.query, "key_id": config["key_id"]}) + "\n")
        response = {
            "connection_id": connection_id,
            "nextcloud_instance_id": config["nextcloud_instance_id"],
            "binding_id": config["binding_id"],
            "status": "active",
            "received_through_event_id": (STATE / "received").read_text().strip(),
            "applied_through_event_id": (STATE / "applied").read_text().strip(),
        }
        override = STATE / "status_binding_override"
        if override.exists():
            response["binding_id"] = override.read_text().strip()
        data = json.dumps(response, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
        if mode == "accept":
            (STATE / "received").write_text(payload["events"][-1]["event_id"])
        data = json.dumps(receipt, separators=(",", ":")).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
