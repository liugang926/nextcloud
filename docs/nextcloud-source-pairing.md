# Nextcloud source pairing

The source pairing is separate from the optional event-inbox connection. A
source created by the generic `POST /api/v1/datasource` route is not paired.
Existing Nextcloud sources are classified as legacy unpaired; they are not
silently adopted. The paired source uses one dedicated, empty knowledge base
and exactly one Nextcloud binding.

1. A Nextcloud administrator prepares an operation with
   `POST /api/v1/admin/bindings/{id}/source-pairing`, supplying the WeKnora
   tenant ID as a canonical decimal string and the ID of an existing empty
   WeKnora knowledge base. The response contains a one-time key and token.
   Save the `operation_id`, `instance_id`, `publication_epoch`, and `key_id`.
2. An authenticated WeKnora tenant administrator with edit access to that KB
   calls `POST /api/v1/datasource/nextcloud-source-pairings`:

   ```json
   {
     "knowledge_base_id": "<existing empty KB ID>",
     "base_url": "https://nextcloud.example",
     "binding_id": "<Nextcloud binding ID>",
     "operation_id": "<prepared UUID>",
     "instance_id": "<Nextcloud instance ID>",
     "publication_epoch": 0,
     "key_id": "pair_<32 lowercase hex characters>",
     "token": "<one-time machine token>"
   }
   ```

   WeKnora checks the operator-approved URL and signed live capabilities and
   binding registry, then creates a paused source and pending pairing in one
   database transaction. The token is encrypted in the source configuration.
   It signs the exact tuple back to Nextcloud's commit endpoint and activates
   the source only after an exact active ACK.
3. HTTP 201 means active. HTTP 202 with `state: pending` means the signed
   commit or local activation outcome is uncertain. The source remains paused;
   no content is admitted. Read `GET
   /api/v1/datasource/nextcloud-source-pairings/{operation_id}` for nonsecret
   status, then call `POST` on the same path with `/retry`. Retry reads the
   already encrypted local credential; it never requires or reveals the token.
   The identical Nextcloud commit is idempotent, including when the first ACK
   was lost. Repeating the original WeKnora POST with the exact tuple and
   token also reuses the same source.

A deterministic Nextcloud rejection (wrong tuple, revoked key, or stale
uncommitted epoch) returns HTTP 409 with `last_error_code:
remote_commit_conflict` in the nonsecret pairing status. It leaves the source
paused and pending until an administrator retries or aborts the same operation.
Status and retry never reissue the token. Direct revocation of a pending or
active pairing key is rejected. Administrator binding deletion returns HTTP
`409 paired_binding_decommission_required` whenever the binding has a pairing
record, including an aborted pending intent. The source credential and an
abort-only retry verifier remain available. A stopped binding is subject to
the same deletion guard.

## Abort an initial pending pair

A WeKnora tenant administrator with KB edit permission may call `POST
/api/v1/datasource/nextcloud-source-pairings/{operation_id}/abort`, or use
`scripts/ops/local-source-pairing.py abort --binding BINDING --operation-id
UUID`. WeKnora signs the exact original operation, tenant, KB, instance and
binding tuple using the pending source credential. Nextcloud atomically closes
the pending intent and expires that key. The expired verifier is usable only
to repeat this same signed abort if its response was lost; ordinary machine
calls and source commit reject it. Once WeKnora has the remote aborted ACK, it
transactionally removes only the empty paused source and keeps a nonsecret
operation tombstone. The KB may then be used by a new pairing operation, while
the original UUID and key cannot be reused. An active pair cannot be aborted.

HTTP 202 means the remote abort outcome or local cleanup is uncertain. The
source stays paused until `POST .../{operation_id}/abort` is retried; do not
prepare a replacement while the old operation remains pending. A remote 409
leaves the local source intact for inspection. If the token never reached
WeKnora, the Nextcloud administrator can instead `DELETE
/api/v1/admin/bindings/{id}/source-pairing` with its pending
`operation_id`; an idempotent repeat returns the aborted status. After an
operator aborts Nextcloud first, WeKnora's signed abort can still complete
its local cleanup using the original credential. In both cases, use a new
operation UUID for replacement.

## Paired source health

`GET /api/v1/datasource/nextcloud-source-pairings/{operation_id}/health`
requires a tenant administrator session and edit access to the paired
knowledge base. Tenant API keys cannot call it. It returns `Cache-Control:
no-store` and excludes machine credentials, source URLs, file paths, document
content, and free-form error messages. A missing event connection is
represented by `event_inbox: null`; it does not mean the shared task queue is
empty. An aborted pair has no live source and returns HTTP 409.

The `event_inbox` counters are scoped to the paired source's latest event
connection. `unapplied_count` counts durable receipts above the verified
applied watermark, including already dispatched receipts;
`undispatched_count` counts receipts above the dispatch watermark. Its oldest
age is measured from the oldest unapplied receipt. The three watermarks keep
their distinct received, dispatched, and applied meanings. A received event
does not prove that a source scan ran, and a dispatched event does not prove
that a file is ready.

`sync_logs.running_count` and `oldest_running_age_seconds` describe rows
still marked running for this data source. `failed_last_24_hours` and
`partial_last_24_hours` count retained logs whose scan started in the last
24 hours. The background log retention policy can prune older rows;
`latest_status` is `null` when no log remains. These values do not count
the Redis/Asynq queue or any parser jobs that have not written a sync log.

`current_versions` groups only the latest `nextcloud_source_versions` rows
for this tenant, KB, and data source. Its parse counters inspect their
currently referenced, non-deleted knowledge candidates.
`missing_candidate_count` includes a staging or published row whose candidate
is absent, soft-deleted, or outside the paired tenant/KB.
`oldest_staging_age_seconds` is measured from the oldest current staging
version's `updated_at`; it is not the parser task's queue wait. The source
revision ledger and older knowledge rows are deliberately outside these
current-version counts. Even `parse_completed_enabled_count` is not a
per-file publication proof, an index completeness check, or permission to
answer a question. Use the signed current-ETag file-status response and the
query authorization path for those decisions.

## Active source-key rotation

For an established pair, a Nextcloud administrator prepares a distinct UUID
with `POST /api/v1/admin/bindings/{id}/source-pairing/rotation` and JSON
`{"operation_id":"<rotation UUID>"}`. The response returns a `rot_` key ID
and machine token only on HTTP 201. Identical repeats and GET status return
nonsecret metadata. WeKnora's tenant administrator, with KB edit permission,
passes that one-time credential to
`POST /api/v1/datasource/nextcloud-source-pairings/{pair_operation_id}/rotations`:

```json
{"operation_id":"<rotation UUID>","new_key_id":"rot_<32 lowercase hex>","token":"<one-time token>"}
```

WeKnora verifies the same live instance and binding with the new key, stores
the credential encrypted in a pending rotation, and signs the exact tenant,
KB, source, instance, pair operation, and rotation operation tuple to
Nextcloud's `/bindings/{id}/source-pairing/rotation/commit`. Nextcloud then
accepts both keys. The old key has a maximum 24-hour grace interval from
commit. WeKnora atomically switches its data-source config and pinned hash,
then signs `/rotation/finalize` with the new key. The final ACK immediately
removes the old key. A lost commit ACK leaves WeKnora pending; a lost finalize
ACK leaves it switched. `GET` status and `POST .../{rotation_id}/retry`
resume either state using the encrypted local credential. The same signed
remote operation is idempotent.

Before remote commit, the WeKnora administrator may `POST
.../{rotation_id}/abort`. WeKnora signs `/rotation/abort` with the old key.
Nextcloud revokes only the pending new key; an identical abort can be retried
after a lost ACK. WeKnora then marks the local operation aborted and erases
its stored config copies. An administrator can also abort a Nextcloud-only
pending rotation with `DELETE` on its admin rotation route and the operation
UUID. A committed rotation cannot be aborted: use retry to finish the switch
and finalization. If the old key's grace time expires during an outage,
ordinary reads with the old key stop; retry uses the encrypted new key to
complete recovery. A lost one-time token before WeKnora stores it requires
aborting that pending Nextcloud operation and creating a fresh UUID.

Source-key rotation changes the exact source config hash. An optional
WeKnora event-inbox connection pinned to the former hash must be re-paired
after rotation; it does not silently gain the new source credential. The
operator CLI is `scripts/ops/local-source-rotation.py`.

Nextcloud Stop blocks source reads. Resume does not change the paired tenant,
KB, binding, source ID, or credentials. A pending commit whose publication
epoch changed may only activate if Nextcloud confirms that the exact operation
had already committed. A pending operation that never committed before Stop
must be aborted in Nextcloud and prepared again with a new operation and key.

Paired source configuration and ownership are immutable through generic
create/update/credential routes. Generic resume and sync refuse pending or
legacy unpaired sources; database triggers enforce active pairing on running
sync logs. The event connection has its own credentials and lifecycle.

Source pairing uses PostgreSQL `000116`/`000117` and SQLite `000035`/`000036`;
rotation uses PostgreSQL `000118` and SQLite `000037`; initial abort uses
PostgreSQL `000120` and SQLite `000039`. The intervening numbers are used by
independent evaluation-run and garbage-collection changes.

## Paired-binding decommission boundary

An active pair created **after** the empty-inventory migration can be
decommissioned only if it has never admitted a sync, event connection,
knowledge row, chunk or source version. Existing pairs have no durable
never-touched proof and remain blocked even if their current KB looks empty:
historical logs may have been pruned. Any first admission permanently burns
the proof. This is an empty-source retirement path, not general GC.

The follow-up WeKnora migration 124 / SQLite 43 invalidates every earlier
virgin marker, including markers created under migration 123 / SQLite 42.
Only pairs created after that follow-up migration can use this path. It also
retains an operation-ID lineage across a pending pair deletion and rebuild,
so repairing an older pending pair cannot mint a new empty-source proof.

1. A Nextcloud administrator calls `POST
   /api/v1/admin/bindings/{id}/decommission` with a fresh UUID
   `operation_id`. Nextcloud stops publication first, then records the exact
   pair operation, instance, binding, tenant, KB, source, current key and
   stopped publication epoch. A crash between those writes leaves publication
   stopped and the key intact; repeat the same request. Normal Resume is
   blocked once the intent is stored. `GET` on the same path shows nonsecret
   status.
2. A WeKnora tenant administrator with KB edit rights calls `POST
   /api/v1/datasource/nextcloud-source-pairings/{pair_operation_id}/decommission`
   with the same `operation_id`. WeKnora signs `GET
   /api/v1/bindings/{id}/decommission/{operation_id}` using the current pair
   key and compares every identity field. Under a KB lock it requires the
   permanent never-touched proof and zero sync logs, event connections and
   inbox/dispatch state, source versions, GC jobs, knowledge rows and chunks.
   It then pauses the source and records its decommission operation in one
   transaction. Database guards reject later source resume and content/event
   admission. A signed, idempotent ACK to Nextcloud certifies a complete
   **empty** inventory and zero visible/running work. HTTP 202 from WeKnora
   means the remote ACK or local checkpoint is uncertain; retry the same UUID.
   `GET` on the WeKnora decommission path reads its nonsecret checkpoint.
3. Only after **both** Nextcloud and WeKnora status say `acknowledged`, its administrator calls
   `POST /api/v1/admin/bindings/{id}/decommission/finalize` with that UUID.
   Nextcloud atomically retires the binding ID, pair, event connection and all
   machine credentials. WeKnora retains its paused data-source and immutable
   pair identity as a decommission tombstone; the separate decommission row
   records `acknowledged` and the DB guards prevent resumption. Its historical
   pair row still says `active`, which does not mean the source is runnable.
   An exact Nextcloud repeat reports `finalized`. Ordinary
   `DELETE /admin/bindings/{id}` retains its 409 guard throughout.

An empty-inventory ACK cannot be used for a source with any indexed history;
there is no physical-copy cleanup to claim in this path. This protocol retires
the Nextcloud binding and durably disables the empty WeKnora source, but does
not delete the WeKnora source or its historical pair metadata. Failed or uncertain
remote steps retain the stopped binding and its credentials for retry. Neither
side deletes Nextcloud originals. Do not edit app configuration, delete
database rows or revoke the machine credential to bypass the protocol.

A general decommission still needs a durable operation ID tied to the exact pair
operation, instance, binding, tenant, knowledge base and data source. Nextcloud
must first stop publication and keep credentials. WeKnora must then pause its
source, reject new event/sync/parse writes, withdraw all source versions,
inventory every retained source file and derived object, and acknowledge that
state with a signed, idempotent response. Nextcloud may retire the binding and
credentials only after that acknowledgement is durable. The acknowledgement
must describe logical withdrawal and a complete cleanup inventory; physical
reclamation remains a separate, retryable GC status. In particular, the
current GC scan does not prove a complete per-source inventory when a knowledge
row has no matching source-version row, and derived indexes are not yet
deleted.

An indexed source can enter the separate, non-final withdrawal stage described
in [nextcloud-indexed-withdrawal.md](nextcloud-indexed-withdrawal.md); it cannot
receive an empty-inventory ACK or retire its Nextcloud credential yet.
