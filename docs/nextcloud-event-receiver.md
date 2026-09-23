# Nextcloud event receiver and connection provisioning

`POST /api/v1/integrations/nextcloud/events` persists change hints in WeKnora.
The route is independently authenticated before user and API key middleware.
An HTTP `202` means the entire batch and its receipt watermark committed to the
database. It does **not** mean that WeKnora reconciled the source, parsed a
document, published a version, or applied a deletion. This phase does not send
an application acknowledgement or start sync jobs from inbox rows.

Only an `active` connection row may receive events. The row fixes one
Nextcloud instance and binding to one tenant, knowledge base, and Nextcloud
data source. The data source must still select exactly that binding. Pairing
and key provisioning use the administrator endpoints below. Installing the
receiver alone does not create an active connection. Connection secrets are
random and stored only as `enc:v1:` ciphertext using `SYSTEM_AES_KEY`.
The connection also pins the normalized complete Nextcloud base URL and the
SHA-256 fingerprint of the data source's **committed, encrypted** configuration
bytes. An endpoint edit, credential clear/rotation/reprovision, or other
configuration edit invalidates the old connection and requires re-pairing.
The receiver checks this identity inside the same locked transaction as the
inbox write; it does not hash or log a plaintext machine token.

## Administrator connection lifecycle

The following routes require the current tenant's Admin or Owner role and
edit access to the target knowledge base. Tenant API keys do not issue or
recover event secrets. The target must be a committed, dedicated Nextcloud
data source whose sole selected binding still exists in signed live
capabilities/bindings responses. Pairing checks the persisted tenant, KB,
source, base URL, encrypted config fingerprint, and dedicated KB marker again
under the database lock before committing the connection.

| Method | Route | Result |
| --- | --- | --- |
| `POST` | `/api/v1/datasource/:id/nextcloud-event-connection` | Create one active connection; `201` once, then `409` on repeat. |
| `GET` | `/api/v1/datasource/:id/nextcloud-event-connection` | Read connection status and decimal received watermark; never returns a secret. |
| `POST` | `/api/v1/datasource/:id/nextcloud-event-connection/rotate` | Replace the only accepted key immediately; `200` returns the new secret once. |
| `DELETE` | `/api/v1/datasource/:id/nextcloud-event-connection` | Revoke the active connection immediately; `204`. |

A successful pair or rotation returns `connection_id`, `key_id`, a one-time
`secret`, `nextcloud_instance_id`, `binding_id`, `status`, and `receiver_url`.
The receiver URL is the fixed absolute path
`/api/v1/integrations/nextcloud/events`; configure it under the public
WeKnora origin reachable from the Nextcloud server. Store the returned
connection ID, key ID, secret, instance ID, and binding ID in the Nextcloud
sender configuration. No management read can retrieve the secret again. If
the one-time response is lost, inspect status and rotate the key; repeating
Pair will not reveal an existing key. Rotation clears any previous-key grace
period, so the old key is rejected at commit. A changed source is reported
as `source_changed` by GET; revoke and then pair again after resolving the
source. Revocation does not require a live Nextcloud call, so it remains
possible after credentials are cleared or the endpoint changes.

Connection provisioning does not advance an applied checkpoint. The only
watermark exposed here is `received_through_event_id` for durable receipt.

## Request contract

The request body is JSON, at most 1 MiB and 200 hints. Event IDs and
`after_event_id` are decimal strings because database IDs may exceed the
integer precision of a JavaScript number. IDs are strictly increasing within
a batch; gaps are expected because Nextcloud allocates IDs across bindings.

```json
{
  "connection_id": "an-opaque-paired-connection-id",
  "nextcloud_instance_id": "example-instance",
  "binding_id": "example-binding",
  "after_event_id": "0",
  "events": [
    {"event_id": "41", "file_id": 123, "type": "upsert", "etag": "example-etag"}
  ]
}
```

Send one value each for `X-Nextcloud-Connection-Id`, `X-Nextcloud-Key-Id`,
`X-Nextcloud-Timestamp` (Unix seconds), `X-Nextcloud-Nonce` (32 lowercase hex
characters), and `X-Nextcloud-Signature` (64 lowercase hex characters).
The timestamp must be within five minutes of the receiver's clock. Each
connection/key/nonce can be consumed once. Retries must generate a fresh
nonce and signature.

The signature is lowercase hex HMAC-SHA256 using the paired, decrypted
connection secret as the key. Its message is these lines joined by `\n`,
without a trailing newline:

1. `nextcloud-event-hmac-sha256-v1`
2. `POST`
3. `/api/v1/integrations/nextcloud/events`
4. an empty line (queries are rejected)
5. lowercase hex SHA-256 of the exact request body bytes
6. timestamp header value
7. nonce header value
8. connection ID header value
9. key ID header value

The receiver reads the committed connection key under a database lock. A
previous key is accepted only while its explicit rotation window remains
valid. An expired or revoked key fails closed, including when another web
worker has a stale configuration cache.

`after_event_id` must equal the receiver's `received_id` for a new batch.
Replaying an older batch with a fresh nonce is accepted only if every event
ID and fingerprint already exist and none extends past the current watermark.
Otherwise the route returns `409` without advancing the watermark. The
nonce, all inbox rows, and the new watermark are one transaction, so a
database error cannot produce a false durable receipt. Reusing a nonce is
rejected even for an otherwise identical retry.

Hints remain non-authoritative. A future consumer must re-read the current
Nextcloud manifest, authorization state, and file version before publication
or deletion. Outbox retention must not treat `202` as an applied checkpoint.
