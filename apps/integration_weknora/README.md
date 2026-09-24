# WeKnora Integration for Nextcloud 34

This app exposes service read APIs and administrator publication operations under `/index.php/apps/integration_weknora/api/v1`. Every machine API request requires `Authorization: Bearer <service token>` plus the versioned HMAC headers described in [the request-signing contract](../../docs/machine-request-signing.md). A nonce is consumed once in the Nextcloud database; a Bearer-only request is rejected. Administrator operations require a logged-in Nextcloud system administrator and a valid CSRF request token; the service token cannot call them.

Only the SHA-256 hash of each machine token is stored in the database; the
hash's 32 raw bytes are the HMAC key. A Nextcloud system administrator creates
the source binding with `POST /admin/bindings`, then issues a key for it with
`POST /admin/bindings/{id}/keys` and JSON `{"key_id":"unique-id"}`. This
CSRF-protected response returns the plaintext token once. Set that token and
key ID as the WeKnora data source's `credentials.token` and
`credentials.key_id`. See [the signing contract](../../docs/machine-request-signing.md)
for the exact rotation and migration procedure. The local Docker
`scripts/bootstrap.sh` creates a sample `dev-published` binding and its
`default` key from the local `.env` fixture.

`root_file_id` must identify a child folder in the `owner_uid` user's files;
binding the user's entire files root is refused. The binding ID uses letters,
digits, `_`, and `-`. Missing or invalid scoped credentials reject all machine
requests. Invalid binding configuration returns HTTP 503. A machine key sees
only its binding and cannot call a different binding's source endpoints.

Service read endpoints:

- `GET /capabilities`: protocol version and Nextcloud instance ID.
- `GET /bindings`: the authenticated key's one binding ID, name, and root file ID.
- `GET /bindings/{id}/manifest[?cursor=...]`: readable, non-withdrawn files recursively under the binding root. Each page has at most 200 items plus a generation, `complete` flag, and `next_cursor`. The first page stores a ten-minute immutable list; later pages use that list. A changed source or expired list returns HTTP 409; restart the traversal rather than applying a partial manifest.
- `GET /bindings/{id}/files/{fileId}/content`: streamed file bytes with `Content-Type` and `ETag`. An optional `If-Match` header rejects a changed version with HTTP 412. Withdrawn files return HTTP 404.
- `GET /bindings/{id}/changes[?cursor=...]`: ordered file-change hints with a signed cursor. The feed is a wakeup and reconciliation aid, not authority for physical deletion. A 409 response requires a complete rescan.

The change outbox is pruned by a Nextcloud background job once per day. The
default retention period is 30 days. Administrators may lengthen it with
`php occ config:app:set integration_weknora outbox_retention_days --value=90`;
values from 0 to 29 days are clamped to 30. Invalid values stop cleanup and
appear as a background job error, avoiding unexpected early deletion. The job
needs Nextcloud background jobs to be running (the local Compose stack has a
`cron` service). It removes up to 1,000 old events per binding per run and
advances that binding's durable `weknora_change_floor` in the same database
transaction as deletion. A recent event blocks deletion of later events in that
binding, even if their timestamps appear older, so the floor cannot skip an event
still in the journal.

An old signed change cursor below the floor receives HTTP 409 with
`rescan_required: true`. The connector must finish a full manifest
reconciliation before using the response's `next_cursor` checkpoint. Events
above the floor remain available during that reconciliation. Append, page,
and cleanup all take the same database row lock, so a page cannot observe a
new floor with old rows or old floor with deleted rows. A long backlog can
take several daily runs to clear; cleanup is deliberately batched to limit
database lock time.

After upgrading the local Compose app, run
`python3 apps/integration_weknora/tests/outbox_retention_http_smoke.py` to
exercise expiry, floor 409, timestamp-order safety, append after pruning,
and page serialization against the database lock. The test uses a temporary
binding and removes its files and database rows.

Administrator endpoints (browser session and CSRF protected):

- `GET /admin/bindings`: list full binding configuration.
- `POST /admin/bindings`: create a binding or update its name with JSON fields `id`, `name`, `owner_uid`, `root_file_id`. The root must be a readable child folder of the owner. Overlapping roots are rejected, and an existing binding cannot silently change its owner or root. V1 configuration accepts bindings for one owner account only, because overlapping shared mounts across owners cannot yet be proved disjoint.
- `DELETE /admin/bindings/{id}`: remove an unpaired binding and revoke all of its machine keys atomically. A paired binding continues to return 409. Its ID is permanently retired because publication and change history are keyed by that ID.
- `POST /admin/bindings/{id}/decommission` and `GET` on the same path: stop publication and persist/read a retryable, exact-pair retirement intent. `POST /admin/bindings/{id}/decommission/finalize` retires the binding and credentials only after a signed empty-inventory acknowledgement from WeKnora. This path is limited to newly paired sources with no sync or indexed history; see [the protocol](../../docs/nextcloud-source-pairing.md).
- `GET /admin/bindings/{id}/keys`: list key IDs and creation metadata, never token values or hashes.
- `POST /admin/bindings/{id}/keys`: issue a unique key ID for this binding and return its token exactly once.
- `DELETE /admin/bindings/{id}/keys/{keyId}`: revoke a key immediately. A second key ID can overlap on the same binding during rotation.
- `POST /admin/bindings/{id}/source-pairing`: prepare an intent with JSON `operation_id` (UUID), `tenant_id` (canonical decimal string), and `knowledge_base_id`. The first HTTP 201 returns `{pairing, token}`. The `key_id` in `pairing` is derived from the operation ID. An exact retry returns HTTP 200 and never repeats the token.
- `GET /admin/bindings/{id}/source-pairing`: return the latest source-pairing status without a token.
- `DELETE /admin/bindings/{id}/source-pairing`: abort a pending operation with JSON `operation_id` and atomically revoke its prepared key. An active pair cannot be aborted this way.
- `GET /admin/bindings/{id}/files/{fileId}/publication`: current `eligible` or `withdrawn` state.
- `POST /admin/bindings/{id}/files/{fileId}/withdraw`: persist exclusion from the manifest and content API.
- `POST /admin/bindings/{id}/files/{fileId}/republish`: explicitly clear the exclusion.

The WeKnora source-pairing coordinator calls signed machine endpoint
`POST /bindings/{id}/source-pairing/commit` with JSON `operation_id`,
`instance_id`, `tenant_id` (canonical decimal string), `knowledge_base_id`,
and `data_source_id`. The HMAC key must be the key created by that pending
operation. The response is HTTP 200 with `{pairing, changed}`; an exact retry
returns `changed: false`. Different source or target identities return HTTP 409.
The operation records the binding root and publication epoch at prepare time;
a stopped binding returns HTTP 423 and a pending operation cannot commit after
stop/resume or a root move. An already active pair survives stop/resume, and
its source reads continue to obey the publication gate. A moved root of an
active pair fails closed. Pair status remains visible after an error so an
operator can retry or abort the pending operation. Binding removal retires its
pairing record and revokes its machine key; it does not delete WeKnora data.
Source pairing is separate from the event-delivery connection below.

The Nextcloud side records the expected tenant and knowledge-base IDs; the
WeKnora administrator pairing API must validate their actual ownership and
dedicated, empty state before committing. This source-side protocol does not
establish employee AD identity or prove access to indexed knowledge. Run
`python3 apps/integration_weknora/tests/source_pairing_http_smoke.py` after
installing/upgrading the app to exercise the Nextcloud half with a temporary
binding and synthetic target IDs.

Publication state and an action audit are stored in the Nextcloud database through app migrations. Withdrawal records a denial for a file ID scoped to an existing binding even if that file has already been moved or deleted; it can also reserve a file ID for exclusion before it enters the binding. Republish requires the file to be currently readable under the bound root. Withdrawal blocks this app's read APIs immediately. Removing an already indexed copy from WeKnora still depends on connector reconciliation and WeKnora's retrieval controls.

The manifest generation is a SHA-256 digest of the binding and sorted file metadata, independent of the request host. The first page still scans the entire binding, and each later page reads and decodes the stored list. Large directories need load and fault testing before production use.

After bootstrapping the local Compose stack, run `python3 apps/integration_weknora/tests/publication_http_smoke.py --file-id <sample-file-id>` to verify administrator and non-administrator access, CSRF, withdrawal, republish, manifest/content exclusion, and binding overlap. The test restores the sample file's original publication state.

## V1 source authorization and identity mapping

The paired connector calls `POST /bindings/{id}/authorize` with the signed
machine headers and a JSON body containing
`directory_id`, canonical UUID-form `object_guid`, and integer `file_id`.
The caller must derive the directory identity from its own verified user
session, never from browser-submitted identity fields. A successful check
returns `allow`, `reason`, `policy_revision`, and `checked_at`. Ordinary denials
return HTTP 200 with `allow: false`; malformed requests return 400 and an
invalid machine credential returns 401. If the binding registry, database,
mount, or permission state cannot be checked, the endpoint returns 503 with
`allow: false`. All responses use `Cache-Control: no-store`. WeKnora must
perform a fresh check for each protected read and treat every non-allow result
as denied; `policy_revision` is a fingerprint of the observed effective
permission chain, not a safe cache lifetime or an AD change feed.

Nextcloud system administrators maintain exact one-to-one mappings through
session and CSRF protected endpoints:

- `GET /admin/identities` lists current mappings.
- `POST /admin/identities` creates an immutable mapping from `directory_id`
  and `object_guid` to `nextcloud_uid`. An identical retry is idempotent;
  conflicting principals or Nextcloud UIDs return 409.
- `POST /admin/identities/revoke` removes the exact mapping identified by all
  three fields. Replacing a link requires explicit revocation followed by
  creation, so access is denied between the two operations.

Mappings and their create/revoke audit records are stored in the Nextcloud
database. Configure the exact WeKnora directory namespace on Nextcloud with
`php occ config:app:set integration_weknora ad_directory_id --value='corp-ad'`.
The account must use Nextcloud's LDAP backend. Mapping creation and each
authorization read its current binary AD `objectGUID` through Nextcloud's
public LDAP provider and a fresh LDAP connection, convert it to canonical UUID
form, and compare it with the claimed GUID. A missing LDAP provider, directory
configuration, entry or GUID, or a failed LDAP read prevents mapping creation
or access. The account's backend class is pinned at creation and rechecked on
every authorization; disabled or missing accounts are denied. Authorization
resolves the binding root and target file through the mapped user's mount view
and checks each directory in the path for read access, plus publication state.
No mapping is inferred from email, display name, or Nextcloud UID similarity.

This proves the UID-to-GUID relationship against Nextcloud's configured AD.
The directory ID remains an operator-configured namespace. Confirm WeKnora and
Nextcloud use the same enterprise AD and run the real-user permission and
team-folder ACL matrix before production use. The connector's service token
remains a high-trust credential: the holder can
submit any mapped directory identity within its one binding. Binding-scoped
keys prevent it from reading other configured roots, and revocation takes
effect without a Web restart. The machine signature alone does not prove that
a querying person owns the GUID in the authorization body. WeKnora must
authenticate that person and resolve the stable GUID independently; use TLS for
the machine call. The paired patch enforces this API's live decision on its
tested read paths. An enterprise entry-point and permission-matrix audit is
still required before production use.

After the local Compose upgrade, run
`python3 apps/integration_weknora/tests/authorization_http_smoke.py --file-id <sample-file-id>`.
The test covers machine authentication, administrator and CSRF controls,
mapping conflicts, sharing and share revocation, disabled accounts, and
publication withdrawal. It removes the temporary account and share and
restores the sample publication state.

The local account HTTP smokes use synthetic GUIDs. For those probes only, set
`WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1` in the repository's local Compose
environment and recreate its Nextcloud container. This bypass is accepted only
alongside the repository Compose's `WEKNORA_LOCAL_COMPOSE=1` and
`WEKNORA_EVENT_DEV_HTTP=1`; it is disabled by default and must be
removed before any enterprise AD or production test. With the bypass off,
non-LDAP accounts and unverified GUIDs cannot be mapped or authorized.

## Employee Files sidebar

The WeKnora tab in Nextcloud 34's Files sidebar
calls `GET /files/{fileId}/status` with the current Nextcloud browser session.
It returns 404 for a file the current user cannot read. It only names a binding
after verifying that the user can read its root and this file inside it. The
source-side states are `in_scope`, `withdrawn`, and `outside_scope`.

`in_scope` means only that the connector may read the current source. When an
active source pair and event connection both exist, Nextcloud makes a bounded
machine request to WeKnora's `/api/v1/integrations/nextcloud/files/status`.
The connection's HMAC secret signs the exact connection, file ID and current
source ETag request. WeKnora verifies the active connection and source pair,
reads its version and candidate rows, and signs the response body. Nextcloud
checks the response signature, full pair tuple, file ID and ETag, then checks
the user's source access and ETag again. It reports `ready` only if the signed
response proves that the candidate is completed, enabled and published for
that same ETag. An older published ETag or a staged candidate is `updating`;
a failed candidate for the current ETag is `failed`. No row, invalid proof,
missing connection, transport error, or unsupported ETag remains `unverified`.
Stopped, withdrawn, inaccessible and outside-scope files never use this feed.

`knowledge_ready_at` is set only for a verified current publication. The
signed feed does not establish an employee's individual WeKnora retrieval
rights, so `qa_available` remains false for every state. The browser receives
no machine credential, tenant ID or knowledge base ID. A status read does not
claim that event delivery, queue admission or a sync cursor means parsing
completed.

An administrator may configure an ordinary WeKnora web login URL, for example
`php occ config:app:set integration_weknora weknora_web_url --value=https://weknora.example/login`.
The sidebar then offers “Log in to WeKnora with your personal identity” for
an in-scope file. The link contains no file ID, source contents, or service
token. Only HTTPS URLs are accepted, except HTTP loopback URLs for local
development. This is a login handoff, not a grant of knowledge access; WeKnora
must enforce the user's own permissions after login. Leaving the setting empty
removes the link. The app does not proxy AI questions through a shared account.

Build the Files tab after changing its source with `cd apps/integration_weknora
&& npm ci && npm run build`; the generated `js/weknora-sidebar.js` is included
in the app package. In the local Compose environment, run
`python3 apps/integration_weknora/tests/employee_file_status_http_smoke.py --file-id 77`.
The test checks anonymous access, service-token isolation, file sharing and
revocation, publication withdrawal, and URL filtering, then restores its
temporary user, share, publication state, and URL setting.

## Source-side diagnostics

`GET /api/v1/admin/diagnostics` requires a Nextcloud administrator session.
It reports whether all configured binding roots remain resolvable, the number
and age of retained change hints, the latest hint, and the count of explicit
file withdrawals. Hint retention is **not** a delivery backlog: there is no
consumer acknowledgement yet. This endpoint does not report WeKnora parsing,
index health, queue age, or storage capacity. Run
`python3 apps/integration_weknora/tests/diagnostics_http_smoke.py` in the
bootstrapped local stack to check administrator and non-administrator access.
