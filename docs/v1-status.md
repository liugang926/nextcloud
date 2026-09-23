# V1 implementation status

This repository currently supplies a local development slice of the PRD, not a V1 enterprise release. The source PRD is [development-plan.md](development-plan.md); its proposed endpoints and product claims are requirements, not evidence of implementation.

| PRD area | Local implementation | Remaining work before V1 |
| --- | --- | --- |
| V0 Docker and app | Pinned Nextcloud 34.0.4, PostgreSQL 16, Redis 7 images; separate volumes; installable app; sample binding and smoke scripts | Verify upgrade/rollback and backup/restore on a clean host; lock all adjacent WeKnora runtime images |
| Binding API | Bearer-protected capabilities, bindings, short-lived paginated manifest and conditional content; bounded folder checks; administrator settings page | Binding policy and knowledge-base validation, pairing, HMAC with replay protection and key rotation; scale and fault tests beyond the 207-file smoke |
| Publication decisions | Persistent administrator withdrawal exclusion and audit; file-event hint outbox | Immediate WeKnora retrieval block, outbox consumer and retention, published revision state and publication comparison/swap |
| WeKnora connector | Fixed-baseline patch with full/incremental scanning, ETag-checked downloads and two complete scans before a tombstone; failed Nextcloud replacement checks stop creation | Durable inbox/checkpoints, event-driven wakeups, safe version swap, GC, full reconciliation under concurrent changes |
| Permissions | Administrator-attested AD objectGUID to Nextcloud UID mapping and a fresh per-user source authorization endpoint | WeKnora caller integration and fail-closed checks across **all** retrieval and file access routes; verified AD backend semantics and permission matrix |
| Operations | Local smoke and connector tests | Load/fault tests, monitoring, backup rehearsal and pilot acceptance |

## Authorization blocker

WeKnora's existing knowledge-base read permission does not verify a user's current Nextcloud folder permission. There is no single existing check that covers search, RAG, Agent direct reads, knowledge/chunk detail, previews/downloads, history, public/embed/MCP/API-key routes, and signed resource URLs. A successful local import therefore **does not** make a Nextcloud knowledge base safe for enterprise use.

V1 needs a persistent binding and publication ledger independent of document metadata. For every read, a shared `PublicationGuard` must resolve the authenticated WeKnora user to a unique directory identity (directory ID and AD objectGUID), verify the current Nextcloud binding policy and read permission, and reject access when identity or source state is unknown. Search candidates must be filtered before they reach the model; direct document, chunk, file and history reads must use the same guard. Machine/public routes without a verified person must reject Nextcloud source content. Anonymous resource grants cannot carry Nextcloud-derived bytes. Long streams need rechecks before output. These controls require an entry-point audit and tests for permission loss, outages, old messages, direct IDs and file URLs.

Until those controls pass, use only synthetic local fixtures and keep Nextcloud-sourced knowledge bases out of shared retrieval and public access.

## Scale and consistency limit

The first manifest page scans the whole bound tree once and stores a sorted list for ten minutes. Later pages read that list and reject a changed root ETag or publication audit revision. A 207-file local smoke covered paging, an intervening write and conditional content reads. This is still not a transactional source snapshot: changes during traversal can invalidate a page, and a large list is deserialized on each page. The connector rejects a changed generation and confirms absence twice, but V1 still needs load and fault tests, verified root ETag propagation, and a reconciliation protocol before relying on this manifest for deletion at pilot scale.
