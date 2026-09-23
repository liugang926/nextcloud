# WeKnora Integration for Nextcloud 34

This app exposes service read APIs and administrator publication operations under `/index.php/apps/integration_weknora/api/v1`. The read APIs require `Authorization: Bearer <service token>`. Administrator operations require a logged-in Nextcloud system administrator and a valid CSRF request token; the service token cannot call them.

The service token is stored only as a SHA-256 hash. Configure it and a JSON array of bindings as the Nextcloud web user, for example:

```sh
TOKEN='replace-with-a-long-random-token'
HASH=$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)
php occ config:app:set integration_weknora service_token_sha256 --value="$HASH"
php occ config:app:set integration_weknora bindings --value='[{"id":"dev-published","name":"Published","owner_uid":"devadmin","root_file_id":123}]'
php occ app:enable integration_weknora
```

`root_file_id` must identify a child folder in the `owner_uid` user's files; binding the user's entire files root is refused. The binding ID uses letters, digits, `_`, and `-`. A missing token hash rejects all requests. Invalid binding configuration returns HTTP 503.

Service read endpoints:

- `GET /capabilities`: protocol version and Nextcloud instance ID.
- `GET /bindings`: configured binding IDs, names, and root file IDs.
- `GET /bindings/{id}/manifest[?cursor=...]`: readable, non-withdrawn files recursively under the binding root. Each page has at most 200 items plus a generation, `complete` flag, and `next_cursor`. The first page stores a ten-minute immutable list; later pages use that list. A changed source or expired list returns HTTP 409; restart the traversal rather than applying a partial manifest.
- `GET /bindings/{id}/files/{fileId}/content`: streamed file bytes with `Content-Type` and `ETag`. An optional `If-Match` header rejects a changed version with HTTP 412. Withdrawn files return HTTP 404.
- `GET /bindings/{id}/changes[?cursor=...]`: ordered file-change hints with a signed cursor. The feed is a wakeup and reconciliation aid, not authority for physical deletion. A 409 response requires a complete rescan.

Administrator endpoints (browser session and CSRF protected):

- `GET /admin/bindings`: list full binding configuration.
- `POST /admin/bindings`: create a binding or update its name with JSON fields `id`, `name`, `owner_uid`, `root_file_id`. The root must be a readable child folder of the owner. Overlapping roots are rejected, and an existing binding cannot silently change its owner or root. V1 configuration accepts bindings for one owner account only, because overlapping shared mounts across owners cannot yet be proved disjoint.
- `GET /admin/bindings/{id}/files/{fileId}/publication`: current `eligible` or `withdrawn` state.
- `POST /admin/bindings/{id}/files/{fileId}/withdraw`: persist exclusion from the manifest and content API.
- `POST /admin/bindings/{id}/files/{fileId}/republish`: explicitly clear the exclusion.

Publication state and an action audit are stored in the Nextcloud database through app migrations. Withdrawal records a denial for a file ID scoped to an existing binding even if that file has already been moved or deleted; it can also reserve a file ID for exclusion before it enters the binding. Republish requires the file to be currently readable under the bound root. Withdrawal blocks this app's read APIs immediately. Removing an already indexed copy from WeKnora still depends on connector reconciliation and WeKnora's retrieval controls.

The manifest generation is a SHA-256 digest of the binding and sorted file metadata, independent of the request host. The first page still scans the entire binding, and each later page reads and decodes the stored list. Large directories need load and fault testing before production use.

After bootstrapping the local Compose stack, run `python3 apps/integration_weknora/tests/publication_http_smoke.py --file-id <sample-file-id>` to verify administrator and non-administrator access, CSRF, withdrawal, republish, manifest/content exclusion, and binding overlap. The test restores the sample file's original publication state.

## V1 source authorization and identity mapping

The paired connector calls `POST /bindings/{id}/authorize` with its existing
`Authorization: Bearer <service token>` header and a JSON body containing
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
database. The account's backend class is pinned at creation and rechecked on
every authorization, and disabled or missing accounts are denied. Authorization
resolves the binding root and target file through the mapped user's mount view
and checks each directory in the path for read access, plus publication state.
No mapping is inferred from email, display name, or Nextcloud UID similarity.

The administrator currently attests that a pair of identifiers refers to the
same person. This app does not independently read AD objectGUID from
`user_ldap`, prove that the two systems use the same directory, or validate
enterprise team-folder advanced ACL behavior. These must be verified against
the target AD and Nextcloud permission setup before production use. The
connector's service token remains a high-trust credential: the holder can
submit any mapped directory identity and access any configured binding.
The current single app-wide token has no per-binding scope or overlapping-key
rotation; use TLS and implement scoped credential rotation before enterprise
deployment. A service-side authenticated principal and all WeKnora retrieval
entry points must enforce this API's result.

After the local Compose upgrade, run
`python3 apps/integration_weknora/tests/authorization_http_smoke.py --file-id <sample-file-id>`.
The test covers machine authentication, administrator and CSRF controls,
mapping conflicts, sharing and share revocation, disabled accounts, and
publication withdrawal. It removes the temporary account and share and
restores the sample publication state.
