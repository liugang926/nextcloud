# Disposable synthetic LDAP cross-system fixture

This fixture makes the two-account permission drill in
[synthetic-ldap-acceptance.md](synthetic-ldap-acceptance.md) repeatable on a
developer's Docker host. It creates a **new** Compose project with a synthetic
OpenLDAP directory, Nextcloud, WeKnora, ParadeDB, Redis, a document reader, and
a deterministic three-dimensional embedding stub. It does not change an
existing Nextcloud/WeKnora stack or contact an enterprise directory.

The directory gives Alice and Bob distinct binary `objectGUID`/`objectSid`
values and AD-shaped account attributes. `direct`, `primary`, and `nested`
modes select the sole content-grant path for Alice. Bob stays outside
`Engineering`. Both applications use the same directory and private CA over
`ldaps://openldap:1636`; the in-container LDAP export used by the preflight
reads the same server over its loopback listener. Nextcloud, the WeKnora API,
and the debug LDAPS port are published on random host `127.0.0.1` ports. The
optional browser drill also publishes its WeKnora UI on loopback. This
isolated acceptance stack is intentionally not a LAN-facing demo.

## Run a fresh nested-group matrix

Use a locally built WeKnora image containing the candidate LDAP/directory,
Nextcloud source, and group-access implementation. The generator records its
Docker image ID and refuses to start if the tag moves after preparation. On
this host, the verified candidate image was
`weknora-ldap-app:pilot-load-8345373-f420ef` with image ID
`sha256:c4f63fd9af774d6555265a2fd7672459d3c4a7c7e9542f1beab41220b447a465`.
The other Compose images are digest-pinned in the generator. Docker Compose,
OpenSSL, and Python 3 are required. Build or load the patched WeKnora image
before running `prepare`; the generator never pulls an unreviewed WeKnora tag.

```sh
cd /path/to/nextcloud
IMAGE=weknora-ldap-app:pilot-load-8345373-f420ef
SCRATCH="$(python3 scripts/ops/synthetic-ldap-fixture.py prepare \
  --weknora-image "$IMAGE" --mode nested |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["scratch"])')"
python3 scripts/ops/synthetic-ldap-fixture.py up --scratch "$SCRATCH"
python3 scripts/ops/synthetic-ldap-e2e.py bootstrap --scratch "$SCRATCH"
python3 scripts/ops/synthetic-ldap-e2e.py matrix --scratch "$SCRATCH"
python3 scripts/ops/synthetic-ldap-fixture.py destroy --scratch "$SCRATCH"
```

`prepare` creates a unique project and scratch directory (`0700`); generated
credentials, Compose config, TLS key, source token, LDIF, topology snapshot,
and acceptance fixture are `0600`. `up` waits for every health check.
Project allocation rejects existing Docker resources with the chosen Compose
name. The fixture records a private owner token, scratch path, and Compose
fingerprint; its services, volumes, and network carry that owner label.
`up` and `destroy` reject mismatched resources, and verify the actual WeKnora
backend/UI container image IDs against the prepared pins. State-recorded
loopback ports and backend/UI image names must match Compose exactly before
either command uses them. The PDF probe's
single `DOCREADER_PDF_FORCE_SCANNED=1` override is allowed. Scratch directories
created before these ownership fields were added require manual inspection
before cleanup; the current script will not delete them automatically.
`bootstrap` configures Nextcloud `user_ldap` with `objectGUID` as the expert
user/group UUID attribute **before** user discovery, enables the repository's
`integration_weknora` app, creates a synthetic DAV file and Engineering group
share, and requires Alice's authenticated DAV access before creating a binding
or source pair. It maps the two exact identities and issues a binding key. It registers
only a synthetic WeKnora local administrator, synchronizes the same LDAPS
directory, grants both users workspace viewer via Domain Users, restricts the
dedicated KB to Engineering, pairs the source, and waits for one published
indexed document with a ready chunk and embedding. `matrix` re-exports a
fresh attribute-limited LDAP snapshot and invokes the six-field HTTP probe.
It prints no passwords, source key, or JWT. A failed bootstrap leaves this
owned fixture for inspection; discard it with `destroy` and create a fresh
one. Do not retry bootstrap against a partly configured stack.

For a direct-member baseline, select `--mode direct`; `matrix` runs
`baseline`. For the primary-group case, select `--mode primary`; the generated
topology grants Alice only through `primaryGroupID=2000`. The DAV preflight
currently stops before pairing because Nextcloud cannot read Alice's group
share in this synthetic setup. After using any mode, `destroy` removes
only the marker-verified project, its volumes/network, and that run's private
scratch directory. It never prunes global Docker resources.

## Observed result, 2026-09-24 UTC

The automated **fresh** nested fixture `nc-synldap-cc05d73e` used Nextcloud
34.0.4, the integration app from candidate Nextcloud worktree `f1fb568`, and
the WeKnora image ID above. Its `bootstrap` created one published document,
one ready chunk, and one embedding. At `06:19:05Z`, `matrix` exited 0:

| Account | Nextcloud DAV login | File DAV | Signed source | WeKnora LDAP login | Knowledge | Both KB/document search scopes |
| --- | --- | --- | --- | --- | --- | --- |
| Alice | allow | allow | allow | allow | allow | hit |
| Bob | allow | deny | deny | allow | deny | no hit |

The fresh LDAP preflight confirmed Alice→Platform→Engineering as her **only**
Engineering path and excluded Bob. The Nextcloud folder share and WeKnora KB
grant both targeted Engineering. Docker inspect showed only loopback port
bindings, and the scratch directory/files had the modes above. A separate
manual nested run on the same candidate also passed all six HTTP fields at
`06:09:28Z`.

The primary-only mutation in that manual stack passed the LDAP preflight and
WeKnora reported Alice in Engineering with `origin=primary`. Nextcloud
`user_ldap` listed Engineering's direct disabled test member, omitted Alice,
and returned DAV 404 for Alice's shared file; Bob also received 404. This is
a **failed** cross-system primary-group permission case, not an accepted
denial. The full primary KB/search matrix was not run. Nextcloud 34.0.4's
`Group_LDAP::primaryGroupID2Name` constructs a textual `objectsid=S-...-RID`
filter, whereas this fixture stores `objectSid` with binary
`octetStringMatch`; the textual lookup returned no group. This identifies a
synthetic-directory mismatch and does not establish how a real AD server
would answer. The bootstrap now stops before pairing when Alice's actual DAV
grant is absent. Test a real AD primary group before claiming that case complete.

This OpenLDAP schema models selected AD attributes. It does not prove
enterprise AD behavior, Kerberos/SSO, Team folder ACLs, account disablement,
long-lived revocation, citation generation, or production scale. The
WeKnora image used here was a locally loaded candidate image; for another
host, build the intended WeKnora commit and record its image ID alongside
the output.

## Regression check, 2026-09-30 UTC

The fresh `primary` fixture stopped during bootstrap with Alice's authenticated
DAV `PROPFIND` returning 404. No source pair was created. The fresh `direct`
fixture passed that preflight, indexed one chunk and embedding, then passed the
six-field HTTP matrix: Alice's login, DAV, source, knowledge and search were
allowed; Bob could log in to both services but all four content checks were
denied. Both owned fixtures and their Docker volumes were removed. Fixture
passwords now use 31-character values that satisfy WeKnora's registration
length/character policy while remaining safe as Nextcloud installer arguments.

## File-scoped ask and citation drill, 2026-09-30 UTC

Build the RAG patch images with `bash scripts/build-weknora-rag.sh`, then run a
fresh `direct` fixture with `weknora-ldap-app:nextcloud-rag`. After `bootstrap`
and `matrix`, run the dedicated probe before destroying the owned project:

```sh
python3 scripts/ops/synthetic-ldap-ask-handoff.py --scratch "$SCRATCH" --revoke
python3 scripts/ops/synthetic-ldap-fixture.py destroy --scratch "$SCRATCH"
```

The bootstrap sets `overwrite.cli.url` to the disposable Nextcloud browser
origin before the first sync, and sets `weknora_web_url` to the disposable
WeKnora browser origin. Its signed event/status connection lets the Files
sidebar show `ready`. The local mock chat model emits the fixture answer
marker only when that marker reaches its prompt. The probe asserts Alice's
file-scoped question returns the marker and a reference whose
`nextcloud_human_url` points to the original Files `/f/<file_id>` route on the
browser origin. Source pairing and ingestion still use `http://nextcloud`
inside the Compose network. The probe does not print credentials, tokens,
source content, or SSE data.

The run used Nextcloud integration app `0.4.27` at `0086699`, RAG patch
SHA-256 `a7d638ac36f2e64b5adf998cdd16a811c238de343a5ab8d001262fa8207c3961`,
and local backend image ID
`sha256:3d5354deb5973e79446f8be3acf1d46dd72a2224ab07b1b6c37edf4b6837f2be`.
The isolated source indexed two chunks and two embeddings. The six-field
permission matrix passed. The handoff probe observed Alice Files status 200,
Bob status 404, Alice ask-target 200, Bob ask-target 403, and stale-ETag
ask-target 404. The file-scoped answer contained the synthetic marker and a
citation to Alice's Nextcloud original. An unauthenticated visit to that route
required login, and Alice's logged-in visit returned 200. A Files route may
serve its browser shell to a logged-in user without proving file read access;
the DAV, Files status, signed source, and WeKnora checks establish the actual
per-user grants here.

After removing Alice's only Engineering group membership, the same WeKnora
JWT issued before revocation lost access: Files status 404, ask-target 403,
direct knowledge 403, chunk/preview denied, KB- and document-scoped search
empty, and prior-answer history omitted both the marker and citation URL;
the same history contained both before the mutation. An initial run observed
all denials by the seventh poll, about 31 seconds after mutation. A fresh
reproduction with the stronger before/after history assertion observed all
denials by the second poll, about 7 seconds after mutation. These are observed
synthetic intervals, not an enterprise revocation SLA. The probe is an HTTP/API simulation of the
browser handoff, not a browser click test. This OpenLDAP folder group share is
not a production AD or Team Folder ACL test, and the deterministic mock model
does not establish answer quality with a real LLM.

## Files browser handoff drill, 2026-09-30 UTC

`isolated-browser-ask-smoke.py` creates a new loopback-only `direct` fixture,
boots both applications, drives a real headless Chromium session, and removes
only its marker-owned Compose project and volumes in `finally`. It needs local
backend and frontend WeKnora candidate images, an installed Playwright Node
module, and Chromium. Image IDs are pinned when the fixture is prepared; the
browser probe checks the UI image and owned Compose ports before login.

```sh
python3 scripts/ops/isolated-browser-ask-smoke.py \
  --weknora-image YOUR_LOCAL_BACKEND_IMAGE \
  --weknora-ui-image YOUR_LOCAL_FRONTEND_IMAGE \
  --playwright-module /path/to/node_modules/playwright \
  --browser-executable /path/to/chrome-headless-shell
```

The browser logs in as synthetic Alice, opens Files → Published → the file's
Details → WeKnora, clicks **在知识库中提问此文件**, completes the WeKnora directory
login, and submits a question. It checks that the chat request is limited to
the indexed file, uses `agent_enabled: false`, and omits `agent_id` and
`agent_source_tenant_id`. It then requires the deterministic answer marker,
an original-file citation, and a logged-in open of that citation in Nextcloud.
The same browser context must also read the exact synthetic original via
Alice's authenticated WebDAV session; a Files app-shell response alone does
not satisfy this assertion.
Screenshots stay in a private `0700` evidence directory printed on success;
the script does not print credentials, tokens, or answer text.

A fresh run against the fixed UI candidate completed these checks with HTTP
200 for the chat request and a citation to `/f/92`. It reported
`fixture_cleaned: true` after verifying no owned containers, volumes, or
network remained. The standalone frontend regression test
`src/api/chat/streame.test.ts` passed all five cases, including the non-Agent
request shape. The model and LDAP directory are synthetic, so this confirms
the browser handoff path without establishing production answer quality or
enterprise AD behavior.

The same drill then exited 0 with the combined RAG/AnyDoc/event-rebind app
and UI patch SHA-256
`ed055900b1eca78cc15a14021794fb6dc95dafb3e8e5592ce3f865ca1538af03`.
In owned project `nc-synldap-14bdc529`, the chat request returned HTTP 200
without an Agent ID, the answer contained the fictional marker, and the
original `/f/92` citation opened. Alice's same browser session also read the
exact original file via WebDAV with HTTP 200. The driver reported
`fixture_cleaned: true`. This run exercised the current app and UI together;
the directory and answer were still synthetic.
After adding exact state-to-Compose port and image checks to the fixture, a
fresh run in `nc-synldap-47834a82` passed the same browser, citation, exact
WebDAV content, and cleanup assertions.

## Source-share-only revocation drill, 2026-09-30 UTC

The LDAP group-removal drill above withdraws both the Nextcloud folder grant
and WeKnora's Engineering KB grant. To exercise the independent Nextcloud
source-authorization boundary, create another fresh `direct` fixture using
the same `prepare`, `up`, `bootstrap`, and `matrix` steps, then run:

```sh
python3 scripts/ops/synthetic-ldap-ask-handoff.py --scratch "$SCRATCH" --revoke-source-share
python3 scripts/ops/synthetic-ldap-fixture.py destroy --scratch "$SCRATCH"
```

The handoff probe stores the ID returned by the fixture's OCS group-share
creation. This mode deletes only that share through the disposable Nextcloud
admin session. It leaves Alice's LDAP Engineering membership, WeKnora's
restricted Engineering KB grant, the source binding, and the owner's original
file in place. The probe verifies those remaining grants after the mutation.
It checks both Alice's authenticated DAV file and the signed per-user source
authorization endpoint, then repeats the old-JWT ask-target, direct knowledge,
chunk/preview, KB- and document-scoped search, and prior-answer history checks.

The fresh disposable fixture `nc-synldap-2f5367da` used app `0.4.28` at
`4b8ab91` and `weknora-ldap-app:nextcloud-rag` image ID
`sha256:095461a5632abd406b70f55fd18c757721475d3a2ab119cf5a206bd148d77e56`.
The baseline six-field matrix passed with Alice authorized and Bob denied.
After deleting the share at `03:47:49Z`, the first poll at `03:47:52Z` observed
Alice's DAV and signed source reads denied, Files status 404, ask-target 403,
direct knowledge 403, chunk/preview denied, both search scopes empty, and the
prior answer and citation hidden. Alice could still log in to WeKnora through
LDAP; Nextcloud still listed her in Engineering; the WeKnora administrator's
policy view still listed the Engineering `read` grant; and the owner could
still resolve the same file ID through DAV. This demonstrates a source-only
denial in the synthetic group-share fixture. The approximately three-second
interval is an observation, not a revocation SLA or proof of real Team Folder
ACL behavior.
The existing LDAP Engineering group-removal mode also passed again in a
separate fresh disposable fixture after these probe changes.

## Real Team Folder advanced ACL drill, 2026-09-30 UTC

The pinned Nextcloud 34.0.4 image does not bundle Team Folders. The official
[Nextcloud App Store](https://apps.nextcloud.com/apps/groupfolders/) lists
groupfolders 22.0.6 for Nextcloud 34. Download that release package into a
fresh disposable scratch directory before bootstrap. The script verifies its
SHA-256 against the official release asset digest
`bfff357b12bbd24257d8d127cf30e83a72256e7659f1cc3e32bd09fe647d2f9b`
and installs it only inside the owned Nextcloud container. The container's
`occ app:install groupfolders` failed on this host with GitHub TLS EOF, so the
reproducible path downloads the pinned package on the host:

```sh
cd /path/to/nextcloud
SCRATCH="$(python3 scripts/ops/synthetic-ldap-fixture.py prepare \
  --weknora-image weknora-ldap-app:nextcloud-rag --mode direct |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["scratch"])')"
gh release download v22.0.6 -R nextcloud-releases/groupfolders \
  --pattern groupfolders-v22.0.6.tar.gz --dir "$SCRATCH"
python3 scripts/ops/synthetic-ldap-fixture.py up --scratch "$SCRATCH"
python3 scripts/ops/synthetic-ldap-e2e.py bootstrap --team-folder --scratch "$SCRATCH"
python3 scripts/ops/synthetic-ldap-e2e.py matrix --scratch "$SCRATCH"
python3 scripts/ops/synthetic-ldap-ask-handoff.py --scratch "$SCRATCH" --deny-team-acl
python3 scripts/ops/synthetic-ldap-fixture.py destroy --scratch "$SCRATCH"
```

This mode creates an actual groupfolders Team Folder named `Published`, grants
Engineering to Alice and a separate local publisher group to `devadmin`,
binds the Team Folder root, and indexes `acl-note.txt` in WeKnora. The probe
records a file-scoped answer and original Files citation before applying a
real file-level advanced ACL `-read` rule to Alice. It then checks Alice's and
Bob's authenticated DAV, signed per-user source authorization, Files status,
old-JWT ask-target and WeKnora content paths. It also confirms that Alice
retains access to the Team Folder root and LDAP Engineering membership, the
WeKnora Engineering KB `read` grant remains, and the publisher can still
resolve the same original file ID.

Fresh fixture `nc-synldap-9951640c` passed the six-field baseline matrix:
Alice was allowed and Bob was denied on content paths. It indexed two chunks
and embeddings. The file-scoped mock answer included the synthetic marker and
original Files citation. After setting the ACL at `04:19:08Z`, the first poll
at `04:19:12Z` found Alice's DAV and signed source reads denied, Files status
404, ask-target 403, knowledge 403, chunk/preview denied, both search scopes
empty, and the prior answer and citation hidden. Bob stayed denied on DAV,
signed source, ask-target and WeKnora content paths. The approximately
four-second interval is one synthetic observation, not a revocation SLA.

This verifies a real groupfolders 22.0.6 ACL in an AD-shaped OpenLDAP fixture.
It does not prove enterprise AD behavior, production Team Folder configuration,
browser interaction, or the V1 requirement to reject or pause publication of
unsupported permission exceptions during configuration and synchronization.
