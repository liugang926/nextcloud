# Two-account AD permission acceptance

This is an **operator-run test with isolated AD accounts and synthetic files**.
The development Compose users and the existing Team folder smoke do not prove
enterprise AD semantics. Run no phase against employee accounts or production
folders. The probe neither changes directory membership nor creates files. It
uses the two test users' AD passwords to authenticate separately to Nextcloud
WebDAV and WeKnora's `/auth/ldap/login`; it uses the already paired, binding
scoped machine key only to ask Nextcloud for a current source decision. It
never prints response bodies, passwords, machine tokens or JWTs. Redirects and
untrusted TLS certificates are rejected. Run it from a protected terminal and
do not place secrets in shell history or the fixture JSON.

## Provision once

1. Use one isolated AD domain and two distinct test users **A** and **B**.
   Record each immutable `objectGUID` in canonical UUID form. Record the AD
   domain/LDAP server IDs, TLS CA and base DN configured independently in
   Nextcloud `user_ldap` and WeKnora Directory. Verify the two configurations
   point to the same directory. The script cannot infer this from a matching
   username or password. Disable `WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY`.
   For read-only WeKnora evidence, a system administrator can inspect
   `GET /api/v1/system/admin/directory/config`, `/directory/status`, and
   `/directory/groups/{object_guid}/members` under that prefix. Compare the
   redacted configuration and membership paths with Nextcloud's LDAP admin
   configuration and the test AD's primary-group SID and child→parent edges.
   Keep administrator credentials out of the fixture and script environment.
2. Create a dedicated Nextcloud publication root and WeKnora KB for synthetic
   documents only. Pair the source. Put one text file with a unique question
   and answer marker under that root; wait for the signed applied watermark and
   ready chunk/embedding. Record its Nextcloud `file_id` and WeKnora
   `knowledge_id`. Keep the expected answer marker **out of the query**.
3. Create and verify the exact `objectGUID` ↔ Nextcloud UID mappings through
   the administrator UI. Grant the relevant WeKnora KB group access. Before
   each phase, inspect both systems' directory sync state and group membership
   snapshots. Record AD change time, source-visible time, WeKnora snapshot time
   and first denied read; these are distinct clocks and establish the observed
   revocation window.
4. Store the fixture outside the repository with restrictive permissions. The
   JSON contains identifiers and expectations, **no credentials**:

```json
{
  "schema_version": 1,
  "synthetic_fixture": true,
  "directory_id": "corp-ad-test",
  "binding_id": "pilot-test",
  "file_id": 12345,
  "knowledge_base_id": "kb-test",
  "knowledge_id": "knowledge-test",
  "synthetic_query": "What is the pilot's unique test answer?",
  "accounts": {
    "a": {
      "nextcloud_uid": "ad-test-a",
      "weknora_identifier": "ad-test-a",
      "object_guid": "11111111-1111-1111-1111-111111111111",
      "dav_path": "Pilot Share/synthetic-note.txt"
    },
    "b": {
      "nextcloud_uid": "ad-test-b",
      "weknora_identifier": "ad-test-b",
      "object_guid": "22222222-2222-2222-2222-222222222222",
      "dav_path": "Pilot Share/synthetic-note.txt"
    }
  },
  "cases": {
    "baseline": {
      "a": {"nextcloud_login": true, "ldap_login": true, "dav": true, "source": true, "knowledge": true, "search": true},
      "b": {"nextcloud_login": true, "ldap_login": true, "dav": false, "source": false, "knowledge": false, "search": false}
    },
    "team_acl_deny": {
      "a": {"nextcloud_login": true, "ldap_login": true, "dav": false, "source": false, "knowledge": false, "search": false},
      "b": {"nextcloud_login": true, "ldap_login": true, "dav": false, "source": false, "knowledge": false, "search": false}
    }
  }
}
```

Use the observed access policy for each phase; `knowledge` and `search` may
both be false when WeKnora KB access is denied even though `source` is true.
All six expectations are required for both accounts. `search=true` requires
a hit from the fixture `knowledge_id` in separate KB and document searches;
a 200 response with zero hits is not a successful authorized retrieval. The
exact `file_id` check prevents a stale share path from silently testing another
file.

Export secrets through a protected environment, not command arguments or the
JSON fixture. A real run requires **all** variables below. The magic value is
an explicit assertion that both accounts are isolated test identities. Obtain
the binding key ID/token from the already paired test source; do not issue a
new key solely for this read probe.

```sh
export AD_ACCEPTANCE_TEST_ENV=isolated-test-accounts
for name in AD_TEST_A_PASSWORD AD_TEST_B_PASSWORD AD_TEST_BINDING_KEY_ID AD_TEST_BINDING_TOKEN; do
  printf '%s: ' "$name" >&2
  IFS= read -r -s value
  printf '\n' >&2
  export "$name=$value"
  unset value
done
python3 scripts/ops/ad-permission-acceptance.py \
  --fixture /protected/ad-pilot-fixture.json --case baseline \
  --nextcloud-origin https://nextcloud-test.example.invalid:443 \
  --weknora-origin https://weknora-test.example.invalid:443 \
  --allow-remote-test-environment
```

For isolated loopback Compose, use `http://127.0.0.1:PORT` for both origins and
`--allow-loopback-http`. Remote HTTP, including the LAN development URL, is
always refused because the probe sends AD passwords and a machine token. Use
a local tunnel or trusted HTTPS for a remote test environment. Authentication
and the source authorization call use POST, but the probe makes no configuration,
file, KB, membership or chat-history changes. The signed source call records a
replay nonce and log entry; LDAP login may update login metadata.

## Run the permission matrix

For each row, change only the **test** memberships/ACL in the directory or
test Nextcloud folder, wait for each service's normal propagation, update the
fixture's expectations, and run `--case NAME`. Preserve the probe's timestamped
boolean JSON plus the separate AD change/sync timestamps. Restore test state
after the run. Do not turn an HTTP 503 into an expected denial: it means the
authorization chain could not be verified.

| Case | Required setup | Expected proof |
| --- | --- | --- |
| `baseline` | A in pilot group; B in other department | A exact WebDAV file, source, knowledge, retrieval allowed; B denied on all content paths |
| `primary_group` | Grant via A's AD primary group only; remove other grant paths | A allowed; B denied. Verify primary group SID/objectGUID from the AD read-only view and both applications' effective group snapshots |
| `nested_group` | A in child group, parent group grants both folder and KB | A allowed; B denied. Record child→parent edges and sync versions; test a cycle/missing-edge condition separately as an expected fail-closed case |
| `team_acl_deny` | Advanced Team folder file ACL denies A while folder/KB remain readable | A WebDAV/source/knowledge/retrieval denied; restore ACL and rerun `baseline` |
| `share_revoked` | Revoke A's sole direct or group folder share | A denied on all content paths; B remains denied |
| `group_removed` | Remove A from the sole authorized group | A denied after source and WeKnora propagation; B remains denied |
| `user_disabled` | Disable A in AD without deleting its mapping | A's LDAP login, Nextcloud DAV root/file, source, knowledge and retrieval all denied after the measured propagation window; old WeKnora sessions/tokens need a separate recheck |

For the `user_disabled` case the script cannot obtain a new WeKnora JWT, so
`knowledge=false` and `search=false` mean **no new JWT can be used**. Retain a
pre-disable token only in a protected, separate test tool and verify that its
knowledge, search, history, preview/download and chat routes also deny; that
is not established by this script. The same applies to live streams already
running during a revocation.

## Authorized Q&A and remaining entry points

The default probe does not call `/knowledge-chat`: chat creates messages and
may send text to a configured model. On the dedicated synthetic fixture,
perform the following as a separate operator-run step:

1. Log in to WeKnora as A through LDAP, create a disposable session, ask the
   fixture's question with **only** this dedicated KB selected, and verify the
   answer contains the synthetic answer marker and a citation to the exact
   `knowledge_id`. Delete the disposable session and confirm its history is
   gone. Record whether A can open the cited Nextcloud original.
2. Repeat as B. The marker, source chunk and citation must not appear in SSE
   events, persisted message/history, retrieval results or downloaded content.
   Repeat after each revocation while holding a token issued **before** the
   change. Check direct knowledge, chunk, preview/download, Agent/tool,
   share/embed and API-key entry points. An HTTP 200 with a generic answer is
   acceptable only when it carries none of the synthetic source data.
3. Manually verify browser login/logout and Nextcloud→WeKnora jump with A and
   B. These are not exercised by the read-only probe.

The probe cannot prove that Nextcloud and WeKnora point to the same AD, that a
group is primary or nested, that a model generated a correct answer, or that
all alternate entry points are closed. Those require the recorded directory
evidence and the explicit Q&A/entry-point run above. Do not mark P4/V1
accepted from the boolean JSON alone.
