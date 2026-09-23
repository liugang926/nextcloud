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
remote_commit_conflict` in the nonsecret pairing status. It currently leaves
a paused pending source and a provenance
tombstone for its KB. Status and retry never reissue the token. An operator
must inspect both sides, abort the pending Nextcloud operation when possible,
and use a new dedicated KB for a fresh operation. There is no automated
WeKnora pending-abort cleanup in this slice. Rotation/recovery of an active
source key is also not implemented. Direct revocation of a pending or active
pairing key is rejected; binding removal retires the pair and its key together.

Nextcloud Stop blocks source reads. Resume does not change the paired tenant,
KB, binding, source ID, or credentials. A pending commit whose publication
epoch changed may only activate if Nextcloud confirms that the exact operation
had already committed. A pending operation that never committed before Stop
must be aborted in Nextcloud and prepared again with a new operation and key.

Paired source configuration and ownership are immutable through generic
create/update/credential routes. Generic resume and sync refuse pending or
legacy unpaired sources; database triggers enforce active pairing on running
sync logs. The event connection has its own credentials and lifecycle.

Migrations are PostgreSQL `000116`/`000117` and SQLite `000035`/`000036`. The intervening
numbers are intentionally reserved for independent evaluation-run changes.
