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
reads the same server over its loopback listener. Only Nextcloud, WeKnora, and
the debug LDAPS port are published, each on host `127.0.0.1` and on a random
port. This isolated acceptance stack is intentionally not a LAN-facing demo.

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
