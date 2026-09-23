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
active pairing key is rejected; binding removal retires the pair and its keys
together.

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
