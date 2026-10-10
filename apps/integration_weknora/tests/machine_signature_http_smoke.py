#!/usr/bin/env python3
"""Verify signed machine requests, canonical query and replay denial."""

import json
from concurrent.futures import ThreadPoolExecutor
import secrets
import subprocess
import time
import urllib.error
import urllib.request

from machine_auth import canonical_request, signed_headers
from publication_http_smoke import PROJECT, load_env


def raw_request(url, headers, method="GET", body=None):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def sql(statement):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "nextcloud",
         "-d", "nextcloud", "-v", "ON_ERROR_STOP=1", "-Atc", statement],
        cwd=PROJECT, check=True, text=True, capture_output=True,
    )
    return result.stdout.strip()


def main():
    env = load_env()
    api = (f"http://127.0.0.1:{env.get('NEXTCLOUD_HTTP_PORT', '18082')}"
           "/index.php/apps/integration_weknora/api/v1")
    token = env["WEKNORA_SERVICE_TOKEN"]
    bearer = {"Authorization": "Bearer " + token}
    capabilities = f"{api}/capabilities"

    vector = canonical_request(
        "POST", f"{api}/bindings/dev-published/authorize?b=two+words&a=1&empty",
        b'{"file_id":42}', "1780000000", "00112233445566778899aabbccddeeff", "default",
    )
    assert vector.decode().splitlines()[3] == "a=1&b=two%20words&empty="

    assert raw_request(capabilities, bearer)[0] == 401, "Bearer without signature accepted"
    signed = signed_headers("GET", capabilities, bearer)
    status, body = raw_request(capabilities, signed)
    assert status == 200 and json.loads(body)["protocol_version"] == "1", (status, body)
    assert raw_request(capabilities, signed)[0] == 401, "replayed nonce accepted"

    stale = signed_headers("GET", capabilities, bearer, timestamp=1)
    assert raw_request(capabilities, stale)[0] == 401, "stale timestamp accepted"
    unknown_key = signed_headers("GET", capabilities, bearer, key_id="unknown-key")
    assert raw_request(capabilities, unknown_key)[0] == 401, "unknown key ID accepted"

    path_headers = signed_headers("GET", capabilities, bearer)
    assert raw_request(f"{api}/bindings", path_headers)[0] == 401, "path tamper accepted"

    query_url = capabilities + "?a=1&b=two+words"
    query_headers = signed_headers("GET", query_url, bearer)
    assert raw_request(capabilities + "?a=2&b=two+words", query_headers)[0] == 401, "query tamper accepted"
    # Different order and percent encoding have the same canonical query.
    status, _ = raw_request(capabilities + "?b=two%20words&a=1", query_headers)
    assert status == 200, f"equivalent canonical query returned {status}"
    for raw_query in ("x=1&x=2", "x=1&%78=2", "a.b=1", "x=1;y=2"):
        url = capabilities + "?" + raw_query
        duplicate_headers = signed_headers("GET", url, bearer)
        assert raw_request(url, duplicate_headers)[0] == 401, f"duplicate query key accepted: {raw_query}"
        nonce = duplicate_headers["X-WeKnora-Nonce"]
        assert sql(f"SELECT COUNT(*) FROM oc_weknora_request_nonce WHERE nonce = '{nonce}'") == "0"

    authorize = f"{api}/bindings/dev-published/authorize"
    original = b'{"directory_id":"x","object_guid":"00112233-4455-6677-8899-aabbccddeeff","file_id":77}'
    modified = original.replace(b'"file_id":77', b'"file_id":78')
    body_headers = signed_headers("POST", authorize, {**bearer, "Content-Type": "application/json"}, original)
    assert raw_request(authorize, body_headers, "POST", modified)[0] == 401, "body tamper accepted"
    status, body = raw_request(authorize, body_headers, "POST", original)
    assert status == 200 and json.loads(body)["allow"] is False, (status, body)
    assert raw_request(authorize, body_headers, "POST", original)[0] == 401, "POST replay accepted"

    parallel = signed_headers("GET", capabilities, bearer)
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: raw_request(capabilities, parallel)[0], range(8)))
    assert statuses.count(200) == 1 and statuses.count(401) == 7, statuses
    nonce = parallel["X-WeKnora-Nonce"]
    expiry = int(sql(f"SELECT expires_at FROM oc_weknora_request_nonce WHERE nonce = '{nonce}'"))
    assert expiry >= int(time.time()) + 590, expiry

    expired_nonce = secrets.token_hex(16)
    try:
        sql("INSERT INTO oc_weknora_request_nonce (nonce, key_id, expires_at) "
            f"VALUES ('{expired_nonce}', 'expiry-smoke', 1)")
        assert raw_request(capabilities, signed_headers("GET", capabilities, bearer))[0] == 200
        assert sql(f"SELECT COUNT(*) FROM oc_weknora_request_nonce WHERE nonce = '{expired_nonce}'") == "0"
    finally:
        sql(f"DELETE FROM oc_weknora_request_nonce WHERE nonce = '{expired_nonce}'")
    print("machine signature HTTP smoke passed")


if __name__ == "__main__":
    main()
