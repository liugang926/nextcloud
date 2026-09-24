# Nextcloud event sender

The Nextcloud app can send committed `weknora_outbox` hints to a paired
WeKnora receiver. The sender uses the existing
`nextcloud-event-hmac-sha256-v1` request contract. A successful HTTP `202`
records **durable receipt only**. It does not acknowledge reconciliation,
parsing, publication, or deletion in WeKnora.

## Operator egress approval

Set `WEKNORA_EVENT_ALLOWED_ORIGINS` in every Nextcloud web and background-job
process to a comma-separated list of exact approved origins, for example
`https://weknora.example.com`. The app has no default origin. A connection's
`receiver_url` must be that origin plus the fixed path
`/api/v1/integrations/nextcloud/events`. User information, queries, fragments,
other paths, redirects, and an origin absent from the operator list are
rejected. Raw IP URL hosts are also rejected. HTTPS certificate verification
remains enabled. An approved private
HTTPS host is supported.

The repository's `compose.yaml` is a development stack and explicitly sets
`WEKNORA_EVENT_DEV_HTTP=1` with the fixed Docker origins `http://app:8080` and
`http://event-receiver-smoke:8080`. Do not set that development switch for a
production deployment. An HTTP URL is rejected without it, even if listed.

## Pair and rotate

First create a dedicated Nextcloud data source in WeKnora and issue its event
connection using WeKnora's administrator API. Its one-time response contains
`connection_id`, `key_id`, `secret`, `nextcloud_instance_id`, `binding_id`, and
the fixed `receiver_url` path. Immediately send the credential to the
Nextcloud administrator endpoint with an administrator browser session and
Nextcloud `requesttoken` CSRF header:

```http
POST /index.php/apps/integration_weknora/api/v1/admin/bindings/{binding_id}/event-connection
Content-Type: application/json
requesttoken: <administrator CSRF token>

{
  "binding_id": "dev-published",
  "nextcloud_instance_id": "<from WeKnora Pair response>",
  "connection_id": "<from WeKnora Pair response>",
  "key_id": "<from WeKnora Pair response>",
  "secret": "<one-time secret from WeKnora Pair response>",
  "receiver_url": "https://weknora.example.com/api/v1/integrations/nextcloud/events"
}
```

The app requires the claimed instance ID to match this Nextcloud installation,
the claimed binding ID to match the route, and the binding root to remain
active. It encrypts the secret with Nextcloud `ICrypto` before writing it to
the database. The `201` response and subsequent `GET` expose only connection
metadata, delivery status, retry metadata, and
`received_through_event_id`; they never return the secret or ciphertext.
No secret is put in the URL or app logs.

After WeKnora rotates the connection key, repeat `POST` with the same
`connection_id` and `receiver_url` and its **new** `key_id` and `secret`.
The app replaces its old key and resumes a paused sender while preserving the
durable receipt ID. A different connection ID requires `DELETE` of the old
local configuration and a new pair. Local `DELETE` stops sending and erases
the local ciphertext; revoke the connection in WeKnora separately. Deleting
an unpaired Nextcloud binding removes its sender configuration in the binding's
transaction, and the binding ID cannot be reused. A paired binding cannot be
removed until a remote decommission protocol is available.
WeKnora currently invalidates the old key immediately on rotation. Requests
between WeKnora Rotate and the local `POST` may receive `401` and pause the
sender; install the new one-time secret promptly. A short two-key grace window
is not implemented.

## Delivery and recovery

`EventDeliveryJob` pages at most 200 hints per request and stays below the
receiver's 1 MiB body limit. It signs the exact JSON bytes with a fresh nonce
and timestamp for every attempt. Two workers cannot send the same binding
concurrently: the sender holds its database row lock across a bounded HTTP
request. The receipt ID advances only when HTTP status is `202`, the returned
`connection_id` matches, `durable_receipt_only` is `true`, and the returned
decimal watermark equals the last event in the batch. A dropped local commit
can safely retry the same immutable batch with a fresh nonce.

The database stores attempt count, next retry time, and a non-sensitive error
code. Network errors, `429`, `5xx`, and malformed `202` receipts use
exponential backoff. `401`, `403`, redirects, receiver checkpoint conflicts,
and a valid but divergent receipt pause delivery for administrator review.
`GET /index.php/apps/integration_weknora/api/v1/admin/bindings/{id}/event-connection`
shows that status. Rotation with a fresh WeKnora key resumes a paused sender.
The app does not yet emit an active alert for a paused sender; operators must
monitor this status.
Outbox retention cannot remove unsent hints while a sender exists, including
while paused. It may prune hints already durably received after the configured
minimum retention period; this still does not mean they were applied.

If outbox retention had already passed the new connection's initial ID,
pairing fails closed. Restore the missing outbox and floor from a verified
backup before pairing, or retire the old binding and provision a **new**
binding/data-source identity with a complete source manifest reconciliation.
Do not manually advance the receipt ID to hide a gap. If WeKnora reports a
checkpoint divergence, compare its durable connection status and the local
outbox before re-pairing; a Nextcloud restore may require a new connection and
full source scan.

The job remains registered with a 60-second interval as a fallback under
ordinary Nextcloud cron. The local Compose stack also runs `event-worker`,
which calls the bounded `occ integration_weknora:deliver-events` pass every
five seconds as the `www-data` user. A pass has a 90-second process timeout;
the worker retries after failure, and the database sender row lock serializes
it with ordinary cron. Production operators must schedule and monitor an
equivalent worker. This implementation has no measured end-to-end P95
10-second delivery guarantee.

Local verification:

```sh
python3 apps/integration_weknora/tests/event_delivery_http_smoke.py
docker compose exec -T -u www-data nextcloud php occ integration_weknora:deliver-events
docker compose exec -T -u www-data nextcloud php occ background-job:list \
  --class='OCA\IntegrationWeknora\BackgroundJob\EventDeliveryJob' --output=json
```

The HTTP smoke creates an isolated mock receiver and synthetic binding,
checks signature bytes, retries, false receipts, fairness beyond ten bindings,
pause behavior, and unsent retention, then removes its resources.
