# WeKnora RAG baseline adaptation

`integration/weknora.patch` remains the reproducible patch for WeKnora commit
`c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` used by the fixed-baseline
Docker integration build. `integration/weknora-rag-77c97fd7.patch` is a complete,
separate patch for the RAG branch at commit
`77c97fd72f26e84435503d24eeed88cb5dfe1f01`. Apply **one** patch to its
matching base; the RAG patch is not a delta to the fixed-baseline patch.
The fixed-baseline patch SHA-256 is
`b1652548af695d62560a092e1d5520382faef36094a9d0b35499aebe3c0fb267`.

From a clean WeKnora worktree at that exact RAG commit:

```sh
git apply --check /path/to/nextcloud/integration/weknora-rag-77c97fd7.patch
git apply /path/to/nextcloud/integration/weknora-rag-77c97fd7.patch
```

The RAG patch SHA-256 is
`79b4ae7574ab88274f2931d1a46eb89728dbd96eb4a5791604a71ce14287b9b3`.
The build script and CI reject a different patch hash. On 2026-10-02 both
current baseline patches applied cleanly to fresh source archives and passed
focused Go package tests. The RAG baseline retains the evaluated RAG runtime
changes and is pinned to the tested source commit rather than a moving branch
head. For the preceding parser-overlap patches, failed-file editor probe tests,
frontend type checks and focused Docker Go tests passed for source publication,
direct and RAG search
authorization, HTTP read leases, worker build admission, MCP denial, and the
file-scoped question target. The fixed and RAG service and handler suites
passed. The RAG frontend typecheck, citation/export tests and production build
passed, and CI rebuilt that patch at `5664b6b`.
Those preceding patched baselines also passed disposable pgvector PostgreSQL tests for
BatchSave versus source retirement, stale Publish after a new Stage, Stage
versus Tombstone, queued parser admission before Stage, crash/retry of exact
vector-ID receipts, and exact claimed deletion with retrieval checks. These
checks do not enable global derived GC.

The inherited connector rejects empty text candidates as published answers,
returns a signed static no-content status, polls queued event publication
proof every five seconds during its first minute, and fences knowledge-list and
raw-search output with read leases and current authorization. Focused Go tests
in the repository and both handler packages passed for both preceding pinned
patches; the 27-route OpenAPI checker passed after applying each to a clean
source archive. The full suite and an exact pinned-`77c97fd7` RAG runtime
image have not yet been verified for the current patch revision.

The current receiver also records each signed event's ETag and relative path.
File ACK requires the exact version and publication proof; broad and excluded
targets require two complete scans of the same target. Focused SQLite and
isolated PostgreSQL event/pairing/withdrawal/rebind tests passed on both pinned
baselines. A reduced newer-head event-only image, made by layering the Go
binary and migrations onto an earlier AnyDoc runtime, completed a disposable
three-sample queue pilot; see the [event queue record](isolated-event-queue-pilot.md).
This does not substitute for the pinned full RAG image build or sustained
load acceptance.

The dispatcher now admits a newer signed upsert of the same file while its
previous parser remains pending, processing or finalizing, after the earlier
source scan has completed. The newer hint must carry a changed ETag and an
importable binding-relative path. The source cursor must cover the old receipt,
and only one unfinished parser generation or live build lease may already exist
for that file. A third generation waits until an older row is terminal and its
build lease is released. Staging the newer candidate retires the older fence;
the applied watermark stays unchanged until a newer complete source cursor and
exact publication proof pass. Both pinned source trees passed the focused
overlap race and Nextcloud repository, service and handler tests in the local
Go test image. A full pinned app/UI build with the preceding RAG patch
`84afeffc37040b2a85c2c795c05600817ea738b7862decc22f0310b4a0b23a2f`
was exercised in an isolated synthetic LDAP fixture, then installed on the
shared LAN stack.
Its image IDs and the one-file signed V1/V2 overlap results are recorded in
[V1 status](v1-status.md) and the
[redacted acceptance report](evidence/rag77-parser-overlap-2026-10-02.json).
The stale V1 was denied by guarded retrieval, but retained a chunk and
embedding at the final database read. Sustained load and physical derived-index
collection remain unverified.

The current patch adds recovery for uncertain manual and scheduled Nextcloud
sync enqueue. It persists a versioned exact queue intent and worker-start
claim; after five minutes, only a definitively absent exact task with no
worker claim releases its admission slot. Redis recovery is opt-in after all
workers are upgraded, while Lite recovers its single-process queue
automatically. Legacy logs and started attempts remain for operator review.
It also treats an ETag-raced 412, source 429/5xx and interrupted content reads
as retryable, so a failed event scan does not turn an otherwise active source
into an `error` that blocks following hints. A narrow status/cursor update
guards normal and pre-stream sync finalization against a concurrent
administrator pause or resume. Agent graph queries now retain a KB read lease
and recheck every displayed chunk against current source and user scope.
Both pinned patches applied cleanly and passed focused Nextcloud repository,
service, connector, handler and graph-tool Go tests. This patch has not yet
been built into full app/UI images or installed on the shared stack; the
shared image and schema remain at the preceding patch and `130/false`.

The patch also refreshes retired chunk image references on every
due GC retry. A newly malformed `image_info` blocks the local provider
delete callback until repaired; a newly valid image URL is durably
inventoried without duplicating prior items. Focused SQLite and isolated
PostgreSQL GC tests passed on both baselines. This does not enable
derived-index physical deletion or close the external-writer race between
inventory and claim.

For the existing local `weknora-ldap-local` stack, run:

```sh
./scripts/build-weknora-rag.sh
./scripts/use-weknora-rag.sh
```

The first command archives the pinned source commit, verifies and applies the
patch, then builds both images with the patched source Dockerfiles. The backend
build passes `WITH_ANYDOC=1`: that Dockerfile builds the Rust parser library
and compiles Go with `GO_BUILD_TAGS=anydoc`. This keeps the local PDF text
recovery path linked into the backend. There is no local-image overlay path.
To build a candidate without replacing the tags used by the shared stack:

```sh
WEKNORA_RAG_TAG_SUFFIX=nextcloud-rag-77-anydoc ./scripts/build-weknora-rag.sh
```

This creates `weknora-ldap-app:nextcloud-rag-77-anydoc` and
`weknora-ldap-ui:nextcloud-rag-77-anydoc`. The build downloads browser-skill,
Rust, DuckDB and other dependencies and requires substantial time, disk and
network access. The script requires at least 16 GiB of host free space on the
source workspace filesystem and, when present, the default Docker Desktop
disk-image filesystem before it starts. Inspect free space again while building;
the preflight cannot predict total cache growth. An operator with verified
capacity or warm build caches can explicitly set `WEKNORA_RAG_MIN_FREE_GIB` to
an integer from 1 to 1024 to change this threshold. Some upstream Dockerfile
dependencies float, so this is a development build rather than a bit-for-bit
reproducible production image.
For hosts where the default package endpoints fail, the build script accepts
optional `WEKNORA_RAG_APT_MIRROR`, `WEKNORA_RAG_GOPROXY`,
`WEKNORA_RAG_NPM_REGISTRY`,
`WEKNORA_RAG_RUSTUP_DIST_SERVER`, `WEKNORA_RAG_RUSTUP_UPDATE_ROOT`, and
`WEKNORA_RAG_CARGO_REGISTRY_MIRROR` environment variables. They are passed as
the pinned Dockerfile's build arguments; use trusted, credential-free mirror
URLs because build arguments can be retained in image metadata.
The second command above saves a PostgreSQL dump in ignored `dist/backups/`
before switching the shared local app and frontend; it consumes only the
default `nextcloud-rag` tags. The images carry the pinned source commit and
full patch SHA-256 labels, and the backend reports their source-plus-patch
revision.
Before making the dump or recreating containers, the upgrade script requires
healthy WeKnora and Nextcloud database containers, zero active WeKnora event
connections, and zero installed Nextcloud event sender rows. A paused sender
still blocks the upgrade. Drain and revoke connections through the application
workflow, and review the [receiver upgrade order](nextcloud-event-receiver.md#upgrade-order-for-the-relative-path-field)
before retrying. This preflight is a snapshot; keep event pairing and writes
stopped during the upgrade.

Before switching the shared stack, exercise the candidate app image in a
disposable WeKnora fixture with its docreader service:

1. As a tenant viewer, query `GET /api/v1/system/parser-engines` and confirm
   the `anydoc` entry reports `Available: true` and docreader is connected.
2. Ingest a known born-digital PDF for which DocReader returns fewer than 120
   searchable characters while the local extractor returns richer text. Check
   that parsing records `pdf_text_recovered=anydoc`, the expected PDF sentence
   appears in the indexed content, and a permitted question cites the original
   Nextcloud PDF file.
3. Ingest a scanned PDF and check that DocReader OCR still supplies text and
   citations. Revoke that file's source access and check that the prior PDF
   text and original-file citation are no longer returned.

The recovery path runs only for short DocReader text, keeps DocReader's image
references, and does not replace its OCR path. A PDF whose primary result is
already complete will not set `pdf_text_recovered`.
Use synthetic local data. A disposable HTTP/API handoff, answer, citation and
revocation drill passed with mock models; see [its record](synthetic-ldap-compose.md).
Live enterprise AD, Team Folder ACLs, browser-click acceptance and production
load remain open until separately verified; see [V1 status](v1-status.md).

On 2026-10-01, the preceding patch SHA-256
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`
also applied to newer RAG source head
`3ad3b31f2b3e6409c6a9c196bbab70e2eac6c666`. An isolated build using an
alternate Debian mirror produced the AnyDoc backend image
`sha256:2ec7e6b31463e6764130973cc1d636d2f65c83268376943cbae59713b7156f9e`
and UI image
`sha256:5d9272403683d4fafc0d48c07543ba10c9918041d4d3ece922c4952fb9a21075`.
The OCI labels on both images identify that source head and this exact patch.
A new direct-group synthetic LDAP stack started both images healthy, served
the UI with HTTP 200, indexed two chunks and embeddings, and passed the
six-field Alice/Bob permission matrix. Additional fresh primary-group and
nested-group fixtures passed. In the primary case, Alice's only grant came
from `primaryGroupID`; changing it to Domain Users caused her existing login
to lose DAV, signed-source, knowledge, direct-content and search access, and
the directory membership disappeared. The nested case verified the sole
nested group edge and the same allow/deny matrix. All three owned fixtures
were destroyed. A fourth disposable fixture installed Team Folders 22.0.6,
bound its root and checked a file-scoped answer with its original Files
citation. A file-level `-read` ACL for Alice then denied DAV, signed source,
Files status, ask-target, knowledge, direct content and search; historical
content was redacted. All owned resources were removed. This compatibility
run does not change the pinned baseline; PDF recovery is checked separately
below, while real AD and production performance remain unaccepted.

Two more owned fixtures used that exact newer-head candidate for PDF checks.
The natural born-digital PDF path indexed two ready chunks and embeddings,
answered a file-scoped question with an original Nextcloud Files citation,
and hid source, answer, search and historical citation from Alice's old JWT
after source-share revocation. Because DocReader returned complete text, that
run did not enter the short-text recovery branch. In a separate controlled
fixture, `DOCREADER_PDF_FORCE_SCANNED=1` made the primary output 46 characters;
AnyDoc recovered 283 characters and the same protected text, citation and
revocation checks passed. Both synthetic fixtures were destroyed. Mock
embedding and chat services were used; scanned-PDF OCR, real AD and enterprise
answer quality remain unaccepted.
