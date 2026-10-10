"""Disposable candidate/intent transport fixture, not a WeKnora implementation."""
import json,secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

TOKENS = {tenant: f"synthetic-onboarding-scoped-secret-{tenant}-0123456789abcdef" for tenant in ("7", "8", "9")}
REQUIRED_TOKEN = "synthetic-onboarding-scoped-secret-7-required-0123456789abcdef"
SERVER_ID = secrets.token_hex(6)

def credential(headers):
    token = headers.get("Authorization", "").removeprefix("Bearer ")
    for tenant, secret in TOKENS.items():
        if secret == token: return tenant, "template-" + tenant, "optional"
    if token == REQUIRED_TOKEN: return "7", "template-7-required", "required"
    return None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parts = urlparse(self.path)
        tenant = parse_qs(parts.query).get("tenant_id", [None])[0]
        template = parse_qs(parts.query).get("processing_template_id", [None])[0]
        if parts.path.endswith("/candidates") and tenant in TOKENS:
            scope = credential(self.headers)
            if not scope or scope[0] != tenant or tenant == "8":
                self.send_json(403, {"error": "onboarding_denied"})
            elif template and template != scope[1]:
                self.send_json(409, {"error": "processing_template_mismatch"})
            else:
                suffix = tenant + ("-required" if scope[2] == "required" else "")
                self.send_json(200, {"tenant_id": int(tenant), "processing_template_id": scope[1],
                    "processing_template_name": "Graph required" if scope[2] == "required" else "Document first",
                    "graph_mode": scope[2], "candidates": [{"id": "blank-" + suffix + "-" + SERVER_ID, "name": "Authorized blank " + suffix}]})
        else:
            self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path.endswith("/operations"):
            tenant = str(body["tenant_id"])
            scope = credential(self.headers)
            if not scope or scope[0] != tenant or tenant == "8":
                self.send_json(403, {"error": "onboarding_denied"})
            elif body["processing_template_id"] != scope[1]:
                self.send_json(409, {"error": "processing_template_mismatch"})
            else:
                self.send_json(200, {"operation": {**body, "knowledge_base_id": body.get("knowledge_base_id") or "new-" + tenant + "-" + body["operation_id"],
                    "state": "reserved", "revision": 1}})
        elif self.path.endswith("/pair"):
            # Deliberate unknown ACK exercises source retention and exact retry.
            self.send_json(202, {"operation": {"operation_id": body["operation_id"], "state": "pending", "revision": 1}})
        else:
            self.send_json(404, {"error": "not_found"})

HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
