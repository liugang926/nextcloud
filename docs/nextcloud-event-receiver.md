# Nextcloud event receiver and connection provisioning

`POST /api/v1/integrations/nextcloud/events` persists change hints in WeKnora.
The route is independently authenticated before user and API key middleware.
An HTTP `202` means the entire batch and its receipt watermark committed to the
database. It does **not** mean that WeKnora reconciled the source, parsed a
document, published a version, or applied a deletion. A background dispatcher
submits source reconciliation jobs from committed inbox rows. The applied
watermark remains at zero until a later complete publication proof is
implemented; HTTP `202` is never an application acknowledgement.

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
| `GET` | `/api/v1/datasource/:id/nextcloud-event-connection` | Read receipt, dispatch, and applied status; never returns a secret. |
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
period, so the old key is rejected at commit. This does not yet provide the
short dual-key overlap described in the PRD: delivery can pause with `401`
between WeKnora rotation and installing the new key in Nextcloud. Keep the
one-time response available while updating the sender. A changed source is reported
as `source_changed` by GET; revoke and then pair again after resolving the
source. Revocation does not require a live Nextcloud call, so it remains
possible after credentials are cleared or the endpoint changes.

Connection provisioning does not advance any event watermark.

## Background reconciliation and status

The dispatcher polls due connections every 30 seconds. For each connection it
claims the current committed receipt watermark under a database lease, then
queues a forced full Nextcloud source scan. The scan reads the current
Nextcloud manifest and binding authorization through the existing connector;
an event hint is never used directly as a delete instruction. Repeated or
coalesced hints can cause another full scan. A failed queue operation or
partial/failed/canceled sync remains retryable with exponential backoff.
Source configuration drift blocks dispatch; revoke blocks further claims and
cancels queued event tasks when they start. A running event task checks the
pinned connection and its running sync log before each knowledge item, and
rejects a fetched cursor from a different Nextcloud instance. A write already
in progress when an administrator revokes the connection can finish; revoke
does not provide an atomic cancellation barrier for that one write.

The administrator GET returns decimal-string watermarks with distinct meanings:

| Field | Meaning |
| --- | --- |
| `received_through_event_id` | Highest event ID committed in the inbox; the value returned by HTTP `202`. |
| `dispatched_through_event_id` | Highest receipt watermark for which the sync queue accepted a job. This does not prove that the job ran or succeeded. |
| `applied_through_event_id` | Highest event ID proven published or deleted. It remains `"0"` in this increment because the asynchronous parser and publication path has no reliable complete-run proof. |
| `backlog_count` | Inbox rows above the applied watermark, including rows already dispatched. |
| `undispatched_count` | Inbox rows above the dispatched watermark. |
| `dispatch_state` | `idle`, `leased`, `queued`, `retry`, or `blocked`. |
| `last_error_code` | Static failure category; contains no source URL or credential. |

The dispatch cursor and queue intent survive process restarts. The event task
creates a running sync log only when it starts. If the process dies before
enqueue, an expired lease with no log can be retried; a task that started is
reconciled by its exact log ID. An event task still marked running after 150
minutes moves to `blocked` with `sync_stale_manual_review`. An administrator
must investigate the source task and repair or re-pair the connection; the
dispatcher does not start a second task that might write concurrently.
Manual and scheduled Nextcloud syncs share the running-log admission slot.
If their queue enqueue result is uncertain, the running log keeps that slot;
if the queue did not actually accept the task, an administrator must verify
the queue and repair the log before another sync can start. These non-event
logs do not use the event dispatcher's 150-minute blocked transition.
`applied_through_event_id` does not advance. Source deletion still follows
the connector's two-complete-scan rule, so an absent file is not treated as
deleted from a single failed or partial manifest. A missing or malformed old
source cursor with prior imported knowledge blocks event dispatch for manual
review; the old inventory is required to find deletions. Manual and scheduled
syncs use the same baseline check. A live instance change fails the sync and
requires manual repair or re-pairing; until repaired, the dispatch state is
`retry` with `sync_not_successful` and may retry on its normal backoff.

Each event dispatch currently requests a true full scan, including content
downloads. The connector accumulates fetched content in memory before applying
it, so the current implementation cannot safely meet the 10,000-file / 100 GB
performance target. The dispatcher polls every 30 seconds, so it also cannot
meet the PRD's event-to-durable-job P95 target of 10 seconds. Neither target
has been accepted. ETag is a change hint, not a content hash, so skipping an
unchanged ETag is not proof of content equivalence.

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

Hints remain non-authoritative. The dispatcher re-reads the current Nextcloud
manifest, authorization state, and file version before publication or deletion.
Outbox retention must not treat `202` or `dispatched_through_event_id` as an
applied checkpoint.
