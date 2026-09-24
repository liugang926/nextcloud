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
share, maps the two exact identities, and issues a binding key. It registers
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
topology grants Alice only through `primaryGroupID=2000`. The expected
permission probe is intentionally strict and will report the current
Nextcloud mismatch described below. After using any mode, `destroy` removes
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
denial. The full primary KB/search matrix was not run. Investigate Nextcloud
primary-group resolution before claiming that AD case complete.

This OpenLDAP schema models selected AD attributes. It does not prove
enterprise AD behavior, Kerberos/SSO, Team folder ACLs, account disablement,
long-lived revocation, citation generation, or production scale. The
WeKnora image used here was a locally loaded candidate image; for another
host, build the intended WeKnora commit and record its image ID alongside
the output.
