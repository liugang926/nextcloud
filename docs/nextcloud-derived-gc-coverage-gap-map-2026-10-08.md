# Nextcloud derived GC coverage gap map (2026-10-08)

## Scope and release decision

This is a static path audit of `docs/nextcloud-derived-gc-read-lease.md` and
PRD §7.6–7.7 (`docs/development-plan.md:191-211`). It examines Nextcloud
integration commit `17b0268936888a31402fd83104dee615ee1c7f74` and its
two pinned WeKnora patches, applied to source-only checkouts:

| Patch | WeKnora base | Patch SHA-256 |
| --- | --- | --- |
| `integration/weknora.patch` | `c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` | `db842d71468fd663347e70898e20db982293a34af599df400bf368abe19a5021` |
| `integration/weknora-rag-77c97fd7.patch` | `77c97fd72f26e84435503d24eeed88cb5dfe1f01` | `34c560e03bbc20d4eff3c4ce9d652389f2a4f6056e738d01b8cdc0c05ee2c877` |

The lease-relevant paths below have the same implementation in both applied
patches. `internal/application/service/session_knowledge_qa.go` differs in
unrelated RAG reranker/fallback logic; the read-lease lifetime finding is the
same. No container, full build, runtime race test or production data was used
in this audit. A static audit cannot establish that all legacy tasks drained.

A follow-up source-only patch now rejects the three generic mutation entries
identified below (`ReparseKnowledge`, `ReplaceKnowledgeFile`, `UpdateImageInfo`)
for rows marked by the channel or Nextcloud metadata. Its new fixed/RAG patch
hashes are `49c18b24f8639fba6e0a5bb181af2174e9298b5ddecd9bc41f0764cc6ec14a21`
and `b274b2074c1b5532d52e7f084e6c87306290adf29950478e102e6435c73ac797`.
The table records the original audit gap; this narrow mitigation does not
establish generic repository writer or full derived-GC coverage. The shared
WeKnora image has not been switched to this follow-up patch.

**Block physical derived deletion.** The collector inventories a
`derived_index` blocker (`internal/application/repository/nextcloud_gc.go:215-219,
290-400`). There is no production writer for the required
`nextcloud_content_lease_coverage` activation marker. Both
`ClaimKnowledgeGC` and `ValidateGCClaimInTx` require its locked row and no
live exact or KB-wide lease (`nextcloud_content_leases.go:635-680,689-780`).
The exact chunk and PostgreSQL embedding delete helpers are dormant; the
collector has no call to them. Do not insert a marker or enable either helper
from the findings in this document.

## Read and response paths

The table distinguishes a source publication check from a durable lease.
Publication/grant rechecks can deny output after revocation, but they do not
keep GC from deleting rows while an in-flight reader is using them.

| Path and exact boundary | Current fence | Remaining gap and risk |
| --- | --- | --- |
| RAG QA, `internal/application/service/knowledgebase_search.go:189-207,341`, `knowledgebase_search_results.go:38-40`, `session_knowledge_qa.go:23,768-830` | `HybridSearch` starts a KB-wide lease before search, upgrades result documents to exact leases and checks output before returning. | The function defers lease release at `HybridSearch` return. `KnowledgeQAByEvent` subsequently assembles/reports references and runs `CHAT_COMPLETION_STREAM`; no content lease spans the answer turn or SSE/non-HTTP completion. A pause between retrieval and stream exit has no GC read pin. |
| Agent QA and later rounds, `internal/application/service/session_agent_qa.go:25,159-164`, `internal/agent/tools/nextcloud_read.go:13-56`, `search_knowledge.go:199`, `read_document.go:150-205`, `list_documents.go:91-134`, `query_knowledge_graph.go:118` | Tool-local broad/exact leases and publication rechecks cover individual search, read, list and graph tool calls. | Tool leases close on tool return. `AgentQA` has no document turn lease across later rounds, buffered references or output stream. Its sandbox lease is for the VM, not source rows. The graph tool's outer KB lease closes one earlier per-tool gap, not this lifetime gap. |
| Global `GET /knowledge/search`, `internal/handler/knowledge.go:2513-2638`, `nextcloud_read_leases.go:196-225`, `internal/application/service/knowledge.go:1149-1228` | `respondKnowledgeSearch` checks publication, acquires exact leases on returned rows and guards JSON output. | All three branches (shared agent, restricted API key, own + shared KBs) query before any KB-wide lease. The default service resolves own/shared scopes internally. Its SQL `SearchKnowledgeInScopes` can read a retiring generation with no read lease; result-only exact leases cannot prove zero pre-query readers. The earlier checklist's “global search covered” describes response pinning, not pre-query coverage. |
| Scoped raw searches, `internal/handler/knowledgebase.go:354-458`, `session/qa.go:879-998`, `session/nextcloud_raw_search.go:42-176,281+` | Session `/knowledge-search` pins trusted KB scopes before retrieval, then exact results through output. KB `hybrid-search` uses service `HybridSearch` and reacquires exact output leases. | In KB `hybrid-search`, the service lease ends before the handler's result lease begins. The handler denies output if it cannot reacquire; still prove no unleased hydrated-content gap with a paused two-connection test. |
| Direct document, chunk and file output, `internal/handler/knowledge.go:692-865,1051-1207,1721-1870`, `knowledge_download.go:40-199`, `chunk.go:94-254,359-400` | Selected document/span/list/chunk/preview/download paths hold exact or pre-query KB leases and use output publication checks, including byte checkpoints on streams and ZIP. | `chunk.go:110-130` loads a full chunk before exact lease and then rereads it; revision ownership resolution at `chunk.go:359+` also precedes the exact lease. Batch knowledge at `knowledge.go:1899-2048` loads rows before pinning. Output remains guarded, but the strict no-unleased-row-read proof is missing. Folder aggregates at `knowledge.go:1222-1254` use a live channel count rather than the sticky KB source marker. |
| MCP, `internal/mcpserver/tools_retrieve.go:135-225,274-456`, `tools_ask.go:51-100,223+` | Search/grep reuse the Agent tool lease; machine principal list/read reject Nextcloud rows. | `ask` inherits QA/Agent turn-lifetime gaps. Endpoint authorization alone is not a lease. Add a pause after retrieval/before serialization and explicit machine-deny tests; the inspected MCP server has no direct file resource endpoint. |
| FAQ, Wiki and graph, `internal/handler/faq.go:381,472`, `wiki_page.go:53-82,427,1009`, `internal/application/service/wiki_page.go:43-52`, `internal/agent/tools/query_knowledge_graph.go:118+` | Production Wiki routes reject KBs with a sticky Nextcloud marker/live source through `RejectNextcloudDerivedKB`; graph tool has a KB read lease. | FAQ Search/GetEntry have no source read lease; prove these KBs cannot contain Nextcloud-derived text or reject marked KBs. Wiki service's optional policy interface can fail open under dependency miswiring. Historical Wiki/graph content lacks a complete external inventory/cleanup proof. |
| Historical hydration/replay, `internal/application/service/chat_pipeline/into_chat_message.go:210-290`, `message.go:140-160,250+`, `session_agent_qa.go:159-164`, `agent_history.go:73-139,469-540`, `internal/application/access/nextcloud_history.go:46-150` | Chat message output/history filtering and prompt assembly recheck source publication in selected paths. | `AgentQA` loads history directly from `messageRepo`, bypassing `messageService.filterHistoryMessages`; saved AgentSteps tool output and assistant text can be replayed without that source filter. Persisted rendered text needs provenance-aware fail-closed replay/redaction. A row lease cannot retract historical text (PRD §7.7 cases 6, 8). |

The smallest semantics-preserving `/knowledge/search` repair is a shared scope
resolver that freezes the exact authorized own/shared/agent/API-key KB set,
acquires broad leases in stable `(tenant, KB)` order before
`SearchKnowledgeInScopes`, holds them through JSON, then pins/rechecks each
returned source generation. An empty/missing/changed scope or absent lease
store must fail closed for source-derived content. Test a paused pre-query SQL
read against retirement/claim on two connections, all three branches, and
publication revocation before serialization. Moving the lease only around
`respondKnowledgeSearch` would not close this gap.

## Build, mutation and external adapter paths

| Path and exact boundary | Current fence | Remaining gap and risk |
| --- | --- | --- |
| Queued parsers/fanout, `internal/router/task.go:264-305`, `sync_task.go:165+`, `nextcloud_build_lease.go:63-138`; SQL chunk/knowledge/vector writes at `internal/application/repository/chunk.go:68-80,392-401,441-453,499-510,546-556`, `knowledge.go:334-380`, `retriever/postgres/repository.go:132-153,203-231` | Selected Asynq and Lite document/fanout tasks acquire renewable exact build leases at dequeue; selected SQL writes validate token/version in the transaction. KB profile final update checks the sticky marker (`knowledgebase_profile.go:83-87,153+`). | This is selected worker/SQL coverage, not every writer or external commit. `ValidateNextcloudBuildWrite` returns nil when no build context (`nextcloud_build_context.go:50-72`); only covered entry points supply that context. |
| Generic reparse, `internal/handler/knowledge.go:2241-2275,2953-3025`, `internal/application/service/knowledge_process.go:2628-2740,4377-4410`, `internal/router/task.go:300`, `internal/application/service/knowledge_reparse_scope.go:15-75` | `loadKnowledgeWrite` verifies tenant/KB edit grant, but not Nextcloud provenance. Batch task validates IDs and KB before calling the same service method. | **Immediate destructive gap:** non-manual `ReparseKnowledge` synchronously calls `cleanupKnowledgeResources` before queueing. That cleanup deletes index, chunks, extracted images and graph (`knowledge_delete.go:626-706`); `chunk.go:693-700` deletes without a lease if the context has none. The batch reparse registration is not wrapped with `wrapNextcloudBuild`. A later guarded `UpdateKnowledge` failure cannot undo preceding deletion. |
| File replace, `internal/application/service/knowledge_replace.go:41-183` | It calls `ReparseKnowledge` after changing the document's source. | A reparse-only guard is insufficient: `ReplaceKnowledgeFile` saves a new object and updates file/source columns before its reparse call. It must reject source-managed rows before object save, task dequeue or row update. |
| Direct image edit, `internal/handler/knowledge.go:2443-2477`, `internal/application/service/knowledge_process.go:3123-3280` | Service verifies document/chunk ownership; no build lease is acquired. | `UpdateImageInfo` can create OCR/caption child chunks, update existing chunks and their vectors. Generic chunk writes accept a context without a build lease. Reject source-managed rows before any child/index mutation until a source-aware transactional writer exists. |
| PostgreSQL vector and external vector stores, `internal/application/service/knowledge_process.go:705-709`, `internal/application/repository/retriever/keywords_vector_hybrid_indexer.go:179-269`, `retriever/factory.go:154-164`, `retriever/milvus/repository.go:283-315`, `retriever/opensearch/crud.go:32-136`, `retriever/qdrant/repository.go:246-287` | Local pgvector writes carry transactional token and source-version checks. | Milvus/OpenSearch/Qdrant commit outside that SQL transaction; a preflight lease check cannot fence a remote upsert delayed past retirement. External IDs and backend acknowledgements are not in the exact GC inventory. A nil KB binding can choose tenant engine. Deny Nextcloud binding to unsupported external engines and retain historical external copies as blockers. |
| Lite SQLite vector/FTS, `internal/application/repository/retriever/sqlite/repository.go:144-183,493-500,535-541` | Task wrapper may supply a build context. | Embedding, FTS and vec writes are separate; this adapter has no final in-transaction lease/version check or exact inventory receipt, and helper failures can be swallowed. Deny Nextcloud on Lite or implement one source-aware atomic transaction with rollback tests. |
| Graph, `internal/application/service/extract.go:354-374`, `knowledge_post_process.go:243-255`, `internal/application/repository/retriever/neo4j/repository.go:46-109` | Extract/post-process can run inside a wrapped task. | Neo4j `AddGraph` is an external commit after preflight; no final fence, inventory or backend acknowledgement. Deny Nextcloud graph extraction/config admission until the graph adapter is fenced, and block historical copies. |
| Extracted images/objects, `internal/application/service/knowledge_process.go:3790-3815`, `image_resolver.go:80-144,202,395,534,602,734,810`, `file/resource_catalog.go:70-131`, `internal/application/repository/nextcloud_gc.go:224-241`, `nextcloud_gc_objects.go:24-45,208-221` | GC sees image URLs stored on chunks and only deletes local, scoped Nextcloud-provenance objects. | SaveBytes puts the object before catalog registration/chunk persistence; a crash can leave an uninventoried object. The derived image path does not visibly attach the source provenance used for the original file. Cloud/unknown providers remain blocked. Need durable pre-upload intent, exact source ownership and provider acknowledgement or deny source image uploads. |
| Wiki writes, `internal/application/service/wiki_ingest_batch.go:297-302,935-943`, `wiki_page.go:43-52`, `internal/handler/wiki_page.go:53-82` | Normal ingest/finalize and HTTP routes call the source-derived KB rejection policy. | Some service checks depend on optional interfaces/dependencies; prove mandatory policy at every internal entry and legacy row. Historical external Wiki output needs provenance and deletion acknowledgement. |

The follow-up fail-closed code change is at the **service entry**,
before any `OpenAttempt`, metadata update, object save, task dequeue or
cleanup: reject source-managed rows in `ReparseKnowledge`,
`ReplaceKnowledgeFile`, and `UpdateImageInfo`. Reuse
`isNextcloudKnowledge` (`internal/application/service/nextcloud_derived_policy.go:17-39`),
which checks the channel and three Nextcloud metadata keys and rejects
malformed metadata. A legacy row with only generic `datasource_id` /
`external_id` remains ambiguous; resolve it against the trusted source or
version repository, and fail closed in a marked KB when provenance cannot be
established. Keep the repository write boundary source-aware as the later
defense against a new bypass. Focused tests should cover each public/service
entry, batch reparse, a source row with `Channel` rewritten but retained
Nextcloud metadata, malformed/ambiguous metadata, and an ordinary document.
For every denied case assert unchanged chunk/index/graph/object/source row and
no queued work. The patch adds representative no-side-effect tests on both
pinned bases; the offline focused Go test could not start because required
modules are not cached on this host, so CI remains the compile gate. No
runtime race test was run. This is a repair of active mutation risk, **not** permission
to activate derived GC.

## PRD §7.6–7.7 acceptance crosswalk

| Requirement | Audit result / remaining proof |
| --- | --- |
| §7.6 admin status, expiry, estimated vs confirmed reclaim, failed reason, retry, preview and guarded immediate collect | `internal/handler/nextcloud_gc.go:16-64` exposes tenant-admin list/retry and `internal/application/repository/nextcloud_gc.go:63-80,499+` stores job state/bytes. No audited per-item preview, estimated-versus-confirmed capacity view, or guarded manual immediate collect endpoint. Do not claim physical bytes reclaimed from a completed source-file item. |
| §7.6 KB retention quota and 80%/90% storage watermarks | No matching Nextcloud GC quota/watermark/admission path was found in the inspected `internal`/`web`/`frontend` implementation. Thresholds are proposed PRD values, not enabled behavior. Each volume/bucket still needs independent measurement and a source-preserving admission rule. |
| §7.7 cases 1–3, generation ordering and strict latest answers | Existing source-version candidate/publish and retrieval publication checks cover selected paths; repeat across all QA, raw search, MCP, Agent and historical hydration after the read gaps above close. |
| §7.7 cases 4–5, crash/restart and shared resource references | Durable GC jobs/items and dormant exact local delete helpers exist. Final cleanup, external backends, object intent and concurrent shared image reference proof are missing. Exercise failure at each item receipt and remote commit boundary. |
| §7.7 cases 6–8, revocation during lease and historical answers | Output rechecks exist on selected paths; turn-lifetime leases and Agent history provenance filtering remain open. Test revocation after retrieval and before every answer/stream chunk; an expired or cancelled turn must release its pin. |
| §7.7 cases 9–10, capacity and backup restore | Watermark admission and backup-ledger replay/closed-until-reconciled proofs remain outside this audit. Neither can be inferred from the current GC gate. |

**Activation exit gate:** prove all reader and writer scopes with paused
two-connection races and cancellation/expiry tests on both pinned patches;
inventory and acknowledge every local/external derived resource; drain legacy
work and streams; only then write a coverage marker for the audited revision.
Until that sequence is complete, keep `derived_index` blocked and physical
derived GC disabled.
