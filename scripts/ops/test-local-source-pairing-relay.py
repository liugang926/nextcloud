#!/usr/bin/env python3
"""Local contract check for the one-shot pairing fault relay."""

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request


def load_relay():
    path = Path(__file__).with_name("local-source-pairing-relay.py")
    spec = importlib.util.spec_from_file_location("pair_relay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RelayTest(unittest.TestCase):
    def test_only_exact_first_commit_is_blocked(self):
        forwarded = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                forwarded.append((self.command, self.path, body,
                                  self.headers.get("Authorization")))
                answer = b'{"upstream":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            do_GET = handle_request
            do_POST = handle_request

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        relay = load_relay().make_server("owned-binding", "owned-op", 0,
                                         "127.0.0.1", upstream.server_port)
        threads = [threading.Thread(target=server.serve_forever, daemon=True)
                   for server in (upstream, relay)]
        for thread in threads:
            thread.start()
        base = f"http://127.0.0.1:{relay.server_port}"
        path = "/index.php/apps/integration_weknora/api/v1/bindings/owned-binding/source-pairing/commit"

        def post(target, operation):
            req = urllib.request.Request(base + target,
                data=json.dumps({"operation_id": operation}).encode(), method="POST",
                headers={"Authorization": "Bearer hidden-test-token", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        try:
            self.assertEqual(post(path, "another-op"), 200)
            self.assertEqual(post(path.replace("owned-binding", "another-binding"), "owned-op"), 200)
            self.assertEqual(post(path, "owned-op"), 503)
            self.assertEqual(post(path, "owned-op"), 200)
            with urllib.request.urlopen(base + "/__relay_health") as response:
                self.assertEqual(json.load(response), {"ready": True, "blocked": True})
            self.assertEqual(len(forwarded), 3)
            self.assertTrue(all(item[3] == "Bearer hidden-test-token" for item in forwarded))
        finally:
            relay.shutdown()
            upstream.shutdown()
            relay.server_close()
            upstream.server_close()


if __name__ == "__main__":
    unittest.main()
