# Message source lineage design

Status: proposed; no migration or implementation is included in this document.
This is an implementation plan for PRD §§7.3, 7.7 cases 6–8 and 8. It does not
close those requirements, establish derived GC coverage, or authorize rollout.

## Source inspected

Nextcloud repository base: `fc6cec3`. The source audit used the source-only
WeKnora checkout at `c5ee8c4e` with the full RAG integration patch whose SHA-256
is `7f9c7f6a0027e42a4dd01b33366051ee7533aeddb9369653b83653165d5b5517`.
That checkout includes newer upstream compaction and fork paths; their presence
must be checked separately when implementing the pinned `77c97fd7` and
`c6c4bd44` adaptations. This document is not runtime verification.

| Current path | Finding that determines the design |
| --- | --- |
| `internal/application/access/nextcloud_history.go:46–150` | Checks visible references and request scopes. These are not an exhaustive record of the inputs that influenced a saved answer. |
| `internal/application/service/session_agent_qa.go:150–164`; `agent_history.go:72–140,299–324,357–410,469+` | Loads raw repository history, expands saved AgentSteps and injects a saved summary. All three can carry source text into another turn. |
| `internal/application/service/chat_pipeline/common.go:125–175`; `search.go:164–178`; `into_chat_message.go:37–43,210–303` | RAG replays prior assistant prose, can merge historical references, and checks the merged retrieval list. A later answer can paraphrase prior prose without carrying its original references or scope. |
| `internal/types/context_checkpoint.go`; `internal/agent/compaction/{prepare,compactor}.go`; `internal/agent/checkpoint.go:21–34` | Summaries, split-turn summaries and degraded raw archives have no source lineage. The stored checkpoint can replace many old turns. |
| `internal/handler/session/agent_stream_handler.go:417+,715+,916+`; `qa.go:1966–1984`; `internal/application/repository/message.go:263–275` | References and steps are accumulated in memory; final answer save already has a transaction boundary and completion notification follows that save. The history index is started afterwards with only answer text. |
| `internal/handler/session/stream.go:224–251,580–632` | Replay/live guards reconstruct a partial set from reference and tool events. Some optional guard interfaces currently pass when absent. |
| `internal/application/service/session_fork.go:499–518`; `internal/application/repository/session.go:408+` | Fork clones messages/checkpoints and changes IDs. Copying must preserve provenance rather than recertify the text. |
| `internal/application/repository/datasource_repo.go:155–185` | Historical classification includes soft-deleted data sources, but tenant classification counts retained source rows. Hard deletion can remove that evidence. A KB's sticky marker alone does not survive deletion of the KB. |

A proposed Agent history admission fallback can deny ambiguous old Agent
history. **Ordinary RAG later-turn paraphrase leakage remains even with that
fallback:** turn A reads a source; ordinary turn B is generated from A's prose
with no new search or Nextcloud request scope; after revocation B can appear
source-independent. The same problem crosses Agent/RAG mode switches and
survives compaction. Citations, selected KBs, and output text scanning cannot
recover this dependency.

## Persisted contract

Add an internal, per-assistant `messages.source_lineage`: PostgreSQL `JSONB`,
SQLite `TEXT`, nullable for existing rows. The proposed next migration slots
are PostgreSQL **132** and SQLite **51**, following the currently inspected
131/50. These numbers are reservations for implementation review only; no SQL
files are added here and they must be rechecked before a migration is written.

Version 1 has the following logical shape (illustrative, not SQL):

```json
{
  "version": 1,
  "state": "complete",
  "sources": [
    {
      "provider": "nextcloud",
      "tenant_id": 42,
      "knowledge_base_id": "kb-uuid",
      "datasource_id": "source-uuid",
      "pair_operation_id": "immutable-pair-uuid",
      "instance_id": "instance-uuid",
      "binding_id": "binding-uuid",
      "file_id": "123",
      "external_id": "nextcloud:instance-uuid:123",
      "knowledge_id": "revision-knowledge-uuid",
      "etag": "opaque-original-etag"
    }
  ]
}
```

`complete` means every input/output adapter for this turn accounted for its
source dependencies. `unknown` means coverage or identity is incomplete;
known sources may still be retained for diagnostics, but cannot make the row
readable. SQL NULL, malformed JSON, unsupported versions, missing identity or
ETag, and missing guard dependencies all mean `unknown`. Existing NULL rows
must not be backfilled to `complete` merely because a current source list is
empty. A fresh, fully accounted source-independent turn has explicit
`complete` plus `sources: []`.

Resolve identities from trusted persisted source/revision rows, not model
arguments, citation strings, event text, file names or paths. Preserve the
original opaque ETag; it is not a content hash. Use the effective source
tenant for shared Agents, separately from the session owner. Record the
immutable publication/revision identity available in the source registry;
if a required generation identity cannot be resolved, retain `unknown` rather
than invent a generation. A later restore/republication gets a new identity.
Different ETags/revisions of one file remain separate dependencies.

The stored set covers all source material actually supplied to any model that
can influence the turn, and all source material emitted by the turn: excerpts,
titles, metadata, image descriptions, graph results, tool results, retrieved
history passages and direct answers. It includes planner/rewrite/summarizer
calls and models producing follow-up suggestions or other saved derivatives.
Account for ordinary and external input adapters too: they must supply a
trusted source-independent classification or resolve controlled dependencies.
An opaque adapter that may return copied source content makes the result
`unknown`. User-authored text alone does not create a Nextcloud dependency;
system-resolved quotes, attachments, memories and artifacts do require their
own dependency contract. Never infer independence by inspecting prose.

## Accumulation and inheritance

Carry the lineage envelope through `History`, `chat.Message`, RAG result
assembly, tool-result envelopes and a turn-local accumulator. This metadata
stays internal and is not a new model instruction or public source inventory.

1. At the final model-input boundary, union dependencies of the actual messages
   being sent, including inherited assistant answers, saved tool content,
   checkpoints, resolved quotes and source-backed images. RAG must capture the
   final merge after history, rerank/dedup, enrichment and prompt rendering.
   Selection scopes and visible references remain UI/audit fields.
2. Agent tools attach trusted lineage when reading source data, before either
   returning it to the engine or appending an output event. Search/list/read,
   graph, MCP and sandbox/artifact adapters need coverage. Empty structured
   search/list results contribute no new source; they do not discard sources
   already inherited by the turn. Diagnostic failures cannot certify opaque
   nonempty output as source-independent.
3. Each model response inherits the entire input dependency set. The turn's
   saved answer conservatively unions every influencing call and emitted
   source output across all rounds. Dropping a visible citation, paraphrasing,
   changing mode, redacting replay tool bodies, or switching to no-search
   intent does not remove dependencies of inherited prose.
4. Union is associative and deterministic; `unknown` dominates. Deduplicate
   by the full tenant/source/file/revision/ETag tuple. Enforce a size limit;
   overflow becomes `unknown`, never a truncated `complete` set. Raw source
   text, authorization tokens and credentials are excluded from the envelope.

Compaction must propagate lineage explicitly. Its previous summary, complete
turns, split-turn prefix and file-operation annotations each carry their input
union. The new summary and degraded raw archive inherit that union. The
persisted `ContextCheckpoint` gets its **own** lineage field, distinct from
the lineage of the assistant answer whose row hosts it. Save summary and
checkpoint lineage in the same column update. Do not authorize a summary by
checking only the host answer. Input truncation is not proof that a source
dependency disappeared from a previous summary or derived annotation.

Fork preserves/deep-copies answer and checkpoint envelopes unchanged while
remapping session/message IDs. It neither grants source access nor converts
NULL/unknown to complete. Authorize copied generated content before returning
or replaying it; refusal/redaction must not leak copied content through title,
artifact, attachment or checkpoint fields. Rewind/deletion may remove history
but cannot clear dependencies of a surviving summary or fork.

## Commit, read and streaming boundaries

Create pending assistant rows with unknown lineage. A typed repository update
commits answer text, AgentSteps, references, `is_completed` and the sealed
lineage together, using explicit fields so empty/zero values are not skipped
by GORM. Missing or mismatched lineage must fail the save or persist an
unreadable unknown row. A successful completion event and asynchronous
derivative work begin only after this transaction succeeds. Crash, retry or
checkpoint writes must never pair new readable text with stale provenance.

Use one mandatory lineage authorizer before generated content enters history,
any model, or any client response. Cover message get/list/recent/search and
partner loading, Agent repository paging/checkpoint loading, RAG history,
follow-up generation, memory extraction, exports/forks/artifacts, MCP/IM/embed
and reconnect/replay. Unknown assistant content and unknown checkpoints are
denied. A generic redaction marker may replace denied generated fields; source
text, titles, tool output and summary text must not accompany it. Availability
failures stop the operation; optional missing guard wiring cannot pass.

For each complete controlled-source entry, check current interactive identity,
KB/directory grant, source state, publication identity and the recorded ETag.
The saved tuple is evidence, not a grant. Deletion, withdrawal, source outage,
changed ETag or absent identity denies the generated content. Never substitute
the current knowledge row/ETag for the original one. A future explicitly
authorized historical-version experience needs its own policy and version
label; this change must not silently restore old evidence.

Live generation uses a trusted complete **prefix** accumulator, checks it
before model calls and every output batch, and cancels generation on revoked
access. Every durable stream event needs its dependency envelope/sequence so
reconnect cannot replay text before provenance or fall back to the pending
unknown message. Atomically persist each replayable prefix with its lineage
before publishing it; bind it to turn ID and monotonic sequence. The final
message save seals the union. Adding a later tool source must not overwrite
earlier dependencies. This complements bounded source leases: a lease never
extends access after revocation, and lineage does not prove GC read coverage.

History indexing currently receives strings and creates a normal passage.
Change that boundary to load a committed message/envelope and reauthorize it
before enqueue and before worker writes. Until every passage/vector retrieval
and derivative writer preserves/rechecks inherited lineage, deny indexing of
controlled-source or unknown answers. This temporary gate must also cover
pre-existing history passages and mode switches; excluding them from the UI
alone does not prevent retrieval into another answer. The full implementation
requires durable passage provenance and invalidation, not permanent removal
of the product feature.

## Independent ever-source tombstones

Propose a durable source-provenance tombstone table keyed by immutable tenant,
KB and source/pair scopes, independent of their business rows. It has no
cascading foreign key and no normal delete/reset API. A scope is `ever` or
`unknown`; only scopes created under the new guarded writer can carry a
verified `never` proof. The first Nextcloud admission marks tenant, KB and
source scopes `ever` in the same transaction, before source content can enter
any consumer. Type changes, hard deletion, KB deletion, recreation, decommission
and restore cannot erase that fact. Database constraints/triggers plus the
mandatory repository boundary must enforce irreversibility in both dialects.

Backfill `ever` from all retained sources (including deleted ones), KB sticky
markers, revision inventories and other durable source evidence. Absence in
legacy data is `unknown`, not `never`. Restore imports/replays these tombstones
and the publication/withdrawal ledger before reopening access; unmatched or
older backup checkpoints remain closed. Tombstones help classify legacy
ambiguity and prevent deletion from changing it to ordinary. They do not
reconstruct per-answer lineage or authorize an old answer.

## Implementation phases and acceptance

| Phase | Code boundary and required proof |
| --- | --- |
| 1. Schema/types and mandatory policy | Review proposed PG132/SQLite51; add nullable column, typed codec/union, checkpoint envelope and independent tombstones. Test malformed/unknown/overflow and migrations from real pre-change schemas; no legacy complete backfill. |
| 2. Prompt and producer coverage | Wire RAG final prompt/history merges and Agent actual history/tool/model inputs. Instrument every influencing model call and source output adapter. Compare captured input envelopes against saved union; an uncovered adapter must fail closed. |
| 3. Persistence/replay | Atomic answer+lineage and checkpoint writes, fork preservation, durable stream-prefix metadata, mandatory read/replay/history-index checks. Inject crashes between text, lineage, queue and event publication; no readable orphan text. |
| 4. Derivative propagation | Preserve lineage through history passages, memory, suggestions, artifacts and their searches/writers. Inventory legacy derived copies and require source-aware denial/invalidation before enabling indexing. |

Required end-to-end cases include: (a) RAG source answer → citation-free ordinary
follow-up → revoke → both hidden; (b) Agent tool → RAG mode switch and reverse;
(c) search/list metadata with no citation and an empty search after prior source
use; (d) two source files where only one is withdrawn; (e) same file with two
ETags and restore to identical bytes/new generation; (f) revoked KB/directory
grant while ETag stays unchanged; (g) complete and split-turn compaction,
summarizer failure/raw archive and successive summaries; (h) fork, rewind and
parent deletion; (i) paginated search partner hydration and SSE reconnect with
missing/stale event provenance; (j) history-index worker delayed across
withdrawal and a pre-existing source-derived passage; (k) hard-delete source/KB
and backup restore without erasing ever-source evidence; (l) complete empty
ordinary turns remain usable in a tenant that has previously used Nextcloud.
Run both PostgreSQL and SQLite, HTTP plus non-HTTP consumers, and pause at the
actual model-send/output boundaries to verify revocation. Static scans or a
green codec test cannot substitute for these behavioral proofs.

All phases remain unimplemented in this design. Keep the existing
`derived_index` blocker and physical derived deletion gate until the separate
[coverage gaps](nextcloud-derived-gc-coverage-gap-map-2026-10-08.md) are closed
with runtime evidence.
