# Nextcloud derived-row GC: lease blocker and required fence

Status: design and acceptance checklist. Physical derived-row deletion remains
disabled until every listed read and build path uses the durable fence.

## Decision

Do not enable general derived-row deletion. The patches contain a dormant, backend-specific PostgreSQL exact-vector-ID delete helper with claim validation and an atomic item receipt; the collector does not call it. The broader experimental local deletion prototype remains unmerged. PRD §7.4 requires **no active build or read lease** before deleting retired chunks/indexes. The repository has a durable lease and fence foundation with partial read and worker coverage, but it does not cover every Nextcloud path or external write. A terminal `parse_status`, `pending_subtasks_count = 0`, and a one-hour delay do not prove this condition. The `derived_index` blocker still prevents a false completed job.

## Read paths that need coverage

| Path | Where work begins | Lease scope and lifetime |
| --- | --- | --- |
| RAG QA, including SSE and non-HTTP calls | `internal/application/service/session_knowledge_qa.go:23`, `internal/handler/session/qa.go` | Acquire a short KB read lease before vector search (document IDs are unknown), then hold exact knowledge leases for hydrated references until the answer stream exits. Existing sandbox turn leases are session/VM leases, not document GC leases. |
| Agent QA and dynamic tools | `internal/application/service/session_agent_qa.go:25`, `internal/agent/tools/search_knowledge.go:165`, `read_document.go:119`, `list_documents.go:79`, `query_knowledge_graph.go:73` | Cover each search/read/tool call, including later agent rounds and buffered source references until stream completion. `read_document` resolves a document or chunk then reads more chunks, so acquire an exact lease before content hydration. |
| Raw search APIs | `internal/handler/knowledgebase.go:354`, `internal/application/service/knowledgebase_search.go:125`, `internal/handler/session/qa.go:879`, `internal/handler/knowledge.go:2331` | KB lease before retrieval; exact knowledge leases for results through response serialization. |
| Direct document/chunk reads | `internal/handler/knowledge.go:725` (spans), `internal/handler/chunk.go:72`, `internal/handler/knowledge.go:1623` (download), `:1675` (preview), batch ZIP in `internal/handler/knowledge_download.go` | Exact knowledge lease from authorization through last byte copied or response completion. Source publication must still be rechecked on streaming checkpoints; a lease never grants continued access after revocation. |
| MCP tools | `internal/mcpserver/tools_retrieve.go:135,187,274,384` (search/grep/list/read), `internal/mcpserver/tools_ask.go:51` | Same KB/exact-document scheme. The endpoint scope is a permission check, not a durable read lease. |
| FAQ, Wiki, graph and historical hydration | `internal/handler/faq.go:381`, `internal/handler/wiki_page.go:427,1009`, `internal/agent/tools/query_knowledge_graph.go:73`, `internal/application/service/chat_pipeline/into_chat_message.go` | Cover source-derived content that may still be read or embedded into an answer. External Wiki/graph physical cleanup additionally needs provenance and backend acknowledgement. Historical message text already persisted is a separate revocation problem; a row lease alone cannot retract it. |

The publication guard (`internal/application/access/nextcloud_publication.go:60`) checks live source access at exposure points, but does not count in-flight readers. Existing tests of deny paths do not establish a zero-reader proof for GC.

As of 2026-10-01, selected direct HTTP reads and downloads, HybridSearch,
global `/knowledge/search`, `ListKnowledge`, raw session `/knowledge-search`,
Agent document tools and answer output hold
renewable read leases and recheck publication and grants at output. A focused
test retires a search result after lease acquisition, proves an active lease
blocks GC claim, and denies the title before JSON output. MCP list/read reject
machine retrieval of Nextcloud source rows; current MCP server exposes no file
resource endpoint.
The table remains the full coverage checklist: Wiki, other raw reads,
historical hydration and external adapters still need enumeration and tests.

The 2026-10-02 patch closes the identified Agent `query_knowledge_graph` gap:
an outer KB lease now spans `HybridSearch`, result filtering, formatting and
the final output check. Each displayed result is compared with its freshly
loaded knowledge row's source channel and full metadata, and current
document/tag scope and source publication are checked before output. Focused
tests on both pinned WeKnora bases cover a retired result, revoked source or
tag access, duplicate chunks with mixed generations, and missing lease
providers. The patch is now in the shared LAN image. Wiki,
historical hydration, external adapters and complete writer coverage remain
open; the derived-index deletion gate stays disabled.

## Build/write paths that need coverage

The Asynq registrations in `internal/router/task.go:264-305` include document parse (`ProcessDocument`, `knowledge_process.go:3380`), manual update (`:3269`), summary (`:1164`), question generation (`:1555`), image multimodal (`image_multimodal.go:151`), post-process fanout (`knowledge_post_process.go:83`), chunk extract/data-table summary, Wiki ingest/finalize, and clone/move/reparse dispatch. The direct/manual passage path in `knowledge_create.go` also calls `processChunks` (`knowledge_process.go:326`). All paths that can write chunks, embeddings, image references, summaries, graph or Wiki output need a source-scoped build lease or must be proven unable to run for Nextcloud knowledge.

Queued tasks must acquire a lease at worker start, before reading or writing. Registration at enqueue alone is insufficient because a queued/retried task can start after retirement. Each DB/index write must verify its lease token and fence epoch; a worker whose heartbeat expired or was cancelled must stop before the write. This is especially important for legacy `Attempt=0`: `attemptSuperseded` in `knowledge.go:245` returns false for such tasks. `pending_subtasks_count` helps track fanout, but is not an authoritative active-worker ledger and cannot fence late retries or external writes.

Selected Asynq/Lite document workers now acquire and renew build leases, and
chunk, knowledge and source-version SQL writes validate the lease inside their
transaction. PostgreSQL pgvector `Save`/`BatchSave` now verify the exact build
lease and current source-version candidate in the same transaction as their
embedding write. If a GC job already exists, that transaction also inventories
the actual embedding primary keys as `postgres_embedding` items; a failed
receipt rolls back the vector write. Stage, publish and tombstone serialize
with this writer on the KB and exact fences. A newly queued parser waits under
its exact build lease until the matching source-version Stage commits before
entering the handler; an absent Stage times out and a retired fence rejects
immediately. The pinned fixed and RAG patches passed two-connection
write/retirement, early-worker admission, stale-publication, tombstone-order,
backend-disconnect/retry, and exact-delete/retrieval tests in a disposable
pgvector PostgreSQL. External vector providers, graph, object and Wiki writes
remain outside this fence. No coverage activation marker is written, so global
derived GC stays denied.

## Proposed durable protocol

1. Add a per-knowledge fence keyed by `(tenant_id, knowledge_base_id, knowledge_id)` with source tuple, `epoch`, state `open | retired | deleting`, and retirement time. The state is never reopened for an old generation; restoration creates a new knowledge/version ID.
2. Add a lease table with `lease_id`, tenant/KB/knowledge (a KB-wide read scope is allowed while search IDs are unknown), `kind = read | build`, `epoch`, worker/turn ID, database-clock `expires_at`, heartbeat and released timestamps. Index live leases by scope/kind/expiry. Store no content or credentials.
3. Acquire and renew under the same KB/source/fence lock order used by publication and GC. A reader/build worker may acquire only while the relevant generation is open and authorized. A read lease cannot override a publication/access denial. Use a bounded renewable TTL with a hard maximum request/task duration; a failed renewal cancels work.
4. Retirement atomically closes acquisition and advances the epoch while hiding publication. GC may claim physical deletion only after the safety window, no active exact or KB-wide read lease, no active build lease, and no queued worker can acquire a new one. Check again at the exact item delete transaction. The GC item token still controls retries; the new fence controls writers/readers.
5. Roll out worker coverage before enabling deletes. An empty new lease table does not prove zero legacy in-flight tasks. Record a lease-protocol activation watermark, drain or cancel pre-rollout Asynq tasks and long streams, then permit deletion only for generations with proven coverage. Unknown history stays blocked for manual review.

## Acceptance tests before enabling the prototype

- PostgreSQL two-connection race: active read or build lease blocks GC; GC fence committed first prevents new lease; neither order allows a lease and deletion claim to coexist.
- SSE/Agent/MCP/raw-search/direct-download tests pause after source content has been read but before response completion. Retirement immediately stops new source output; GC reports `read_lease_active` until release or bounded expiry, then deletes the exact local rows.
- Delayed Asynq retries for `ProcessDocument`, summary/question, image multimodal and legacy `Attempt=0` fail to acquire after retirement. A worker whose renewal expires cannot recreate chunk/index rows after GC.
- Crash/restart tests expire stale leases and recheck the fence. A stale token cannot write or acknowledge a newer claim. GC deletion and receipt remain atomic, and unrelated tenant/KB leases do not block each other.
- SQLite and PostgreSQL scoped deletion tests from the isolated prototype can be reused after the lease gate is wired. The prototype passed focused tests against a disposable PostgreSQL 16 container and SQLite FTS5/vec build; test success does **not** satisfy the missing lease precondition.

## Isolated runtime gate check, 2026-10-02

The [redacted synthetic drill](evidence/nextcloud-publication-reconcile-gc-2026-10-02.json)
with the complete RAG77 image withdrew an indexed file and observed a
two-scan source tombstone. After restarting only the disposable WeKnora app,
the GC job was blocked with `derived_index`, `derived_chunk` and
`postgres_embedding` items blocked, while the old physical chunk and enabled
embedding each still had one row. The tenant/KB coverage marker had zero
rows. The source file item was collected, and the old knowledge row was no
longer visible. This checks the live fail-closed gate on one synthetic
generation; it does not enable or prove physical derived cleanup.
