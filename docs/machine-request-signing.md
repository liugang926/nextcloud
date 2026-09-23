# Signed Nextcloud machine requests

All `/index.php/apps/integration_weknora/api/v1` machine endpoints require a
Bearer token and four headers:

| Header | Value |
| --- | --- |
| `X-WeKnora-Key-Id` | ASCII `[A-Za-z0-9._-]`, 1–64 bytes; default `default` |
| `X-WeKnora-Timestamp` | Decimal Unix seconds, without a sign or leading zero |
| `X-WeKnora-Nonce` | 16 cryptographically random bytes, lowercase hex (32 chars) |
| `X-WeKnora-Signature` | Lowercase hex HMAC-SHA256 (64 chars) |

The HMAC key is `SHA256(raw Bearer token)` as **32 binary bytes**, not the
64-character hex representation. Nextcloud verifies that the supplied Bearer
token hashes to the key selected by `key_id`, then verifies the signature in
constant time. The timestamp must be within 300 seconds of the server clock.
A unique database primary key on `nonce` consumes it atomically across Web
workers. Nonces live for at least 601 seconds and expired rows are pruned on
authenticated machine requests. A missing migration, database error, missing
header, wrong key, stale timestamp, or replay returns HTTP 401.

## Canonical request bytes

Join these **eight** fields with a single LF (`\n`) and no trailing LF:

```text
weknora-hmac-sha256-v1
UPPERCASE_METHOD
RAW_PATH
CANONICAL_QUERY
SHA256_RAW_BODY_LOWERCASE_HEX
TIMESTAMP_HEADER
NONCE_HEADER
KEY_ID_HEADER
```

`RAW_PATH` is the percent-encoded path of the HTTP request target as sent,
including `/index.php` and any deployment prefix. It is not URL-decoded or
resolved against the application route. `CANONICAL_QUERY` is empty when there
is no query. Otherwise split the raw query on `&`, ignore empty segments,
split each segment at its first `=`, and treat an absent value as empty. Decode
percent escapes and `+` as form encoding, then RFC 3986 percent-encode each key
and value (`%20` for spaces, uppercase hex escapes). Sort pairs bytewise by
their encoded key and then encoded value; join as `key=value` with `&`.
Requests with repeated **decoded keys** are invalid, including encoding aliases
such as `x` and `%78`; the Go client refuses to sign them and the PHP verifier
rejects them before consuming a nonce. This avoids a case where
PHP interprets a reordered duplicate as a different effective parameter under
the same signature. Query keys must match `[A-Za-z0-9_-]{1,64}` to avoid PHP's
parameter-name normalization, and a literal semicolon in the raw query is
rejected. Malformed percent escapes are also rejected. Hash the
exact HTTP body bytes transmitted, without reserializing JSON. GET requests use the SHA-256 of
an empty body. The path and query must survive reverse proxies unchanged except
for query order or equivalent form encoding; a proxy path rewrite will cause
signature rejection.

Cross-language test vector: method `POST`, raw path
`/index.php/apps/integration_weknora/api/v1/bindings/dev-published/authorize`,
raw query `b=two+words&a=1&empty`, body `{"file_id":42}`, timestamp
`1780000000`, nonce `00112233445566778899aabbccddeeff`, key ID `default`,
Bearer token `paired-secret`. The canonical query is
`a=1&b=two%20words&empty=`, and the signature is
`204a5da882a867f96220bab1e17d1ed2df2503c288a290fa8c17b3262bb7cc01`.

## Key configuration and rotation

Nextcloud stores the current `service_key_id` (default `default`) and
`service_token_sha256`, plus optional `service_previous_key_id` and
`service_previous_token_sha256`. The Go data source uses `credentials.token`
and `credentials.key_id` (default `default`). Never reuse a token across
different Nextcloud instances. Keep the Bearer token and HTTPS even though the
request is signed.

For a planned rotation, first set the previous token hash and previous key ID
to the current pair. Set a distinct new current key ID, then its new token
hash. Update WeKnora credentials to that new pair and verify signed requests.
After in-flight old requests finish, delete `service_previous_key_id` and then
`service_previous_token_sha256`. The previous key is rejected immediately
after the ID deletion commits; Nextcloud reads these four config rows directly
from the database on each request, avoiding stale Web process caches. During
a partial previous-key update only the current key is valid. If a token is
compromised, skip the overlap and revoke it immediately. Machine APIs are
currently scoped to the whole app, so a token holder may request any configured
binding and any mapped principal; per-binding credentials remain future work.

## Approved destination

The WeKnora process rejects a Nextcloud origin unless its operator has set
`WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS` to an exact comma-separated list of HTTPS
origins, for example `https://cloud.example.com`. Each entry contains only a
scheme, host and optional port; the data source may add a deployment path to
that approved origin. Redirects are never followed. The fixed local
`http://nextcloud` Docker endpoint is permitted only when
`WEKNORA_NEXTCLOUD_DEV_HTTP=1`; an HTTP loopback test origin additionally
needs an exact entry in the allowed-origins list. Do not set the development
flag in production.

Once a Nextcloud data source has a stored token, its `settings.base_url` and
connector type cannot be changed through the ordinary data-source update API.
Changing the endpoint requires clearing the old credentials first, changing
the address under the server operator's approved origins, then provisioning a
new credential for that destination. Repository transactions preserve this
rule across concurrent source and credential updates.

Local verification:

```sh
python3 apps/integration_weknora/tests/machine_signature_http_smoke.py
python3 apps/integration_weknora/tests/machine_rotation_http_smoke.py
```
