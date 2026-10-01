# V1 implementation status

This repository implements a local development slice of the [PRD](development-plan.md), not a V1 enterprise release. The PRD describes requirements; the table records the historical implementation and tests through 2026-09-24, with a newer addendum below. Only synthetic local data has been used for cross-system testing.

| PRD area | Implemented and checked locally | Remaining before V1 |
| --- | --- | --- |
| Docker and app | Nextcloud 34.0.4, PostgreSQL 16 and Redis 7 use separate volumes; the app installs and upgrades; Compose includes cron, separate five-second event sender and applied-status workers, and a mock embedding service. A deterministic app packaging script emits a runtime-only 0.4.24 archive and verifies its install layout. A disposable fresh Nextcloud stack installed the preceding 0.4.23 archive without a source bind mount, created the expected migration tables, and returned authenticated DAV 207. The local shared stack upgraded to 0.4.24; a fresh 0.4.24 install and direct 0.4.6-to-0.4.24 upgrade passed GitHub CI at `a134851`. The existing `weknora-ldap-local` app can use an image built from a fixed WeKnora commit plus this repository's patch. | Pin a production image and dependency matrix for both stacks; verify rollback and coordinated backup/restore. |
| Binding and source API | Administrator settings can create bounded, nonoverlapping folder bindings, stop or resume an entire binding, and withdraw or republish individual files. Stop preserves originals, machine keys and per-file exclusions while new authorization, manifest and content reads fail closed; resume revalidates the current root and emits a reconciliation hint. The service API supplies capabilities, paginated manifests, conditional file reads, ordered change hints, and current per-user source authorization with a live file ETag. The WeKnora repository serializes data-source writes on the owning knowledge base and rejects mixed or historical sources and documents when establishing a Nextcloud-dedicated knowledge base. Nextcloud and WeKnora now expose a headless two-phase source-pairing protocol for a new dedicated knowledge base, with a one-time operation key, exact signed commit, a paused pending source and idempotent retry. The operator CLI and focused Nextcloud HTTP, Go and SQLite checks cover this protocol. Local HTTP smoke exercises a 207-file manifest and authorization/event cases. Machine API requests use a canonical HMAC signature, timestamp, database-backed nonce and overlapping key rotation; replay, tampering and immediate revocation have local HTTP checks. Each machine key belongs to one binding, and cross-binding requests are denied. WeKnora requires an operator-approved destination, and transaction checks prevent a KB editor from repointing a stored token or reviving a cleared credential. A disposable cross-system fault smoke verifies lost-key recovery, exact pending abort, wrong-operation rejection and active-pair abort rejection. An active paired source completed a live key rotation and subsequent manual sync. Ordinary administrator DELETE returns HTTP 409 for any binding with pairing history and preserves its credentials. A signed empty-source retirement path now handles only new pairs with a durable never-touched proof; both sides must acknowledge the exact operation before Nextcloud retires its binding and keys. | Validate the live GUID proof against the target AD, production TLS and reverse proxy; test larger folders, source failures and rotation faults against the isolated live pair. PostgreSQL now has a first-stage indexed-pair withdrawal that pauses publication, revokes its event receiver and retains an append-only observed local inventory. It always reports inventory incomplete and never ACKs Nextcloud; a complete historical/external copy inventory, physical GC receipts and credential retirement remain open. The empty-source path leaves a paused WeKnora tombstone and does not clean indexed copies. |
| Change journal and connector | The Nextcloud outbox records file hints. A daily job retains at least 30 days and advances a transactional cursor floor; an old cursor returns 409. The WeKnora connector requires exactly one selected binding, consumes hints, periodically reconciles a full manifest, downloads with ETag checks, and requires two complete scans before treating absence as deletion. The fixed-baseline Go integration tests pass locally. The patch has a pair-scoped signed event receiver with a transactional inbox and receipt watermark. Its administrator API can provision, rotate and revoke a connection after live binding validation; the one-time secret is encrypted at rest. Nextcloud 0.4.24 stores a scoped encrypted sender credential, signs bounded batches, verifies durable-only 202 receipts, persists backoff or pause state, exposes an administrator retry for a paused sender without advancing its receipt cursor, and protects hints from retention until a signed same-scope applied watermark covers them. Local fault tests cover retry, false receipts, pause, rotation, redirect rejection and fairness across bindings; a synthetic cross-system run matched both receipt watermarks. The WeKnora dispatcher persists a leased queue intent, checks source inventory and instance identity, and serializes Nextcloud manual, scheduled and event sync admission through PostgreSQL/SQLite. A later paired-source run verified real queue acceptance and matching verified applied watermarks for both an upsert and a two-scan delete. | A 60-second Nextcloud background job now emits redacted, deduplicated log warnings for paused senders, old pending hints, failed or stale signed applied-status polling, and unavailable binding roots, with recovery logs; it executed in the local stack. External alert routing, complete item-level publication evidence, and recovery of uncertain manual/scheduled enqueue remain incomplete. The connector streams one downloaded file at a time only after a complete metadata manifest preflight and keeps the old cursor until all emits succeed. Metadata and one file body remain buffered; scoped event scans re-download touched files after a complete manifest preflight, while broad hints and the independent daily content audit re-download all importable files. There is no mid-scan resume. The dispatcher now polls every five seconds by default and the local sender runs every five seconds, but the end-to-end P95 10-second task latency and 10,000-file/100-GB performance targets remain unaccepted. An item already being written can finish after connection revocation; long-running and partial-failure acceptance remains. Event-connection HMAC rotation accepts only the immediately previous key for up to two minutes; a second rotation discards the oldest key, and revocation rejects both keys immediately. A sender that receives 401 still requires operator inspection. Active source machine-key rotation has a maximum 24-hour overlap after commit, ends it at final ACK, and supports crash retry; a finalized rotation invalidates any event connection pinned to the prior source config hash. A 202 receipt does not mean a hint has been applied. Connected hints can be pruned after the minimum retention window only when the verified applied watermark covers them, so cross-service restore still cannot assume full historical replay; a verified full manifest reconciliation remains necessary. The outbox is a hint feed, not a deletion authority. |
| Source identity and read guard | Nextcloud administrators register one-to-one AD objectGUID ↔ Nextcloud UID mappings only after a fresh LDAP GUID comparison; each authorization repeats that comparison. A local PHP contract and strict local-account HTTP denial passed. The synthetic authorization smoke passed only with an explicit, local-Compose-only identity bypass. The WeKnora patch requires an explicit linked web user, fresh directory state, exactly one selected binding, a live Nextcloud allow decision, and equality between the imported and current source ETag. Guarded Go tests cover search/RAG, Agent tools, direct knowledge/chunk/file/resource reads, history and live streams, embed/MCP/public routes, and derived-content restrictions. Missing identity, stale source, unsupported caller, or unavailable authorization fail closed. Local HTTP smokes check direct and group folder shares, group membership removal and restoration, share revocation, disabled account, mapping revocation and publication withdrawal against Nextcloud's live mount view. A separate synthetic Team folder probe on groupfolders 22.0.6 checks group access, a file-level advanced ACL deny and restoration, and group membership removal. | Prove both services use the same enterprise AD and verify real user, nested AD group, primary group and production Team folder ACL semantics. Complete a browser/API entry-point audit and a permission/revocation matrix with authorized and denied users. Conservative history rules may hide otherwise permitted mixed-source answers; existing presigned URLs require expiry planning. |
| Publication and version lifecycle | Manual withdrawal and binding-wide Stop publication block new Nextcloud source reads immediately. A changed source ETag prevents WeKnora from serving an older imported version through the guarded paths. Nextcloud provenance is persisted on imported resources, including a fail-closed legacy/unknown state. A durable per-source version row now stages an invisible candidate, publishes only the currently desired candidate after parse completion and a signed, exact source recheck, then probes again after commit; confirmed deletion is tombstoned. PostgreSQL migration 126 / SQLite migration 45 add an append-only observation for every subsequent local version-row write; pre-upgrade rows permanently carry `legacy_history_unknown`, and the sequence is not a complete external-copy inventory. Focused Go and Nextcloud HTTP tests cover the recheck. An isolated live file was published, overwritten with a new candidate, and tombstoned after deletion using the new image; signed HTTP fault injection now covers withdrawal and ETag change between publication probes; the second probe rejects both with published=false. A transient SQL marker between the local commit and final probe remains visible to direct SQL/vector readers, so all external read paths require live source authorization. Repository tests cover failed parsing, old-task completion and physical-row retention. Durable GC inventory, retry state and tenant-admin status are implemented. A leased transaction claim now releases registered, tenant-scoped local source objects only after their retention deadline and last binding disappears; provider-confirmed unlink is counted once. An isolated PostgreSQL 120→121 upgrade and two synthetic tombstone jobs confirmed two 37-byte local unlinks, zero remaining bindings and 74 fewer bytes under the local file volume. | Complete the PRD's cross-system document-revision and publication ledger, an atomic cross-system publication guarantee, production-scale failed-staging recovery, cancellation and version-aware history; clean derived indexes, chunks, graph/vector and cloud objects, 90-day history retention, metrics, and restore/replay tests. The two tested jobs remain blocked on derived indexes even though their local originals were collected. An exact-ID local chunk/embedding deletion helper remains dormant: selected retrieval, streaming and build paths now hold leases or write fences, but complete reader and external-writer coverage is still missing, so GC coverage remains denied by default. The [read/build lease gate](nextcloud-derived-gc-read-lease.md) records the concrete paths and race tests needed before enabling deletion. Migration 122/41 reopens older falsely collected jobs and retains a derived-index blocker for every historical Nextcloud GC job; chunk and local embedding IDs are inventoried, but physical vector/chunk cleanup remains unimplemented. The version row provides a narrow visibility barrier, not an atomic cross-system publication guarantee. |
| Product and operations | The administrator publication settings and focused HTTP/Go smoke tests exist. The admin page can inspect the latest local source-pairing state, install a one-time WeKnora event credential, inspect local delivery status and revoke its local sender. It displays durable received and separately verified applied watermarks, including the last check result. Separate operator CLIs prepare, retry, abort pending pairs and rotate source pairing credentials; pairing actions are not yet integrated into the admin page. A Files sidebar shows each readable file's source scope, withdrawal or binding-stopped state and modification time, with a configurable link to the WeKnora login only while source publication is open. It now accepts a signed, pair-scoped WeKnora status for the current file ETag and reports ready only for a completed, enabled publication of that ETag; Q&A authorization remains separate. The signature contract, source-side HTTP smoke and a live paired-status/withdrawal check pass. After a signed current-ETag `ready`, withdrawal immediately changed the sidebar result to `withdrawn / unverified`; the temporary event connection was revoked afterward. Administrator diagnostics show binding-root availability, retained source hints, explicit withdrawals, and configured senders' local pending outbox count/oldest age alongside their receipt and separately verified applied watermarks. These counters exclude unconfigured bindings and already received retained hints, and do not establish WeKnora task queue, synchronization, parsing, indexing or per-file publication health. An isolated Docker backup/restore drill verifies synthetic Nextcloud and WeKnora database, file and key fixtures without touching live volumes. | Enterprise failure/retry UX acceptance with real AD and pilot files, physical WeKnora copy cleanup after Stop, consumer-aware monitoring, load/fault tests for the PRD's 10,000-file/100-GB pilot assumption, coordinated restoration of the actual application stack and pilot acceptance. Personal embedded Q&A and SSO are V1.1 work. |

The WeKnora patch also exposes an administrator-only paired-source health GET.
It reports scoped event inbox watermarks and backlog, retained sync-log states,
and current candidate parsing counts. SQLite, PostgreSQL, handler authorization,
and router tests pass. These database observations do not count the shared
Redis task queue or prove that a file is indexed, published, or readable.

## 2026-09-30 integration update

Nextcloud app 0.4.29 was packaged as a runtime archive and upgraded in the
shared local Docker stack. Its LAN listener is `http://10.106.105.128:18082`;
`scripts/allow-lan-access.sh` sets the browser origin used for original-file
citations. The Files sidebar shows a question handoff only for a readable,
current, signed `ready` file. WeKnora's browser route and interactive
`ask-target` endpoint resolve that exact paired file after current personal
knowledge-base, group, source and ETag checks. The manifest keeps separate
machine content and browser Files URLs. Existing indexed files need reindexing
to acquire the latter citation metadata. A URL contract checks IPv4, bracketed
IPv6 and reverse-proxy path handling for original-file links.

The 0.4.29 runtime archive has SHA-256
`54f2de890d784b8f0296948209e8f9dfff0864be5b7fed2b199bb2e908947b5e`
and 73 verified runtime files. `occ upgrade` completed on the shared stack;
the installed app reports 0.4.29, maintenance mode is off, and both LAN entry
points returned HTTP 200. A fresh disposable Nextcloud stack installed that
archive, created migrations and returned authenticated DAV 207; it was cleaned
afterward.

Another isolated install used the packaged 0.4.29 app to verify an actual
WebDAV file across app disable, re-enable and Nextcloud restart. The test
read and overwrote the same file while the integration app was disabled,
then read the new exact bytes after restart. Its private app configuration
also survived the lifecycle. The disposable containers, volumes and network
were removed; this establishes local core Files availability through those
app operations, not behavior during an enterprise deployment.

A separate disposable `0.4.6` to `0.4.29` Nextcloud upgrade-recovery drill
failed deliberately in the final app migration (`occ upgrade` exit 5), after
the late migration table existed while maintenance mode remained on. Its cold,
matched PostgreSQL and HTML checkpoint restored the original instance ID,
file ID and exact WebDAV bytes,
binding configuration, publication-state row, enabled `0.4.6` app and live
administrator binding route. The database and file were both changed after
the checkpoint before restoring, proving the restored state replaced those
changes. The successful run used project `nc-upgrade-recovery-8dcc3a45` and
confirmed its containers, volumes and network were removed. Its checkpoint
archive SHA-256 values were
`62259c6fc67a98fde048f3ce6c3d37c3c41ee115c769a2a2186dad24c95fe8e5`
(PostgreSQL) and
`ef390b54cabb2012eae8e23d54f7f8537552c94307258084a72c7e948fd1aec5`
(HTML); the recovered file SHA-256 was
`366f8cde9b04c3fd66cd6004d0cd95836bb5dd3d6f63c1a41b112e3afc9bc55b`.
This is an isolated same-image rollback exercise, not cross-service recovery,
queued-event replay, a production migration rollback or an RPO/RTO result.
See [the runbook](isolated-nextcloud-upgrade-recovery.md).

The shared development stack's three configured binding roots were later found
in the Nextcloud trashbin, making administrator diagnostics report unavailable
roots. Before recovery, the Nextcloud database and its config/data volume were
archived under ignored `dist/backups/` with mode `0600` and verified SHA-256
digests. Restoring only those three exact trash items preserved file IDs 76,
6718 and 6720. Diagnostics then reported all binding roots available; the
`dev-published` signed manifest and its file 77 content returned HTTP 200.
The shared WeKnora source records still include a paused historical pair and
another missing data source, so this repair does not establish a fresh
cross-system sync or user question flow. Disposable paired fixtures provide
the isolated integration evidence below.

A fresh disposable direct-group OpenLDAP fixture paired and indexed one
synthetic file, then exercised the Files question link as a mapped user.
Alice received a file-scoped mock-model answer with a citation to her
Nextcloud original; Bob and a stale ETag were denied. Before revocation,
Alice's persisted history contained the answer marker and citation URL.
After removing her only group grant, the same pre-revocation JWT received no
direct document, chunk, preview, search or prior-answer content. The second
isolated run observed all denials about seven seconds after the mutation.
These are HTTP/API and synthetic-LDAP observations, not a browser-click test,
real-model quality assessment, enterprise AD permission matrix or revocation
SLA. The [fixture record](synthetic-ldap-compose.md) has the exact checks.

A separate fresh fixture removed only the Nextcloud Engineering folder share
after first recording an authorized answer and original-file citation. Alice
remained in the LDAP Engineering group and WeKnora's Engineering knowledge-base
grant remained active. Her pre-revocation JWT was then denied by Nextcloud
DAV/source authorization and by WeKnora's ask target, direct content, preview,
both search scopes and history display. The first synthetic poll observed the
denials about three seconds after share removal. This isolates the source-side
permission boundary; it is not a production Team Folder ACL or revocation SLA.

A further disposable fixture installed pinned groupfolders 22.0.6 on
Nextcloud 34.0.4 and paired a genuine Team Folder root. Alice and the owner
initially resolved the same file ID; Bob was denied. An advanced `-read` ACL
on only Alice's `acl-note.txt` then denied her original DAV file, signed source
authorization, Files status, ask target, direct content, searches and earlier
answer/citation through the old JWT. The first poll observed the denials about
four seconds after the ACL mutation. Her LDAP group membership, Team Folder
root access and WeKnora Engineering KB grant remained in place; the owner
still read the same original file. This covers one isolated real groupfolders
ACL path, not enterprise AD, production ACL configuration or an SLA.

Source-side diagnostics now separate retained hints awaiting a durable receipt
from those already received but still above the last verified applied watermark.
The admin page shows both counts and oldest hint ages. A fresh, successful
signed applied-status check plus a received-but-unapplied hint created at least
five minutes ago produces a redacted `application_overdue` warning. The age is
measured from hint creation, not receipt, and the counter does not prove an
individual file is parsed or ready. The PHP alert contract and the shared-stack
HTTP smoke passed, including anonymous, machine-token, administrator and
ordinary-user access checks. CI additionally exercises the delivery watermark
transitions and their diagnostics fields.

A separate disposable dual-service restore drill stopped both apps, created
PostgreSQL logical dumps and cold physical PostgreSQL/file-volume archives,
and checked their hashes. After the live file was deleted and two full scans
tombstoned it, the drill restored the older checkpoint while app ingress
remained stopped. It verified the original Nextcloud instance ID, pair
operation, root/file IDs and source ETag, then replayed the persisted
post-checkpoint deletion journal on Nextcloud before starting WeKnora. The
same old JWT still authenticated Alice but could not read stale document,
chunk, preview or search content before or after two reconciliation scans;
the final source row was a tombstone with no visible candidate. Checkpoint and
restoration took 30.159 and 63.946 seconds for this small synthetic fixture.
The script confirmed its own Compose containers, volumes and network were
removed. This is local recovery evidence, not a production RPO/RTO promise,
external vector/Wiki/object restore or a durable production replay ledger.
See [the drill and its limits](isolated-dual-service-restore.md).

An earlier pinned RAG build compiled the WeKnora app with `WITH_ANYDOC=1` and the
`anydoc` Go build tag from source commit
`e5cc3e4491ee10fb85e0c2ad79f1e3329826d02e`; both candidate images carry
the complete patch SHA-256
`a7d638ac36f2e64b5adf998cdd16a811c238de343a5ab8d001262fa8207c3961`.
An isolated, loopback-only dual-service fixture verified the candidate app
image `sha256:c8e8b10b275bc0abfe8446278132e35e4b87d79338d4f55c331cf4f84e42e551`:
AnyDoc was available, the synthetic PDF reached two ready chunks and two
embeddings with its protected text, Alice received a file-scoped answer with
an original Nextcloud `/f/<file_id>` citation, and Bob was denied. Removing
only the source share made Alice's old JWT lose DAV, source, ask, direct,
search and prior citation access on the first poll; LDAP membership, WeKnora
grant and owner's PDF remained. The PDF had complete DocReader text, so this
first run did not trigger the short-text AnyDoc recovery branch. A second
independent fixture forced only its DocReader to rasterize the same PDF: the
primary result had 46 searchable characters, the candidate logged AnyDoc
recovery to 283 characters, and the same indexing, answer, citation and
old-JWT revocation checks passed. This verifies the running fallback branch
under controlled input, not natural PDF truncation or scanned-PDF OCR. The
shared WeKnora LAN service still uses its prior image while the candidate is
reviewed. See [the PDF candidate record](isolated-pdf-candidate.md).

An earlier combined RAG candidate has complete patch SHA-256
`ed055900b1eca78cc15a14021794fb6dc95dafb3e8e5592ce3f865ca1538af03`,
app image `sha256:d052febfcd39d3ea20e136a12a9dc10fda2389d14c118748764318f2d32f22dd`,
and UI image `sha256:699f4ae751e57a838f875093bbd739ee0e1a64418d38bf00a56958b0b28e2b08`.
Both normal and controlled short-primary PDF flows passed again against the
current app. In the controlled flow DocReader supplied 46 searchable
characters and the local AnyDoc recovery supplied 283; indexing, source-scoped
answer, original-file citation, and old-JWT denial after source-share removal
passed. A separate disposable real-browser flow used the current app and UI:
Alice followed the Nextcloud Files sidebar handoff, signed in through LDAP,
asked about the exact published file and received the synthetic marker with
HTTP 200. The ordinary request omitted Agent identifiers. Clicking its
citation opened the original Nextcloud `/f/92` route; the same browser session
read the exact source file over WebDAV with HTTP 200. The browser check passed
again after the fixture began checking that state ports and images exactly
match its owned Compose configuration. All owned Compose projects were removed
after their runs. These are synthetic local checks;
there is no real AD/Team Folder acceptance, natural PDF truncation/OCR proof,
or enterprise answer-quality evidence.

The same current app image also passed the separate local BGE candidate
smoke with its pinned source and patch labels: the built-in embedding returned
512 dimensions, ReRank ranked the matching fictional clause first, one
synthetic knowledge item completed parsing, and pure vector search returned
one hit from one enabled 512-dimensional vector. That owned Docker project
was removed. This checks model wiring and a tiny retrieval example, not
answer quality on approved enterprise questions.

The current RAG app image also passed a fresh owned, loopback-only dual-service
event exercise in `nc-synldap-51dbd280`. Event e1 reached verified applied
watermark 2 on both services. Finalizing an exact source machine-key rotation
made the old event sender pause with `receiver_unauthorized`, without
advancing its receipt. The operator rebind command retained the connection ID,
HMAC key and inbox watermarks, resumed that sender through the scoped
compare-and-swap retry, and returned `already_active` on an identical replay.
Event e2 then reached verified applied watermark 4 on both services. The
fixture's containers, volumes and network were removed. This is one synthetic
rotation and continuation, not production fault-rate or latency acceptance.

Both complete WeKnora patches were regenerated from their pinned fixed and RAG
baselines and passed clean-apply checks. Selected direct HTTP reads,
HybridSearch, Agent read/list and live answer paths acquire renewable read
leases and recheck current source and user grants at output. Enumerated
Asynq/Lite document build workers acquire build leases; same-transaction
chunk, knowledge and version writes are fenced. MCP machine read routes reject
Nextcloud source documents. Focused fixed and RAG Go suites, including
revocation-during-hydration and HTTP-write tests, passed locally.

The coverage marker remains absent, so physical derived-copy GC stays denied.
External vector, graph and object writes, Wiki and other read paths, old task
drain, production coordinated restore, production alert routing and the 10,000-file /
100-GB performance target remain open. The same-directory LDAP fixture is
synthetic; a real AD and production Team Folder permission matrix is not yet available.

## Local cross-system evidence

The first cross-system ingestion and event probes preceded the source-pairing gate. Their legacy synthetic data source is now intentionally denied for new sync and event intake. A second, isolated WeKnora stack paired a new dedicated knowledge base with Nextcloud binding `isolated-pair-8dc8867a58`; both sides reported `active`. The existing local WeKnora test stack was connected to the isolated Nextcloud Compose network. A synthetic embedding model and dedicated knowledge base were created; syncing the `dev-published` datasource imported `department-notes.md`. WeKnora reported successful sync and completed parsing, and the resource carried Nextcloud source provenance. The local WeKnora administrator has no linked AD identity; direct knowledge access and the knowledge-base list returned HTTP 403. This is one ingestion and denial case. It does not validate an authorized AD user's retrieval or a generated answer; no real chat model or enterprise AD test account was used.

After the minimal version-state migration, a repeated local sync succeeded and left one completed, enabled candidate with a matching active source ETag in `nextcloud_source_versions`. On the newly paired source, a real manual sync imported `paired-note.md`, parsing completed and the version row became `published`. Moving that same Nextcloud file ID to `paired-note-renamed.md` and syncing updated the visible knowledge filename and provenance path without changing its candidate ID; moving it back and syncing restored both names. A separate synthetic `version-probe.md` was uploaded, synced, overwritten with different bytes and deleted; each version reached one visible candidate, and the candidate ID changed. After WebDAV deletion and two complete syncs, the source had a durable tombstone and zero visible candidates. The probe file was removed. On the later 0.4.18 image, another isolated synthetic file (file ID 6958) published, was overwritten with a new candidate and ETag, and became a tombstone with zero visible candidates after two deletion scans. Failed parsing and late-task handling still have repository tests rather than a cross-system fault drill. The Nextcloud stack has local accounts and groupfolders 22.0.6 for a synthetic ACL probe, but `user_ldap` is disabled. The existing WeKnora stack connects to enterprise AD while its OpenLDAP fixture is inactive, so neither an authorized AD read nor the full nested-group and production Team folder matrix has been demonstrated here.

The app's local HTTP smoke scripts cover service authentication, pagination, publication withdrawal, source authorization, change hints, root-folder events, runtime binding scope and retention. The change-hint smoke additionally verifies same-name delete/re-upload with a new file ID and a folder move out of and back into the bound scope; content access returned 404 while out of scope and 200 after return. A binding-wide Stop/Resume smoke verifies stopped reads, retained machine binding visibility, stale cursor rejection after resume, audit persistence and preserved per-file withdrawal. The optional Team folder probe can be run after installing the compatible groupfolders app in local Compose: `docker compose exec -T nextcloud chown www-data:www-data /var/www/html/custom_apps`, then `docker compose exec -T -u www-data nextcloud php occ app:install groupfolders --no-interaction`, then `python3 apps/integration_weknora/tests/team_folder_acl_http_smoke.py`. It removes its temporary user, group, folder, binding and identity. An isolated 0.4.6→0.4.22 upgrade smoke passes with valid and invalid old binding configuration. Fixed-baseline Go tests cover the connector and the guard paths in the patch. A 0.4.22 source-pairing HTTP smoke and WeKnora focused Go/SQLite suites also pass locally. The isolated paired-source ingestion and rename probe uses only synthetic data. These checks establish a development environment and selected safety behavior, not the PRD's acceptance matrix.

The 0.4.22 local diagnostics HTTP smoke passed after upgrade: anonymous and machine-token callers were denied, an administrator received only the documented non-sensitive sender fields, and a temporary non-administrator was denied. The event-delivery fault smoke observed a pending hint before receipt, zero pending after a durable receipt, and a paused sender with a pending hint and error code. Its single worker wake-to-receipt sample was 4.27 seconds, not a P95 result. The app archive contains 69 runtime files and passed its install-layout check. The loopback and LAN login URLs returned HTTP 200, and authenticated WebDAV on the LAN address returned 207. This is host-side verification; a second physical LAN client has not been exercised.

The fresh-install smoke extracted the 0.4.22 archive into a new Nextcloud 34.0.4 container backed by unique PostgreSQL and Redis volumes. `occ app:enable` completed on that empty instance; `installed_version` matched the archive, the binding, source-pairing and event-connection tables existed, and a newly generated administrator authenticated to DAV with HTTP 207. The script removed its Compose project and volumes afterward. This validates a clean local package installation, not production upgrade or rollback.

A later disposable run installed the 0.4.29 archive, disabled and re-enabled
the app, and restarted Nextcloud. `app:list` reflected each state; a private
app configuration value survived re-enable and restart. Authenticated DAV
created and read a test file before disable, read and overwrote it while the
app was disabled, and read the updated bytes after restart. Its isolated
volumes were removed. This covers local disable/re-enable and restart behavior,
not an upgrade-failure recovery or production rollback.

The WeKnora event-connection administrator API was exercised against the earlier local synthetic data source. A signed hint received HTTP 202 with a durable receipt watermark; nonce replay and a rotated or revoked key returned HTTP 401. Nextcloud's sender then delivered a temporary WebDAV file event, and both durable receipt watermarks matched. That earlier dispatch probe observed queue acceptance while the applied watermark was still `0`. On the newly paired isolated source, the revised `local-event-pipeline-smoke.py --expect-applied` created a temporary file and then deleted it. Each hint reached WeKnora's real full-source sync queue, and both WeKnora's applied watermark and Nextcloud's separately verified signed applied watermark covered the upsert and later delete. The delete required two complete scans and a dispatcher retry; the test cleaned up its file and revoked both temporary connections. This proves one synthetic event and publication cycle, not the PRD's sustained latency or enterprise load targets. A later isolated rerun covered upsert event 4973 and delete event 4974: both WeKnora and Nextcloud reached the applied watermark, the deleted source version became `tombstone`, and zero active knowledge rows retained a visible source ETag for that file. The first attempt used an unapproved isolated receiver origin and failed at sender configuration; a temporary Compose-only allowlist entry enabled the rerun and was then removed. The 0.4.18 rerun with `--verify-index` then covered upsert 4980 and delete 4981: both applied watermarks advanced, the upsert candidate had a ready chunk and enabled embedding, and the delete ended with a tombstone and zero visible candidates. Its temporary receiver allowlist and event connections were removed.

A separate isolated administrator-withdrawal probe used only temporary file ID 6962. Before withdrawal, its candidate was published and parsed, with one ready chunk and one visible knowledge row. Immediately after withdrawal, the Nextcloud manifest excluded it, its content endpoint returned HTTP 404, and publication recheck returned HTTP 409. Before a full sync, WeKnora still held the published candidate, so immediate denial relies on the live source/read guard rather than completed index cleanup. Two successful manual syncs then left a tombstone, no candidate ID, and zero visible knowledge rows. Republish restored HTTP 200 content; WebDAV deletion and two more syncs returned to a tombstone with zero visible candidates. The temporary file and machine key were removed; the specific trash DAV item returned HTTP 404 after deletion, while a `oc_files_trash` metadata row and publication audit state remain. The old WeKnora knowledge row was soft-deleted, but a ready chunk and enabled embedding remained; derived-index physical cleanup is still incomplete. This manual-sync probe is separate from the event applied-watermark acceptance above. Synthetic share, group-membership and Team ACL revocation tests do not establish the real AD two-account or authorized-retrieval acceptance matrix.

The isolated `local-source-pairing-abort-smoke.py` run used a separate Nextcloud Compose project and a fresh folder, binding and empty knowledge base. A one-shot loopback relay returned HTTP 503 only for that operation's signed commit, leaving a paused WeKnora source and pending Nextcloud intent; an exact abort closed both sides and removed the paused source. The same run verified Nextcloud-only lost-key abort, wrong-operation rejection, idempotent retry and active-pair abort rejection. It removed its fixtures and relay, and the temporary WeKnora origin approval and network attachment were reverted. This is a synthetic fault path, not a production network failure drill.

The isolated active pair then rotated its source credential with operation `024e590d-c574-46c1-830a-b8965de980bb`. Both services reported `finalized`, the old Nextcloud machine key disappeared, and the following WeKnora manual sync succeeded with two items and zero failures. The optional event connection had already been revoked, so this did not test event-connection repair after source-key rotation or a lost response during rotation.

The isolated WeKnora database upgraded from migration 120 to 121 without a dirty state, preserving its GC inventory. After making only two synthetic tombstone jobs due for a local retry, the new worker removed both registered local original objects. Their resource rows changed to `deleted`, their bindings fell from two to zero, and confirmed released bytes totaled 74; `/data/files/10000` shrank by 74 bytes. Both jobs remained `blocked` with derived index items outstanding, so this is a local object deletion proof rather than complete GC or capacity acceptance. Migration 122 / SQLite 41 reopens any prior GC job incorrectly marked complete and retains a derived-index blocker for every historical Nextcloud job. Migration 124 / SQLite 43 invalidates earlier empty-pair proofs, including a possible false proof minted by repair of an old pending pair; a durable operation-ID lineage prevents a deleted pair from regaining virgin status after reconstruction. Existing decommission records stop this upgrade for manual review.

The isolated WeKnora PostgreSQL database then upgraded from 123 to 124 without a dirty state. In a separate disposable Nextcloud Compose project, `local-empty-source-decommission-smoke.py` created a fresh empty folder, binding and dedicated WeKnora knowledge base after that upgrade. The exact pair became active; Nextcloud recorded a stopped decommission intent, rejected a different operation UUID and premature finalize, and WeKnora returned a signed empty-inventory ACK. Both sides reported `acknowledged` before Nextcloud finalized; retries with the same UUID were idempotent. The old machine credential returned HTTP 401, the binding disappeared from the active list, and WeKnora retained a paused source with an `acknowledged` decommission tombstone and historical `active` pair identity. The one-shot relay and empty WebDAV folder were removed. This proves the virgin-source path only; no indexed source was retired or physically cleaned.

The first-stage indexed-source withdrawal was tested against disposable PostgreSQL schemas with migration 125 and SQLite schema 44. Its PostgreSQL tests cover a signed event batch before withdrawal, atomic event-key revocation and dispatch blocking, a denied later signed batch with an unchanged receipt watermark, concurrent connection locks, write fences, append-only observed inventory and a permanent external-index blocker. A fresh, fully disposable Nextcloud 0.4.21 / WeKnora PostgreSQL 125 dual-stack HTTP drill then paired and indexed one synthetic text file (one ready chunk and one enabled embedding). An exact stopped Nextcloud intent led to WeKnora HTTP 202 with `logical_withdrawn=true` and `inventory_complete=false`; the old event key returned 401. Nextcloud retained its stopped binding and source key, had no ACK, and rejected premature finalize with 409. The drill used an isolated mock embedding endpoint after the initial built-in model endpoint was rejected by SSRF policy; its owned containers, volumes and temporary credentials were removed. This is one synthetic first-stage withdrawal, not proof of full historical copy inventory, physical cleanup or enterprise behavior.

PostgreSQL migration 126 and SQLite migration 45 add a local source-version observation ledger. Disposable PostgreSQL 123→126 and SQLite 43→45 upgrades preserved current rows while marking pre-existing histories unknown. A PostgreSQL migration lock blocks writes across the backfill and trigger installation; a concurrency regression checked a writer that committed before the lock and a later writer that appended revision 2. Eight concurrent UPSERTs remained monotonic in both databases, and rejected writes against the indexed-withdrawal fence did not append a revision. Tests also rejected ledger edits and automatic rollback. These observations do not enumerate pre-upgrade candidates or external vector, graph, Wiki, backup and object copies, and they do not change indexed-withdrawal `inventory_complete=false` or authorize GC or an ACK.

PostgreSQL migration 127 and SQLite migration 46 add an inert per-knowledge
read/build lease and retirement fence foundation. Disposable PostgreSQL and
SQLite tests cover acquire-versus-retire and GC-claim-versus-KB-read races;
an active exact GC claim now rejects new KB-wide read leases until release or
expiry. The coverage table is empty after migration, so GC claims are denied
by default. Direct knowledge, chunk, preview and download HTTP handlers now
acquire renewable exact read leases and recheck publication and reader grants
at bounded response writes; handler/router/container and repository tests
passed in isolated Docker. The 2026-09-30 update above adds selected search,
Agent, MCP and build paths. Frequent remote publication checks still need
large-download performance measurement. Wiki, graph, other readers, old task
drain and external index adapters remain uncovered. No coverage marker is
written and this does not authorize physical deletion. The [content lease
design](nextcloud-derived-gc-read-lease.md) tracks the remaining gates.

## Isolated synthetic LDAP acceptance

A separate disposable stack connected Nextcloud `user_ldap` and WeKnora to the
same AD-shaped OpenLDAP directory. Both synthetic users could log in. With
only A in Engineering, A could read the published file and retrieve its
WeKnora knowledge, chunk, preview and search hit; B was denied. Removing A
from Engineering made Nextcloud DAV and signed source authorization deny the
file. A's **old JWT** then received HTTP 403 for direct knowledge, chunk and
preview reads; search returned zero hits. A fresh-login matrix agreed after
directory sync. The protected fixture, containers, volumes, images and network
were removed after the drill. [Reproduction and limits](synthetic-ldap-acceptance.md)
record the exact synthetic scope; this does not establish enterprise AD, nested
groups, production Team ACLs or the full Q&A citation path.

The same isolated stack, rebuilt with `77c2f9f`, indexed two files and ran a
signed event for an update to just one. WeKnora's received, dispatched and
applied watermarks reached event 4; Nextcloud verified the signed applied
watermark 4. In the event sync window, the changed file had one content GET and
the unchanged file had none. Paired-source health returned HTTP 200 to the
administrator, 403 to a non-administrator and 401 anonymously. The source
retained two published, parse-completed candidates. This is one scoped event
case, not the PRD's 10,000-file/100-GB or P95 latency acceptance.

A disposable paired pilot harness now measures bulk sync and event-to-durable
queue admission with explicit small and large fixture controls. Its final
reduced run synchronized two 256-byte files in 0.335 seconds and observed
three independent event-to-queue intervals of 6,073.6, 5,069.7 and 5,266.1
milliseconds; all three reached the applied watermark. Nearest-rank P95 for
these **three** samples was 6,073.6 ms. Five samples saw a 343,703,552-byte
WeKnora process RSS peak. This is a tiny, sequential, synthetic check of the
measurement path, not acceptance of the 10,000-file/100-GB, sustained P95,
upload-latency or connector-memory targets. The two pilot projects and their
private credentials were removed after recording a credential-free report;
see [pilot load runbook](../scripts/ops/PILOT-load.md).

The acceptance scripts now require a fresh, attribute-limited LDAP topology
export before claiming a synthetic primary-group or nested-group case. A
separate loopback-only probe checks GUID/UID mapping conflicts, and a watcher
holds the original JWTs across a synthetic group-revocation drill. Their 16
offline contracts and five WeKnora OpenLDAP network tests passed. A fresh,
disposable dual-service stack then passed the six-field nested-only group
HTTP matrix: Alice had file/source/knowledge/search access through
Alice→Platform→Engineering, while Bob could log in but was denied all four
content checks. Its one synthetic file had a ready chunk and embedding. The
earlier primary-only case failed across services: WeKnora recognized Alice's
`primaryGroupID` membership, but Nextcloud `user_ldap` omitted Alice from
Engineering because raw OpenLDAP did not match its textual SID lookup against
binary `objectSid`. A later fresh, loopback-only fixture added a narrow
OpenLDAP frontend rule that converts only the generated Engineering SID
assertion to binary. Neither application received an extra LDAP member edge.
The six-field primary-group matrix then passed with Alice's sole Engineering
grant through `primaryGroupID=2000` and Bob denied. After Alice's primary group
changed to Domain Users, her original WeKnora JWT lost direct knowledge and
search access by 08:03:18 UTC; separate DAV and signed-source checks also
denied her. WeKnora's directory membership, Nextcloud's group view and a
fresh topology/login matrix confirmed the removal by 08:03:20 UTC. This is a
synthetic AD-like query simulation; real AD primary groups and LDAP-backed
Team-folder ACLs remain unverified. See [the disposable fixture](synthetic-ldap-compose.md).

A fresh direct-group fixture also exercised the complete file-path lifecycle
against candidate image
`sha256:b75fbae3386625c213d7ae49f701c69d34884508aec0951f70928236e7a24056`.
The same Nextcloud file ID 242 was created (event 21), renamed (22), moved out
of the binding (23), moved back (24), moved out and back before delivery
(final event 26), sent to trash (27), and restored (28). Both services'
applied watermarks reached every target event. Rename updated the indexed
path/name without replacing its candidate; each confirmed withdrawal became
a tombstone with zero visible candidates after the connector's second full
scan. Move-back and restore created new candidates, and rapid movement ended
with the currently authorized source published. Source bytes and Alice/Bob
allow/deny checks passed after restoration. The isolated project's containers,
volumes, network, and private scratch directory were removed. The
[reproducible probe](../scripts/ops/README.md) covers a synthetic direct share,
not production Team Folder or enterprise AD behavior.

A separate owned two-binding synthetic fixture used the source-65 backend
image `sha256:eb558b7ab6d8ae53d3165263bea577ad4d6d12c62dcd75c62aa394c473d4625b`
to move a nested directory with three descendants (file IDs 167, 168 and
169) from A to B and back. Each source binding produced three tombstones;
each target binding published three new candidates while file IDs stayed
stable. The corresponding A/B event watermarks reached 12 initially, then
13/14 and 15/16 across the two moves. Alice's current source and direct
knowledge were readable and her ask returned 200; Bob's direct read and ask
were denied. Alice's unauthorized DAV DELETE returned 403 without any new
delete or subtree-delete hint, and the original file and candidate remained.
The fixture's containers, volumes and network were removed. This verifies the
synthetic two-binding event path; production ACL and load remain open.

## Failed-candidate recovery

The two pinned WeKnora patches now store a per-file failed-candidate retry
intent. A five-second dispatcher scans active paired Nextcloud sources, using
keyset pages rather than a fixed first page, and claims one exact failed
generation for a bounded 24-hour automatic window. Retry delay grows from one
minute with stable jitter. Each task carries the source ETag, failed candidate,
pair operation and epoch, source config hash and one-use lease token. The worker
rechecks these selectors before reading source bytes and before Stage; a stale
queued task cannot replace a newer candidate. The administrator-only API can
inspect the exact file's retry state and restart an exhausted window with a
compare-and-swap request. No parser error text or source credential is exposed
through that status response.

A retry of an unchanged ETag downloads only the failed file after a complete
metadata preflight. A separate event retry still refreshes the full metadata
manifest when its changes page has become empty; this advances the source's
reconcile timestamp so a completed event can reach the verified applied
watermark without re-downloading unchanged file bodies. Fixed and RAG focused
Go tests cover the retry claim, same-ETag staging, stale task denial and event
timestamp transition. Both pinned patches apply to clean source archives and
the SQLite migration-47 upgrade tests pass. At that stage the RAG baseline was
pinned to
`b8a34e0bae8fcf0d3c8273bba2e56414abed41e2`; its evaluation-cost Go tests
and frontend typecheck also pass. The isolated candidate that established the
automatic retry behavior before the administrator list was added has app image
`sha256:35dbcdf20a01fe22998da6dbd141bc1375722af1c3f8b3aaa5dbeb1ce37dd727`
and UI image
`sha256:2c093023f7522f92614f921af4bc12d6f44760192e856a170344308fd03a1a25`.
Both carry the pinned source and full patch SHA-256 labels; neither replaced
the shared WeKnora service.

The earlier loopback-only `--phase full` fault drill used this app image and the
packaged 0.4.29 Nextcloud app. After a new, same-file V2 WebDAV overwrite
created outbox upsert event 1, its first candidate failed while only the
fixture's embedding service was stopped. The V1 recovery copy remained stored
but its old target, direct document and answer/citation were inaccessible.
The durable retry job recorded one automatic attempt and a sync-log ID; after
the model service returned, a distinct V2 candidate published with a current
ETag, one visible copy, ready chunk and embedding. Alice's file-scoped answer
cited the original Nextcloud file. Bob's target and direct document returned
403, and his selected search and answer stream exposed no content or citation.
The old V1 target, document, selected search and stream remained denied after
V2 publication. Both services' verified applied watermarks reached the new
event. The fixture's containers, volumes, network and private scratch data
were removed. This synthetic direct-share run does not verify enterprise AD,
production Team Folder ACLs or a performance SLA; reproduce it with the
[isolated fault probe](../scripts/ops/README.md).

The administrator failed-candidate drawer now lists only current failed
staging files for an active paired source, one tenant and one knowledge base.
Its server page is capped at 50 rows and returns only numeric file IDs and
static retry state codes. Selecting a file fetches the exact candidate and
ETag; the restart request uses those selectors in a compare-and-swap. A `409`
clears the stale selection and reloads current status. Ordinary users cannot
call the list API. PostgreSQL with `en-x-icu` exposed a bug in the original
prefix upper bound: `~` sorted before numeric file IDs. The query now uses an
exact prefix check and database-consistent keyset cursor. A dedicated
PostgreSQL collation test and SQLite malformed-row pagination test pass. The
earlier retry candidate's fixed and RAG patch hashes were
`1d60b7db90ae67aefcf7f19f77f11291918dfd071a07fb58b87bdc5bac8bc78e`
and `2883e0d64518c3bb0da04d74136545677dbfc29f71d907d41065bc80e87901d5`.
The RAG source is pinned to
`77c97fd72f26e84435503d24eeed88cb5dfe1f01`, which also includes the
evaluation selected-model guard. The isolated backend image is
`sha256:1a6d27d3a35f6056552449aadb1e14c34840fb7dd9afc9e4f13eb6dd5ebc27dc`
and the UI image is
`sha256:861693296338a30560d3353916ceb9bc3b6a428ef8dc77bdb8bfadf7649ac951`.
Both labels contain that source revision and the full patch hash; neither is
used by the shared WeKnora stack. A fresh loopback-only `--phase full` run with
this backend image passed: V2 produced a new outbox event and one failed file
(`file_id=92`); the administrator list returned exactly that file and its
active operation ID, while Alice and Bob each received HTTP 403. After one
durable automatic retry, a distinct V2 candidate published, the list became
empty, and its desired and visible ETags matched the new source ETag. One
visible copy had at least one ready chunk and embedding containing the V2
marker. Alice's answer cited the original Nextcloud file. Bob's target and
direct document returned 403; selected search and answer stream exposed no
V2 content. The old V1 target returned 404, direct document 403, and selected
search and stream exposed no V1 content both before and after V2 publication.
The receiver's durable receipt and both sides' applied watermarks reached
event 1. These watermarks prove event processing, while the separate current
ETag, ready chunk, embedding, answer and citation checks prove actual
publication. The owned project, volumes and network
were removed. The first 77-image attempt timed out on an isolated PostgreSQL
read while other Docker builds and fixtures were active; the serial rerun
passed all assertions and removed its owned resources. A separate real
Chromium run used an isolated failed V2 candidate with the current editor:
the administrator list showed file ID 92, the exact drawer showed attempts
and next attempt, an injected `409` reloaded both list and exact status, and
the real retry POST returned `202`. A file-count probe returning `403` did
not hide the knowledge-base editor and displayed a disabled storage selector.
The later backend and frontend guard fix prevents that disabled selector from
silently rebinding a legacy knowledge base; its focused tests cover files,
deleting rows and count errors. Alice saw no settings menu and received `403`
from the list API. The browser run used separate candidate images and cleaned
its own fixture.
All eight GitHub checks on the earlier `30de4d5` pinned-source commit passed, including
both patch applications, new frontend retry tests, PostgreSQL collation tests,
direct upgrade and local smoke.

The WeKnora-facing OpenAPI contract now records 27 Nextcloud integration
methods across 22 paths, including event receipt/status, pairing, failed
candidates, source authorization and file-scoped ask. A standard-library
contract checker runs after each pinned patch is applied in CI. On both clean
source archives it verified all 27 routes, 26 success response shapes and 11
Go JSON structures. Deliberate route-method, cursor-key and ETag-tag drift
were rejected by the checker. This freezes the implemented interface shape;
the live enterprise network and identity integration still need acceptance.

## 2026-10-01 candidate stabilization

The preceding fixed c6 and RAG77 full patches had SHA-256 values
`5611d1c3b562410a5314f1dd82312fb485816d740f5ac603fa25253b619263a8`
and `045ff1a17b244c90d1263f3e458fabfa0e142268e7b5df7f1f70cbae91a72852`.
Both apply back to their pinned upstream commits. The combined Go tests for
read-lease cancellation, knowledge-base settings, and event-key rotation pass
on RAG77; the 27-route OpenAPI checker passes on both applied patches.
The isolated RAG77 candidate backend image is
`sha256:590d91c9d9e963c49c68cbd3b3dd93731d53df0e1eb302858fa1c28d2ff788e3`;
its UI image is
`sha256:710bbe26743b809bad18302f1c1722363e781e6a3a257610979a6f86d0b91a21`.
Both carry the exact RAG77 source revision and full patch hash as OCI labels;
the shared WeKnora service still uses its earlier image. An immediately
preceding candidate with identical runtime source, before a Go test-only
PostgreSQL assertion correction, passed a fresh loopback-only `--phase full`
probe. File ID 92's V2 parse failed while its V1 recovery bytes stayed
stored; the administrator list showed one failed candidate, and Alice and Bob
received `403` from that list. A durable retry with a new sync log published
a distinct V2 candidate with ready chunk and embedding, and the list became
empty. Alice's file-scoped answer cited the original Nextcloud file. Bob's
direct/ask routes denied access, and selected search and streamed answer
suppressed its content. The old V1 direct/ask routes and answer remained
denied. Both verified applied watermarks reached event 1. The probe removed
its owned containers, volumes and network.
A separate real Chromium run with that preceding backend and UI runtime
opened the failed-file drawer even when the editor's file-count probe
returned `403`.
It showed file ID 92 and its exact manual-retry status, reloaded both list and
exact status after an injected `409`, and received `202` from the real retry
POST. Alice had no settings menu and the list API returned `403`. The browser
fixture also removed its owned containers, volumes and network.
The same backend runtime, before a test-only PostgreSQL rotation assertion
was corrected, passed `--cross-bindings-only` with three nested files. A
forbidden DAV DELETE returned `403` without a deletion hint or lost
candidate. Moving the subtree A→B→A produced three source tombstones and
three distinct destination candidates on each leg; event receipt and applied
watermarks reached 12–16. Alice retained direct and file-scoped answer
access, while Bob remained denied. This owned fixture was removed. The
subsequent full-patch hash change touches only that Go test assertion.
The corrected PostgreSQL event-connection lifecycle test passed against a
separate PostgreSQL 16 container; its container and network were removed.

A canceled read request now returns its parent context error even when a
concurrent lease renewal observes a closed database transaction. The original
cancel/close test and a deterministic renewal-failure test each passed 100
repetitions on both source baselines. A legacy knowledge base can no longer
silently adopt the current default storage backend when its file-count probe
fails. Server-side storage binding and Embedding-model changes check both
ordinary and deleting knowledge rows and reject a failed count. Focused Go
tests on both baselines, eight frontend tests, and frontend type checking pass.

The event receiver now keeps the immediately preceding HMAC key for two
minutes after rotation. A second rotation removes the oldest key, while
revoke and source withdrawal still disable the connection immediately. Event,
connection-status and file-status signature checks use the receiver clock
after taking the connection lock; the file-status response key is rechecked
at signing. Focused Go tests on both baselines cover the old/new interval,
expiry, second rotation, replay and scope, and the event smoke script now
expects the same behavior. This two-minute event-key window is distinct from
the source machine-key rotation protocol's maximum 24-hour overlap.

## 2026-10-01 isolated follow-up candidate

Nextcloud app 0.4.30 adds a narrow V1 publication ACL gate. A binding can use
its publisher's local home directory or the verified Team Folders 22.0.6 base
permission mount with advanced ACL disabled. Creation and resume check the
publication tree; manifest scans and individual content, authorization and
Files status reads reject a different nested mount. When an active Team Folder
enables advanced ACL, an ordinary source read fails closed and persistently
stops the binding with an audit entry, a new publication epoch and a
reconciliation hint when the database and outbox are available. An
administrator must disable that ACL mode and explicitly resume. A fresh,
owned Nextcloud-only Team Folder smoke checked normal child-file publication,
the 409 creation/update/resume denials, the exact stop audit and epoch, denied
content and authorization, diagnostics, and recovery; its resources were
removed. A separate synthetic LDAP/WeKnora drill had already verified an
indexed uniform Team Folder and the same stop path. A later disposable
Nextcloud 34.0.4 fixture enabled `files_external` and mounted local storage
inside both an active Team Folder binding and an unbound local folder.
`files:mount:list` and DAV PROPFIND confirmed the nested files were readable.
New binding creation returned `409 unsupported_source_acl`; the active
binding's manifest failed closed and persistently stopped it with epoch 1,
an audit entry and diagnostics. Resume returned 409 until the mount was
removed; then resume reached epoch 2 and the manifest returned 200. The
fixture's containers, volumes and temporary files were removed. This policy
does not establish enterprise AD or arbitrary external-storage ACL parity; see
[synthetic scope](synthetic-ldap-compose.md). The 0.4.30 runtime package
contains 75 files and has SHA-256
`8e8a7355f6a127f4ecf7620d4ffc18e98c64685672306d15c778b1f77f69ec8b`.

The fixed c6 full patch now has SHA-256
`4f976461f099da9ba6a53ce637b1ae7f285f46b0177ad95fc8bcee6a23b76792`;
the RAG77 full patch has SHA-256
`cc6325eac6cb5ef128e32a803cf5610f2a627ab5a6c7119849efda055744214c`.
PostgreSQL migration 129 and SQLite migration 48 admit append-only source
revision identities into the private indexed-withdrawal inventory. A hard
deleted old knowledge row remains represented by its historical candidate
revision. Fixed and RAG PostgreSQL focused tests, SQLite migration tests,
exact patch reverse checks and both 27-route OpenAPI checks passed. This
extends observed evidence only: pre-ledger and external copies are unknown,
`inventory_complete` remains false, no Nextcloud retirement ACK is sent and
derived-index physical GC stays disabled.

The RAG77 candidate images were built with AnyDoc and the exact patch hash in
their OCI labels: backend
`sha256:c2b78f72f0c352949416b17f649b50271555c4eec2ecbad670264e783bec868c`
and UI
`sha256:770e0ed1048bbb7d71f848d481e68110de158a56493a4edfd6fbaab3eb884eec`.
They are separate from the earlier images still used by the shared WeKnora
stack. The previous end-to-end failed-candidate and two-binding drills used
the earlier runtime; this follow-up changed inventory and Nextcloud ACL code,
so those earlier drills are not claimed as tests of the final image labels.
The final backend image then passed an owned dual-service physical backup and
restore drill. Nine checkpoint files included the fixture's state, Compose
configuration and WeKnora JWT/AES keys; their checksums and private modes were
verified and those files were restored before container creation. A stale
published file remained denied before and after two complete reconciliations;
the source ended as `tombstone` with zero visible candidates. The observed
checkpoint and restore durations were 31.828 and 64.234 seconds. The fixture
and volumes were removed. This is local synthetic recovery evidence, not the
PRD's production RPO/RTO, external-backend recovery or complete historical
publication ledger; see [restore drill](isolated-dual-service-restore.md).

A further fixed c6 / RAG77 patch revision has SHA-256
`dd7e09a9df65085d31e99737220dd3a60ff8b2dabfe823f6cc75fabace1b73ee`
and `f627d20af4edd053163d5f8594f8e75fc55393395d2135701073a1a75df0d475`.
It adds a read lease and output-boundary source check to global
`/knowledge/search`; focused tests on both baselines show a live lease blocks
a GC claim after retirement and a revoked result's title is not sent. Derived
GC remains disabled because other paths lack coverage. New manual and scheduled
Nextcloud syncs also derive a stable queue task ID from the persisted log ID.
If enqueue outcome is uncertain, a static redacted status and task identity
are available to administrators while the running admission slot stays
occupied. Two queue fault-injection cases and a repeated cron tick passed on
both baselines. There is still no automatic recovery for an uncertain enqueue,
because a missing Asynq task entry is not proof that a task never ran. Older
ambiguous logs predate stable task IDs and require manual investigation.
The new RAG77 AnyDoc backend image is
`sha256:9880ab2b915f0f2ac6a40650370f7698945a540043afed4602cb6823edb6f3ab`;
the UI image is
`sha256:f7d1671dcd6c1010f59b5072795cae380327fe4046beadabd643755ecdb7145a`.
Both carry the pinned source and new patch SHA-256 labels. An owned disposable
Nextcloud/WeKnora/LDAP Compose fixture booted them, both HTTP health endpoints
returned 200, and its containers, volumes and network were removed. This is a
fresh-image startup check, not a repeated answer/revocation or restore drill.

## Consistency and release boundary

The pinned fixed and RAG WeKnora patches now fence PostgreSQL pgvector
`Save` and `BatchSave` with the current Nextcloud build lease and source
candidate in the same transaction as each embedding write. A candidate's
early parse task waits for its committed Stage record before it enters the
handler; a retired fence or absent Stage fails closed. Two-connection
PostgreSQL tests cover write versus retirement, Stage versus Tombstone,
stale Publish, late exact-ID GC inventory, a failed transaction and a Lite
worker that starts before Stage. Both patches apply to their pinned source
commits and passed the focused repository, pgvector and router suites.
The RAG patch was also built into separate backend and frontend Docker images;
the backend image `sha256:b75fbae3386625c213d7ae49f701c69d34884508aec0951f70928236e7a24056`
passed a fresh, isolated six-field synthetic primary-group matrix and
old-JWT revocation drill on 2026-09-30. The drill's containers and volumes
were removed; [the exact result](synthetic-ldap-compose.md) records its scope.
The exact-vector delete helper remains dormant and the `derived_index` GC
blocker remains active because external writers and all readers are not yet
covered. See [the lease gate](nextcloud-derived-gc-read-lease.md).

The first manifest page scans the bound tree and stores a sorted list for ten minutes. Later pages read that list and reject a changed source or publication revision. A final publication-revision check now also covers a withdrawal that commits after page selection or snapshot persistence. A 207-file smoke covered pagination, a concurrent write, withdrawal between pages without an ETag change, and conditional content reads. This is not a transactional source snapshot; a change during traversal can invalidate a page, and large lists still need performance testing. The connector treats events as hints, recovers an expired cursor by full reconciliation and confirms absence twice. It cannot substitute for a durable publication state machine or a tested backup/recovery protocol.

The Nextcloud app was upgraded in the shared local stack to 0.4.31 and Apache
restarted after the bind-mounted PHP change. The local API smoke, LAN login
HTTP 200, authenticated LAN DAV PROPFIND 207, and WeKnora LAN root HTTP 200
passed. The reproducible 0.4.31 app archive contains 75 runtime files and has
SHA-256 `110e4b9814c808f2f39f9121f77588ac96f9b114a4d36f913770308226b75876`.

## 2026-10-01 follow-up

Nextcloud 0.4.32 accepts a signed, allowlisted
`failure_code=no_retrievable_content` for a failed current file. The Files
sidebar shows a specific no-content message and does not create a question
link. The shared local stack upgraded successfully; maintenance mode is off,
LAN login returned HTTP 200, authenticated LAN DAV returned 207, and the
employee file-status HTTP smoke and PHP signature contract passed. Its
reproducible runtime archive has 75 files and SHA-256
`df9fb1c261453197183aa2ffbf57b5936ea2e494f3deca45e304ff6e660e5f07`.

The fixed c6 full patch SHA-256 is
`5da6f751906b938db68c014607f2adc22feb151f6d012f527c1400f10608f6e7`;
the RAG77 patch SHA-256 is
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`.
Both applied to clean pinned source archives and passed the 27-route,
26-response-shape, 11-Go-struct OpenAPI contract. Focused Docker Go tests
passed on both baselines for zero-text publication/status/ask-target denial,
completed-sync event fast wake, and knowledge-list/raw-search read leases
with revocation and GC races. A local fixed-baseline backend image built as
`sha256:1206e84f0e883492735bfe6efa9690adb0ad8ee6f7cb7d6f15d045d603bc5b91`.
It was built before the test-fixture-only `sync_logs` schema correction in
`140c8e1`; the runtime source is otherwise the same. On 2026-10-01, a fresh
isolated direct-group LDAP fixture (`nc-synldap-72933917`) started this image,
indexed two chunks and embeddings, and passed the six-field Alice/Bob matrix:
Alice could log in and access DAV, signed source, knowledge and search; Bob
could log in but was denied the four protected content paths. Its owned
containers, volumes and network were destroyed. This is synthetic direct-group
evidence, not real AD or the latest RAG runtime. The latest RAG AnyDoc,
browser-skill and Go backend stages compiled, but two final image attempts
ended on different Debian mirror single-package HTTP 502 responses. The shared
WeKnora stack still uses an older image.

A separate candidate used the newer WeKnora RAG head
`3ad3b31f2b3e6409c6a9c196bbab70e2eac6c666` with the same RAG patch SHA
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`.
Using a different Debian mirror, its AnyDoc backend and UI built as
`sha256:2ec7e6b31463e6764130973cc1d636d2f65c83268376943cbae59713b7156f9e`
and `sha256:5d9272403683d4fafc0d48c07543ba10c9918041d4d3ece922c4952fb9a21075`.
Both images carry that exact source and patch in their OCI labels. A fresh
isolated direct-group LDAP fixture (`nc-synldap-bc78b1d4`) started backend and
UI healthy; UI returned HTTP 200, bootstrap indexed two chunks and embeddings,
and the Alice/Bob six-field matrix passed with the same allow/deny pattern.
Two further owned fixtures tested the same backend: `nc-synldap-f089ff7b`
confirmed Alice's sole Engineering grant through her AD-shaped primary group,
then changed that group to Domain Users; her existing WeKnora login could no
longer read DAV, signed source, knowledge, direct content or search, and a new
directory snapshot removed the membership. The first denial was observed after
seven polls, 25.5 seconds after the baseline checkpoint in this fixture.
`nc-synldap-521230a5` verified a sole nested-group edge and passed the same
Alice/Bob six-field allow/deny matrix. Each fixture indexed two chunks and
embeddings, and all owned containers, volumes and networks were destroyed.
This checks the patch against a newer RAG source head, not a runtime image
built from the repository's pinned `77c97fd7` baseline; it does not prove PDF recovery,
enterprise AD permissions or the PRD load target.

The pilot harness now has a `post-accept` event pattern that times an update
sent just after a previous event enters the queue. Its offline schedule test
passed, but no new Docker load run or PRD 10,000-file/100-GB/P95 acceptance
has been made. The safe dispatch fast wake preserves same-source single flight
and publication proof; it is not a measured latency guarantee. Derived-row
physical GC remains disabled because other read and external-write paths lack
complete coverage. Enterprise AD and production Team Folder acceptance still
await a real target environment.

Use synthetic data only in this local stack. Enterprise documents require the remaining publication, identity, permission, security and operational acceptance work above.
