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
- `GET /bindings/{id}/manifest[?cursor=...]`: readable, non-withdrawn files recursively under the binding root. Each page has at most 200 items plus a generation, `complete` flag, and `next_cursor`. A changed generation returns HTTP 409; restart the traversal rather than applying a partial manifest.
- `GET /bindings/{id}/files/{fileId}/content`: streamed file bytes with `Content-Type` and `ETag`. An optional `If-Match` header rejects a changed version with HTTP 412. Withdrawn files return HTTP 404.

Administrator endpoints (browser session and CSRF protected):

- `GET /admin/bindings`: list full binding configuration.
- `POST /admin/bindings`: create a binding or update its name with JSON fields `id`, `name`, `owner_uid`, `root_file_id`. The root must be a readable child folder of the owner. Overlapping roots are rejected, and an existing binding cannot silently change its owner or root. V1 configuration accepts bindings for one owner account only, because overlapping shared mounts across owners cannot yet be proved disjoint.
- `GET /admin/bindings/{id}/files/{fileId}/publication`: current `eligible` or `withdrawn` state.
- `POST /admin/bindings/{id}/files/{fileId}/withdraw`: persist exclusion from the manifest and content API.
- `POST /admin/bindings/{id}/files/{fileId}/republish`: explicitly clear the exclusion.

Publication state and an action audit are stored in the Nextcloud database through app migrations. Withdrawal records a denial for a file ID scoped to an existing binding even if that file has already been moved or deleted; it can also reserve a file ID for exclusion before it enters the binding. Republish requires the file to be currently readable under the bound root. Withdrawal blocks this app's read APIs immediately. Removing an already indexed copy from WeKnora still depends on connector reconciliation and WeKnora's retrieval controls.

The manifest generation is a SHA-256 digest of the binding and sorted file metadata, independent of the request host. Each page rescans the binding; large directories can take time to list.

After bootstrapping the local Compose stack, run `python3 apps/integration_weknora/tests/publication_http_smoke.py --file-id <sample-file-id>` to verify administrator and non-administrator access, CSRF, withdrawal, republish, manifest/content exclusion, and binding overlap. The test restores the sample file's original publication state.
