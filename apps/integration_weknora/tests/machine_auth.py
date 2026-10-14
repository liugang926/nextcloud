"""Test client for the Nextcloud machine request signature contract."""

import hashlib
import hmac
import re
import secrets
import time
import urllib.parse


VERSION = "weknora-hmac-sha256-v1"


def canonical_query(raw):
    pairs = []
    for part in raw.split("&"):
        if not part:
            continue
        raw_key, _, raw_value = part.partition("=")
        if re.search(r"%(?![0-9a-fA-F]{2})", raw_key + "&" + raw_value):
            raise ValueError("malformed percent encoding")
        def encode(value):
            decoded = urllib.parse.unquote_to_bytes(value.replace("+", " "))
            return urllib.parse.quote_from_bytes(decoded, safe="-._~")
        pairs.append((encode(raw_key), encode(raw_value)))
    return "&".join(f"{key}={value}" for key, value in sorted(pairs))


def canonical_request(method, url, body, timestamp, nonce, key_id):
    parsed = urllib.parse.urlsplit(url)
    return "\n".join((
        VERSION,
        method.upper(),
        parsed.path,
        canonical_query(parsed.query),
        hashlib.sha256(body).hexdigest(),
        timestamp,
        nonce,
        key_id,
    )).encode()


def signed_headers(method, url, headers, body=None, *, timestamp=None, nonce=None, key_id=None):
    result = dict(headers)
    authorization = next((value for name, value in result.items()
                          if name.lower() == "authorization"), "")
    if not authorization.lower().startswith("bearer "):
        return result
    token = authorization.split(None, 1)[1]
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    key_id = key_id or result.get("X-WeKnora-Key-Id", "default")
    key = hashlib.sha256(token.encode()).digest()
    canonical = canonical_request(method, url, body or b"", timestamp, nonce, key_id)
    result.update({
        "X-WeKnora-Key-Id": key_id,
        "X-WeKnora-Timestamp": timestamp,
        "X-WeKnora-Nonce": nonce,
        "X-WeKnora-Signature": hmac.new(key, canonical, hashlib.sha256).hexdigest(),
    })
    return result
