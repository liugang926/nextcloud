# V1 implementation status

This repository currently supplies a local development slice of the PRD, not a V1 enterprise release. The source PRD is [development-plan.md](development-plan.md); its proposed endpoints and product claims are requirements, not evidence of implementation.

| PRD area | Local implementation | Remaining work before V1 |
| --- | --- | --- |
| V0 Docker and app | Pinned Nextcloud 34.0.4, PostgreSQL 16, Redis 7 images; separate volumes; installable app; sample binding and smoke scripts | Verify upgrade/rollback and backup/restore on a clean host; lock all adjacent WeKnora runtime images |
| Binding API | Bearer-protected capabilities, bindings, paginated manifest and conditional content; bound folder checks | Admin connection UI, binding policy validation, pairing, HMAC with replay protection and key rotation; scalable snapshot pagination |
| Publication decisions | Persistent administrator withdrawal exclusion and audit | Immediate WeKnora retrieval block, file event outbox, published revision state and publication comparison/swap |
| WeKnora connector | Fixed-baseline patch with full/incremental scanning, ETag-checked downloads and two complete scans before a tombstone | Durable inbox/checkpoints, event-driven wakeups, safe version swap, GC, full reconciliation under concurrent changes |
| Permissions | Local administrator-only withdrawal API | AD objectGUID identity mapping; per-user Nextcloud authorization; fail-closed checks across **all** retrieval and file access routes |
| Operations | Local smoke and connector tests | Load/fault tests, monitoring, backup rehearsal and pilot acceptance |

## Authorization blocker

WeKnora's existing knowledge-base read permission does not verify a user's current Nextcloud folder permission. There is no single existing check that covers search, RAG, Agent direct reads, knowledge/chunk detail, previews/downloads, history, public/embed/MCP/API-key routes, and signed resource URLs. A successful local import therefore **does not** make a Nextcloud knowledge base safe for enterprise use.

V1 needs a persistent binding and publication ledger independent of document metadata. For every read, a shared `PublicationGuard` must resolve the authenticated WeKnora user to a unique directory identity (directory ID and AD objectGUID), verify the current Nextcloud binding policy and read permission, and reject access when identity or source state is unknown. Search candidates must be filtered before they reach the model; direct document, chunk, file and history reads must use the same guard. Machine/public routes without a verified person must reject Nextcloud source content. Anonymous resource grants cannot carry Nextcloud-derived bytes. Long streams need rechecks before output. These controls require an entry-point audit and tests for permission loss, outages, old messages, direct IDs and file URLs.

Until those controls pass, use only synthetic local fixtures and keep Nextcloud-sourced knowledge bases out of shared retrieval and public access.

## Scale and consistency limit

The current manifest is recomputed from the whole directory on every page request. For 10,000 files and 200 items per page, a complete pull traverses roughly 50 whole trees. It can time out and is not a transactional snapshot if files move during traversal. The connector rejects a generation change between pages and confirms absence twice, but V1 still needs a durable generation or equivalent snapshot protocol plus load and fault tests before relying on this manifest for deletion at pilot scale.
