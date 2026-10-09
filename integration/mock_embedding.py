"""Deterministic OpenAI-compatible models for isolated integration tests.

The vectors have no semantic quality. The chat endpoint answers with the
synthetic marker only when that marker appears in its supplied context. It
allows an isolated question/citation drill without a paid model account.
"""

from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import threading
import time
import uuid


class StreamSchedule:
    """One opt-in, bounded delay of a genuine model stream; no app rows exist here."""
    def __init__(self, maximum=0):
        if type(maximum) is not int or not 0 <= maximum <= 20:
            raise ValueError("invalid fixture stream delay maximum")
        self.maximum = maximum
        self.lock = threading.Lock()
        self.pending = None
        self.active = None
        self.last = None
        self.sequence = 0

    def status(self):
        with self.lock:
            return self._status()

    def _status(self):
        item = self.active or self.pending or self.last
        return {"enabled": self.maximum > 0, "maximum_seconds": self.maximum,
                "stream_sequence": self.sequence,
                "operation": {k: v for k, v in (item or {}).items() if k != "release"}}

    def arm(self, operation, seconds):
        if str(uuid.UUID(operation)) != operation or not isinstance(seconds, (int, float)) or \
                isinstance(seconds, bool) or not math.isfinite(seconds) or not 0 < seconds <= self.maximum:
            raise ValueError("invalid fixture stream schedule")
        with self.lock:
            current = self.active or self.pending
            if current:
                if current["operation_id"] == operation and current["delay_seconds"] == seconds:
                    return self._status()
                raise ValueError("fixture stream schedule already active")
            self.pending = {"operation_id": operation, "delay_seconds": seconds, "state": "armed",
                            "armed_at_unix": time.time(), "release": threading.Event()}
            return self._status()

    def claim(self):
        with self.lock:
            self.sequence += 1
            if self.pending is None:
                return None
            self.active, self.pending = self.pending, None
            self.active.update(state="claimed", claimed_stream_sequence=self.sequence, claimed_at_unix=time.time())
            return self.active

    def first_flushed(self, item):
        with self.lock:
            if self.active is not item:
                raise ValueError("fixture stream schedule identity changed")
            item.update(state="waiting_after_first_delta", first_delta_flushed_at_unix=time.time())

    def wait(self, item):
        item["release"].wait(item["delay_seconds"])

    def release(self, operation):
        with self.lock:
            item = self.active or self.pending
            if not item or item["operation_id"] != operation:
                raise ValueError("unknown fixture stream schedule")
            item["release"].set()
            item["released_at_unix"] = time.time()
            return self._status()

    def finish(self, item, succeeded):
        with self.lock:
            if self.active is not item:
                raise ValueError("fixture stream schedule identity changed")
            item.update(state="finished" if succeeded else "stream_disconnected", finished_at_unix=time.time())
            self.last, self.active = item, None


STREAM_SCHEDULE = StreamSchedule(int(os.environ.get("MOCK_CHAT_STREAM_DELAY_MAX_SECONDS", "0")))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/_fixture/chat-stream-control":
            if STREAM_SCHEDULE.maximum == 0 or self.client_address[0] not in {"127.0.0.1", "::1"}:
                self.send_error(404)
                return
            self._json(STREAM_SCHEDULE.status())
            return
        if self.path != "/health":
            self.send_error(404)
            return
        self._json({"status": "ok"})

    def do_POST(self):
        if self.path == "/_fixture/chat-stream-control":
            if STREAM_SCHEDULE.maximum == 0 or self.client_address[0] not in {"127.0.0.1", "::1"}:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("invalid fixture control size")
                payload = json.loads(self.rfile.read(length))
                if payload.get("action") == "arm":
                    value = STREAM_SCHEDULE.arm(payload["operation_id"], payload["delay_seconds"])
                elif payload.get("action") == "release":
                    value = STREAM_SCHEDULE.release(payload["operation_id"])
                else:
                    raise ValueError("invalid fixture control action")
            except (ValueError, KeyError, TypeError):
                self._json({"error": "fixture stream control refused"}, 409)
                return
            self._json(value)
            return
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
            scheduled = STREAM_SCHEDULE.claim()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            def emit(choice):
                self.wfile.write(("data: " + json.dumps(choice, separators=(",", ":")) + "\n\n").encode("utf-8"))
            succeeded = False
            try:
                split = max(1, len(answer) // 2) if scheduled else len(answer)
                emit({"choices": [{"index": 0, "delta": {"role": "assistant", "content": answer[:split]}}]})
                if scheduled:
                    self.wfile.flush()
                    STREAM_SCHEDULE.first_flushed(scheduled)
                    STREAM_SCHEDULE.wait(scheduled)
                    if split < len(answer):
                        emit({"choices": [{"index": 0, "delta": {"content": answer[split:]}}]})
                emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                succeeded = True
            finally:
                if scheduled:
                    STREAM_SCHEDULE.finish(scheduled, succeeded)
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
