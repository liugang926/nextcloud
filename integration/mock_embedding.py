"""Deterministic OpenAI-compatible embeddings for isolated integration tests.

The vectors have no semantic quality. This service exists only to prove that
WeKnora can parse and index a Nextcloud document without a paid model account.
"""

from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._json({"status": "ok"})

    def do_POST(self):
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_048_576:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            values = payload["input"]
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
                raise ValueError("input must be a non-empty string list")
        except (ValueError, KeyError, json.JSONDecodeError):
            self._json({"error": "invalid embedding request"}, 400)
            return

        data = []
        for index, value in enumerate(values):
            digest = sha256(value.encode("utf-8")).digest()
            vector = [round((int.from_bytes(digest[i * 2:i * 2 + 2], "big") + 1) / 65536, 6) for i in range(3)]
            data.append({"object": "embedding", "index": index, "embedding": vector})
        self._json({"object": "list", "model": payload.get("model", "mock-embed"), "data": data, "usage": {"prompt_tokens": len(values), "total_tokens": len(values)}})

    def _json(self, body, status=200):
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        # Never log input text or credentials from model requests.
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
