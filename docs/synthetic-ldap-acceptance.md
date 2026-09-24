# Isolated synthetic LDAP permission acceptance

This runbook records a local, disposable Nextcloud–WeKnora permission drill. It uses an OpenLDAP directory with AD-shaped attributes and **synthetic** identities and files. It complements [AD-acceptance.md](../scripts/ops/AD-acceptance.md); it does not replace acceptance against an enterprise AD or prove every AD behavior.

## Fixture and isolation

Use a unique Docker Compose project, private scratch directory, and dedicated volumes. Publish only the Nextcloud and WeKnora HTTP ports on `127.0.0.1`; the directory, databases, Redis, document reader, and embedding stub stay on the private Compose network. The recorded drill used Nextcloud `34.0.4`, the repository's `integration_weknora` app, WeKnora built from the candidate branch, and the synthetic LDAP schema/LDIF under `WeKnora-ldap-ad/tests/integration/ldap/`. Each run should generate new passwords, machine tokens, and a CA/server certificate. Keep the generated `.env`, LDIF, certificate keys, and API credentials outside Git with mode `0600` (directories `0700`). Do not paste any of them into test logs or issue reports.

The directory contains two distinct enabled users, A/Alice and B/Bob, with binary `objectGUID` and `objectSid`, plus an enabled service bind account and an unused disabled user. Configure Nextcloud `user_ldap` and WeKnora Directory against the **same** LDAPS endpoint and CA. Set the Nextcloud user and group UUID attributes to `objectGUID`, register the exact GUID↔Nextcloud UID mappings, and keep `WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=0`. Verify both users can authenticate before testing content access.

The isolated source contains a dedicated synthetic file under a Nextcloud publication root shared only with `Engineering`. Initially only A belongs to `Engineering`; B does not. Grant both users a WeKnora workspace viewer role through `Domain Users`, which keeps login possible after content access is revoked. Restrict the dedicated knowledge base to `Engineering` read access. Pair the exact Nextcloud binding with that knowledge base and wait for one published source version, a ready chunk, and an embedding. The unique answer marker belongs in the file content but **not** in the search query.

The private fixture JSON follows the `schema_version: 1` example in [AD-acceptance.md](../scripts/ops/AD-acceptance.md). It records only IDs, GUIDs, file paths, query, and six expected booleans per user and case. Keep passwords and the binding machine key exclusively in the protected process environment. The test used separate loopback origins; the acceptance probe refuses HTTP to a LAN address because it transmits test account passwords and a machine token.

## Run

The following commands show the reproducible workflow. `SCRATCH` is a new private directory containing the generated Compose file, `.env`, synthetic LDIF/certificates, and the fixture; `PROJECT` is a unique Compose project name. Do not reuse an existing development or enterprise directory. Provisioning can be automated by a local script or performed through the two applications' admin APIs, but record the exact image digests and candidate commits in the run log.

```sh
SCRATCH="$(mktemp -d /tmp/nc-ldap-e2e.XXXXXX)"
chmod 700 "$SCRATCH"
PROJECT="nc-ldap-e2e-$(basename "$SCRATCH" | tr '[:upper:]' '[:lower:]')"
# Generate all fixture credentials and TLS material into $SCRATCH, mode 0600.
# Copy the test LDAP schema/LDIF and the candidate integration_weknora app into $SCRATCH.
# Create a Compose file with distinct private volumes and loopback-only HTTP ports.
docker compose -p "$PROJECT" --env-file "$SCRATCH/.env" \
  -f "$SCRATCH/compose.yaml" up -d
```

After healthy startup, install/configure Nextcloud `user_ldap`, add the dedicated binding and exact GUID mappings, create/share the synthetic file, configure WeKnora Directory and group grants, and pair/synchronize the source. The fixture `file_id` must match the WebDAV `oc:fileid`; the WeKnora `knowledge_id` must identify the indexed synthetic document. Use [ad-permission-acceptance.py](../scripts/ops/ad-permission-acceptance.py) to run the matrix:

```sh
export AD_ACCEPTANCE_TEST_ENV=isolated-test-accounts
# Load AD_TEST_A_PASSWORD, AD_TEST_B_PASSWORD, AD_TEST_BINDING_KEY_ID,
# and AD_TEST_BINDING_TOKEN through a protected process environment.
python3 scripts/ops/ad-permission-acceptance.py \
  --fixture "$SCRATCH/fixture.json" --case baseline \
  --nextcloud-origin http://127.0.0.1:18192 \
  --weknora-origin http://127.0.0.1:18193 \
  --allow-loopback-http
```

For revocation, first obtain A and B WeKnora JWTs while their initial permissions are in force; retain them only in memory in the test process. Replace the `Engineering` group's sole `member` value from A to the unused disabled fixture user with `ldapmodify` over the private Docker network. This keeps the `groupOfNames` entry valid while removing A's only content grant. Record the LDAP mutation time, probe the exact WebDAV file and signed source authorization, and retry direct WeKnora knowledge/chunk/preview and search **using the old JWTs** until the expected denial converges. An HTTP 503 during directory refresh is transient or a failed verification, never an accepted denial. Wait for the directory's successful sync timestamp, then run a fresh-login matrix:

```sh
python3 scripts/ops/ad-permission-acceptance.py \
  --fixture "$SCRATCH/fixture.json" --case group_removed \
  --nextcloud-origin http://127.0.0.1:18192 \
  --weknora-origin http://127.0.0.1:18193 \
  --allow-loopback-http
```

## Sanitized observations (2026-09-24 UTC)

Both users authenticated to Nextcloud's DAV root and WeKnora LDAP before and after the group change. The exact-source authorization decision agreed with the corresponding WebDAV file result.

| Phase | User | Nextcloud login | Exact DAV file | Signed source | WeKnora login | Knowledge | Search hit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline | A | allowed | allowed | allowed | allowed | allowed | present |
| Baseline | B | allowed | denied | denied | allowed | denied | absent |
| `group_removed` | A | allowed | denied | denied | allowed | denied | absent |
| `group_removed` | B | allowed | denied | denied | allowed | denied | absent |

With JWTs issued **before** revocation, A's direct knowledge, chunk, preview, and knowledge-base list changed from HTTP 200 to HTTP 403. Its search endpoint remained HTTP 200 but returned zero matching hits. B's old JWT remained denied. The LDAP group change occurred at `04:50:12.860Z`; the first two-second revocation poll already observed denial on Nextcloud and WeKnora; the WeKnora directory snapshot completed at `04:50:21.724Z`; the fresh-login `group_removed` matrix passed at `04:50:32.078Z`. These are observed timestamps from this synthetic environment, not an enterprise revocation SLA.

An earlier candidate repeatedly failed content synchronization because the Go HTTP client requested gzip and Apache returned a content ETag with a `-gzip` suffix while the manifest carried the plain ETag. The connector now disables automatic HTTP compression for these requests. After that fix, the synthetic source published one indexed document with one chunk and embedding. This detail matters if a future reproduction sees a successful manifest fetch followed by a `source_changed` content failure.

## Scoped event and pair health on candidate `77c2f9f`

The isolated WeKnora image was rebuilt from candidate `77c2f9f`. A second synthetic file, `untouched.md` (file ID 236), was added to the same published binding and synchronized before opening the event connection. Pair health then showed two published versions and two completed, enabled parses. The pairing's admin-only health endpoint returned HTTP 200 to the workspace owner, 403 to B/Bob, and 401 without a token:

```text
GET /api/v1/datasource/nextcloud-source-pairings/{operation_id}/health
```

The WeKnora admin created the signed event connection, and the one-time credential was sent immediately to the Nextcloud binding's event-connection admin API. The credential itself was never recorded. After updating only file ID 232 through WebDAV, the Nextcloud outbox persisted event ID 4. The isolated workers ran `php occ integration_weknora:deliver-events` and `php occ integration_weknora:poll-event-status` as `www-data`; the WeKnora event dispatcher queued the source sync. The final receiver and sender checkpoints were both `received=4`, `dispatched=4`, `applied=4` where applicable, with zero unapplied/undispatched events and no last error. Pair health still showed `published_count=2` and `parse_completed_enabled_count=2`.

Apache access logs for the event sync's `04:55:36–04:55:59 UTC` window showed **one** machine content GET for changed file ID 232 and **zero** for untouched file ID 236. The preceding baseline sync fetched file ID 236 once. The source versions remained published with distinct current ETags for IDs 232 and 236. This demonstrates selective content download in this two-file synthetic run; it does not establish large-scale performance or every missed-event fallback.

Use the following commands inside a private test project after configuring an event connection to drive delivery and the signed applied-status poll. The status poll is intentionally rate-limited, so an immediate `applied=0` after the receiver finishes is not a failure; wait for the next eligible poll and verify both sides advance.

```sh
docker compose -p "$PROJECT" --env-file "$SCRATCH/.env" \
  -f "$SCRATCH/compose.yaml" exec -T -u www-data nextcloud \
  php occ integration_weknora:deliver-events
docker compose -p "$PROJECT" --env-file "$SCRATCH/.env" \
  -f "$SCRATCH/compose.yaml" exec -T -u www-data nextcloud \
  php occ integration_weknora:poll-event-status
```

## Limits and cleanup

The fixture models GUID/SID and primary-group-shaped attributes, but OpenLDAP does not establish enterprise AD behavior for nested groups, primary group resolution, disabled-account propagation, Kerberos/SSO, or production folder ACLs. This drill covered the baseline and `group_removed` cases and old JWT direct read paths. It did not establish the full Q&A citation flow or every alternate retrieval entry point listed in [AD-acceptance.md](../scripts/ops/AD-acceptance.md). Run those independently before marking the broader AD acceptance complete.

After saving only redacted results, remove **only this project** and its scratch credentials. Verify the project's containers and volumes are gone; do not prune global Docker resources or another development stack.

```sh
docker compose -p "$PROJECT" --env-file "$SCRATCH/.env" \
  -f "$SCRATCH/compose.yaml" down --volumes --remove-orphans
docker ps -a --filter "label=com.docker.compose.project=$PROJECT"
docker volume ls --filter "label=com.docker.compose.project=$PROJECT"
rm -rf -- "$SCRATCH"
```

The recorded project `nc-ldap-e2e-hzsyv2` was stopped with `down --volumes --remove-orphans`. Verification found no containers, volumes, networks, or locally built images bearing that project name. Its private scratch directory, including generated credentials and TLS keys, was removed. No existing Nextcloud, WeKnora, or enterprise directory project was stopped or changed.
