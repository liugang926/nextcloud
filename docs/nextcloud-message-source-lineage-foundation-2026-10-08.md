# Message source lineage foundation integration

The pinned source patches now combine the approved interim Agent history,
saved-message/SSE and owner-control guards with Phase 1 lineage storage/types.
This is source integration, not a shared-runtime deployment or complete V1
permission acceptance. The prepared7f candidate and schema131 deployment
remain separate.

| Complete patch | Pinned WeKnora base | SHA-256 |
| --- | --- | --- |
| `integration/weknora.patch` | `c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` | `082c03295820a97a6e969973726bd7e07bb03b55c67a63563c5004360a9469ab` |
| `integration/weknora-rag-77c97fd7.patch` | `77c97fd72f26e84435503d24eeed88cb5dfe1f01` | `b6afcd4e150d1b7fdb006993f5936f72ddacf88e00bcaa6ff0e013e610b466dc` |

## Implemented boundaries

PostgreSQL132 adds nullable `messages.source_lineage` JSONB; SQLite51 adds
nullable TEXT. Existing answers retain SQL NULL. Version1 types preserve exact
source identities, original ETags and source-ledger revisions, validate and
union deterministic independent sets, propagate unknown, and deny oversized
envelopes (1024 sources / 256KiB). Strict decoding rejects missing/malformed
identity, unsupported versions, duplicate keys, case/Unicode field aliases and
invalid raw UTF-8. Failure resets to unknown. A complete empty set is an
explicit trusted producer assertion, not inferred from missing metadata.

Checkpoints have their own persistence lineage field. Message lineage and the
host checkpoint remain excluded from public Message JSON. No influencing
prompt/tool/result producer or per-message lineage authorization is enabled by
this foundation; old NULLs and transitive history still need the full policy.

`nextcloud_source_tombstones` holds independent positive ever-source facts for
tenant, KB, data source and pair scopes. Atomic backfill includes retained,
soft-deleted and ledger evidence. Facts have no business-row FK and survive
business deletion; update/delete, PostgreSQL TRUNCATE and SQLite conflicting
REPLACE cannot erase/rewrite them. Automatic downgrade deliberately fails to
preserve provenance. The existing global/tenant/KB source predicates now also
count these facts; a missing table fails closed.

**Empty retained evidence does not prove source-free legacy history.** If all
past evidence was erased before this backfill, the migration cannot recover
it. The complete per-message legacy NULL/unknown read, replay and derivative
policy remains open. Ordinary RAG later-turn paraphrases, original version
binding of saved/cached output, compaction lineage propagation, memory,
artifacts and history-index propagation remain incomplete. This integration
does not activate physical derived GC or its coverage marker.

## Verification

Both exact pinned bases passed clean-archive apply/reverse checks,
`git diff --check`, `gofmt` for all 270 changed Go files, and the Nextcloud
OpenAPI contract. On both integrated source trees, affected typed, repository,
access, service, session and database tests passed with one CPU and
`GOMAXPROCS=2`, including:

- Typed codec/union/limits, malformed/ambiguous inputs, nullable Message GORM
  persistence and the public JSON boundary.
- Saved Agent get/list/search, owner-only stop/steer, live and reconnect SSE,
  Agent history admission/workspace isolation and ordinary history checkpoints.
- Global/tenant/KB source predicates after hard deletion of DS/KB/pair business
  rows; missing tombstone table and cross-scope positives.
- All SQLite migration checks, including actual50-to51 with legacy rows.
- PostgreSQL full0-to132/concurrent-index gate and actual131-to132 with legacy
  rows, independent facts and guarded rollback.

Local PostgreSQL tests used a disposable vanilla16 in the supported
`app.skip_embedding=true` metadata mode; they do not prove vector-index
behavior. CI explicitly runs source-lineage types, tombstone lookup, all
SQLite migration gates and the new PostgreSQL upgrade test on both bases. CI's
existing ParadeDB/full-vector and frontend gates still need their own run for
this integrated revision. No full image build or shared database was used.

The next implementation work follows the
[lineage design](nextcloud-message-source-lineage-design.md). The temporary
[Agent guards](nextcloud-agent-history-admission-2026-10-08.md) and their limits
remain relevant until all actual inputs, persisted derivatives and output
boundaries carry and enforce complete lineage.
