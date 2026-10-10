# Source lineage policy integration

The two complete pinned patches include the independently approved Phase 2
resolver/authorizer and its review fixes. The policy remains unused: no DI,
producer, message reader, SSE/history replay or derivative boundary enables it.
This is source integration and focused validation, with no runtime switch,
shared database migration or complete V1 acceptance.

| Complete patch | Exact base | SHA-256 |
| --- | --- | --- |
| `integration/weknora.patch` | `c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` | `dbf8c4926f9b8fa1e9e5adefd38affc0f0e00281f2c44a570c3d345bbdd13d09` |
| `integration/weknora-rag-77c97fd7.patch` | `77c97fd72f26e84435503d24eeed88cb5dfe1f01` | `8bbffc98f1fbdc03f08be0e7f6d959da61536b17b1b75b8b3a4258b914e612ae` |

Apply one complete patch to its matching base. Both build scripts and pinned
CI jobs check the current SHA. The frontend dependency files and persistent
security regression, PostgreSQL migration fixture isolation correction,
Phase 1 storage/types and existing integration changes remain included. Their
historical evidence retains the hashes that were actually tested.

## Trusted tuple and caller checks

The repository resolves persisted source identity, active pair and source
configuration, current published intent, latest consistent observation and
open KB/exact build fences in one coherent snapshot. Model/tool strings,
request metadata and citations cannot supply trusted identity or ETag.
The saved revision is the immutable knowledge candidate's exact admitted
build-fence epoch. A same-ETag rename or repeat publication does not change
that dependency; a changed candidate, original ETag or epoch does.
PostgreSQL uses a read-only REPEATABLE READ transaction; SQLite uses its read
transaction. Batch rechecking compares every saved dependency in one snapshot.

Source content requires the captured interactive caller, fresh KB/organization/
Agent and directory resource grants, current directory identity, and live
Nextcloud authorization with the original ETag. The captured caller remains
independent of a shared source tenant. After all remote calls, the guard
reloads grants, then locally rechecks the same principal's directory identity
and the whole source set. A tuple mismatch denies access; a snapshot storage
failure remains an availability error. Unknown, missing mandatory dependencies,
machine credentials and unsupported callers fail closed for source content.

Explicit complete-empty ordinary lineage retains current server-resolved KB
and API-key rules. Organization grants are evaluated before Agent KB fallback,
including legitimate API callers with no Web UserID. A declared shared Agent
still requires fresh sharing and Agent/use directory resource grants when
there are no source dependencies and no KB targets. Saved dependencies and
Agent selectors never become grants.

## Independent review and integration validation

The frozen policy commits are fixed c6
`27fc5d45f35162e51737f85a017e7f207922c255` and RAG
`5d898cb609ec14ac25a4a52c1a1eca51d84ed263`. Both independent review logs contain
104 passed tests including subtests, zero failed and zero skipped. Their log
digests and the delta inputs are recorded in the
[sanitized integration evidence](evidence/weknora-lineage-policy-integration-2026-10-08.json).

The nine leaf review cases cover:

1. An earlier source changes while a later source is remotely authorized.
2. Directory identity becomes disabled during remote authorization.
3. A revoked shared Agent is selected by complete-empty lineage with no KB.
4. A generation changes during the final grant lookup.
5. Directory identity changes during the final grant lookup.
6. An ordinary API caller without a Web UserID retains its organization KB grant.
7. A same-tenant caller loses the selected Agent/use group grant.
8. A shared-tenant caller loses the selected Agent/use group grant.
9. An organization KB grant remains independent of the Agent's KB selection.

The policy production files, tests and source documentation on both complete
trees are byte-identical to the corresponding frozen policy. The frontend
security files and migration fixture retain their exact preceding bytes.
Each regenerated patch applied to its clean pinned base, produced the exact
full integration Git tree, and reversed to the exact base tree.

On each integrated profile, the following focused gate passed **118 tests
including subtests, zero failed and zero skipped**, with real disposable
PostgreSQL16 and SQLite. The complete integration's existing publication
repository cases account for 14 additional passes over the independent policy
review; all 104 independently reviewed passes are retained.

```sh
go test -p 1 ./internal/application/access ./internal/application/repository ./internal/types \
  -run '^Test(NextcloudSourceLineage|PostgresNextcloudSourceLineage|SourceLineage|NextcloudPublication)' -count=1
```

`WEKNORA_LEASE_TEST_POSTGRES_DSN` selected an owned temporary PostgreSQL with
tmpfs storage, an internal network and no host ports or shared database/volume.
The final inventory had zero remaining test schemas. Container ids, nonce
labels, mounts and network peers were checked before removing only the owned
containers and network; shared module/build caches were retained.
Tests used one CPU, `GOMAXPROCS=2`, `-p 1` and offline module resolution.
Container, service, session and Agent-tool packages also compiled on both
profiles. CI now explicitly runs this policy/real-PG batch gate on both bases.

The unchanged full frontend suites/builds and PostgreSQL migration suites were
not repeated. Their preceding validations are linked from the
[foundation](nextcloud-message-source-lineage-foundation-2026-10-08.md) and
[frontend security](weknora-frontend-dependency-security-2026-10-08.md) records.
The complete remote CI jobs still need a run for the new complete hashes.
Directory and Nextcloud tests use service stubs; this is not real AD acceptance.

## Open boundaries and runtime

The next phase must capture and union every actual prompt/tool/result input,
wire DI and producers, atomically persist answer/event lineage, authorize each
output/replay boundary and preserve replayable prefixes. Agent tools,
compaction, memories, attachments, history passages, suggestions, artifacts,
forks and history indexes still require propagation and enforcement. Legacy
NULL/unknown policy and physical derived-copy GC coverage remain open. The
existing build fences do not prove that every external writer is covered.

This integration did not build or replace full Docker images, change runtime
tags, migrate shared schema131, or modify the frozen policy trees. The deployed
frontend is the separate security image
`sha256:3e5feb7e70f4fda72f270435a85a60656615689df14a02572e57e6996586d1ca`;
the backend remains
`sha256:be9fa7b1514edbbe97e99253aaeb4f38f138c964a4317e414732711c63f5b46d`
with the 7f patch/schema131. The
[UI runtime record](evidence/weknora-frontend-security-runtime-2026-10-08.json)
retains the preceding frontend build patch SHA. The existing 100GB original
volume and eight-volume closed-restore evidence are separate and unchanged.

The current complete patch hashes include the subsequent raw-search ranking/denial correction. Earlier policy integration evidence retains the exact hashes tested there; the policy itself is unchanged. See [raw-search correction](nextcloud-raw-search-ranked-metadata-2026-10-08.md).

The current complete patch also includes the independently reproduced
[stream poll failure correction](nextcloud-stream-poll-failure-authorization-2026-10-08.md).
Earlier measured evidence preserves its original source hashes.
