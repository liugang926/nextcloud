"""Deterministic OpenAI-compatible models for isolated integration tests.

The vectors have no semantic quality. The chat endpoint answers with the
synthetic marker only when that marker appears in its supplied context. It
allows an isolated question/citation drill without a paid model account.
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
        if self.path == "/v1/chat/completions":
            self._chat_completion()
            return
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

    def _chat_completion(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_048_576:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            messages = payload["messages"]
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a non-empty list")
            content = json.dumps(messages, ensure_ascii=False)
        except (ValueError, KeyError, json.JSONDecodeError, TypeError):
            self._json({"error": "invalid chat request"}, 400)
            return
        # This fixed value is present only in the disposable fixture file.
        answer = ("ORCHID-QUARTZ-2749" if "ORCHID-QUARTZ-2749" in content
                  else "NO_SYNTHETIC_CONTEXT")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for choice in (
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ):
                self.wfile.write(("data: " + json.dumps(choice, separators=(",", ":")) +
                                  "\n\n").encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._json({"choices": [{"index": 0,
                                      "message": {"role": "assistant", "content": answer},
                                      "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                  "total_tokens": 2}})

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
