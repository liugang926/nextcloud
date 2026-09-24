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

### Repeatable primary-group and nested-group preflight

The `primary_group` and `nested_group` matrix cases now require a fresh,
attribute-limited LDAP export. This prevents a green access result from being
credited to the intended group mechanism when the test user actually has a
different grant path. Capture the export **after** each synthetic membership
mutation and run the HTTP matrix within 120 seconds. The example assumes a
temporary, loopback-only LDAPS debug port on the disposable directory;
otherwise run `ldapsearch` inside that project's private network. Store the
export under the private scratch directory and remove it during cleanup.

```sh
umask 077
LDAPTLS_CACERT="$SCRATCH/certs/ca.crt" LDAPTLS_REQCERT=demand \
  ldapsearch -x -LLL -o ldif-wrap=no \
  -H ldaps://127.0.0.1:11636 \
  -D "$LDAP_BIND_DN" -y "$SCRATCH/bind-password" \
  -b 'dc=example,dc=test' \
  '(|(objectClass=adTestUser)(objectClass=adTestGroup))' \
  dn objectClass objectGUID objectSid primaryGroupID member uniqueMember \
  > "$SCRATCH/topology.ldif"

python3 scripts/ops/ad-permission-acceptance.py \
  --fixture "$SCRATCH/fixture.json" --case primary_group \
  --topology-ldif "$SCRATCH/topology.ldif" \
  --grant-group-guid "$PRIMARY_GRANT_GROUP_GUID" \
  --nextcloud-origin http://127.0.0.1:18192 \
  --weknora-origin http://127.0.0.1:18193 --allow-loopback-http

# Re-export after switching to the nested-group phase, then run:
python3 scripts/ops/ad-permission-acceptance.py \
  --fixture "$SCRATCH/fixture.json" --case nested_group \
  --topology-ldif "$SCRATCH/topology.ldif" \
  --grant-group-guid "$PARENT_GRANT_GROUP_GUID" \
  --child-group-guid "$CHILD_GROUP_GUID" \
  --nextcloud-origin http://127.0.0.1:18192 \
  --weknora-origin http://127.0.0.1:18193 --allow-loopback-http
```

The preflight decodes AD binary GUID/SID values, checks the primary-group SID
or direct child→parent edge, rejects alternate grants that would mask the
intended path, excludes B, and rejects cycles. It handles simple synthetic DNs
only. Compare WeKnora's synchronized effective groups and Nextcloud's LDAP
group view separately; one LDIF export and boolean HTTP results cannot prove
both services used the same directory snapshot or resolve enterprise AD's
complex DN/ranged-membership behavior.

The pre-existing `team_folder_acl_http_smoke.py` automates a Team folder's
advanced file ACL deny, restoration, and group-removal checks on a disposable
**local-account** fixture. For a synthetic LDAP Team folder, perform the same
ACL transitions in the isolated stack and run the `team_acl_deny` and
`baseline` cases with the LDAP-backed fixture. The local-account smoke does
not establish LDAP-backed or production Team folder behavior.

### Identity collisions and an old-JWT revocation window

With A and B already mapped to their distinct live LDAP GUIDs, run the
loopback-only identity probe using administrator credentials from the protected
environment. It checks that the exact mapping is idempotent, cross GUID↔UID
combinations, an unrelated GUID, wrong directory and an email-shaped identity
are rejected, and the registry is unchanged. No mappings are provisioned by
the probe.

```sh
export AD_ACCEPTANCE_TEST_ENV=isolated-test-accounts
# Load AD_TEST_NEXTCLOUD_ADMIN_USER and AD_TEST_NEXTCLOUD_ADMIN_PASSWORD
# through the protected process environment.
python3 scripts/ops/synthetic-identity-conflict-smoke.py \
  --fixture "$SCRATCH/fixture.json" \
  --nextcloud-origin http://127.0.0.1:18192
```

To measure denial with **the same JWTs issued before revocation**, start the
watch while the `baseline` fixture case passes. Wait for its
`baseline_ready` line, then remove A's sole grant in the private directory in
another terminal. The watcher keeps both tokens in memory and retries DAV,
signed source authorization, direct knowledge/chunk/preview, and KB-scoped and
document-scoped search until the `group_removed` fixture expectations all
hold. A 503 is a failed poll and never counts as denial. Record the LDAP
mutation time separately from the watcher's `target_observed` time.

```sh
python3 scripts/ops/synthetic-ldap-revocation-watch.py \
  --fixture "$SCRATCH/fixture.json" --target-case group_removed \
  --nextcloud-origin http://127.0.0.1:18192 \
  --weknora-origin http://127.0.0.1:18193 \
  --allow-loopback-http --timeout-seconds 120
```

The watch covers content grant revocation while both accounts remain enabled;
it does not claim account-disablement or a live chat stream. Rerun the ordinary
matrix after directory synchronization to prove fresh login behavior.

The harness contracts can be rerun without Docker:

```sh
python3 scripts/ops/test-ad-permission-acceptance.py
python3 scripts/ops/test-synthetic-ldap-topology.py
python3 scripts/ops/test-synthetic-identity-conflict-smoke.py
python3 scripts/ops/test-synthetic-ldap-revocation-watch.py
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

The fixture models GUID/SID and primary-group-shaped attributes, but OpenLDAP does not establish enterprise AD behavior for nested groups, primary group resolution, disabled-account propagation, Kerberos/SSO, or production folder ACLs. The recorded cross-system drill covered the baseline and `group_removed` cases and old JWT direct read paths. The new primary/nested preflight, identity-conflict probe, and revocation watcher have offline contracts; a fresh cross-system primary/nested/Team run has **not** yet been recorded. The patched WeKnora LDAP adapter's separate network integration suite passed its binary GUID/SID, primary/nested, disabled-account, LDAPS/StartTLS and failure-path tests in a disposable OpenLDAP project on 2026-09-24. It did not establish the full Q&A citation flow or every alternate retrieval entry point listed in [AD-acceptance.md](../scripts/ops/AD-acceptance.md). Run those independently before marking the broader AD acceptance complete.

After saving only redacted results, remove **only this project** and its scratch credentials. Verify the project's containers and volumes are gone; do not prune global Docker resources or another development stack.

```sh
docker compose -p "$PROJECT" --env-file "$SCRATCH/.env" \
  -f "$SCRATCH/compose.yaml" down --volumes --remove-orphans
docker ps -a --filter "label=com.docker.compose.project=$PROJECT"
docker volume ls --filter "label=com.docker.compose.project=$PROJECT"
rm -rf -- "$SCRATCH"
```

The recorded project `nc-ldap-e2e-hzsyv2` was stopped with `down --volumes --remove-orphans`. Verification found no containers, volumes, networks, or locally built images bearing that project name. Its private scratch directory, including generated credentials and TLS keys, was removed. No existing Nextcloud, WeKnora, or enterprise directory project was stopped or changed.
