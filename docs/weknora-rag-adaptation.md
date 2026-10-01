# WeKnora RAG baseline adaptation

`integration/weknora.patch` remains the reproducible patch for WeKnora commit
`c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` used by the fixed-baseline
Docker integration build. `integration/weknora-rag-77c97fd7.patch` is a complete,
separate patch for the RAG branch at commit
`77c97fd72f26e84435503d24eeed88cb5dfe1f01`. Apply **one** patch to its
matching base; the RAG patch is not a delta to the fixed-baseline patch.
The fixed-baseline patch SHA-256 is
`5da6f751906b938db68c014607f2adc22feb151f6d012f527c1400f10608f6e7`.

From a clean WeKnora worktree at that exact RAG commit:

```sh
git apply --check /path/to/nextcloud/integration/weknora-rag-77c97fd7.patch
git apply /path/to/nextcloud/integration/weknora-rag-77c97fd7.patch
```

The RAG patch SHA-256 is
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`.
The build script and CI reject a different patch hash. On 2026-10-01 both
baseline patches applied cleanly to fresh source archives. The failed-file
editor probe tests and frontend type checks passed for both patches. The RAG baseline
retains the evaluated RAG runtime changes and is pinned to the tested source
commit rather than a moving branch head.
Focused Docker Go tests passed for source publication, direct and RAG search
authorization, HTTP read leases, worker build admission, MCP denial, and the
file-scoped question target. The fixed and RAG service and handler suites
passed. The RAG frontend typecheck, citation/export tests and production build
passed before the final authorization delta; CI rebuilds the final patch.
Both patched baselines also passed disposable pgvector PostgreSQL tests for
BatchSave versus source retirement, stale Publish after a new Stage, Stage
versus Tombstone, queued parser admission before Stage, crash/retry of exact
vector-ID receipts, and exact claimed deletion with retrieval checks. These
checks do not enable global derived GC.

The current patch also rejects empty text candidates as published answers,
returns a signed static no-content status, wakes the event dispatcher after a
completed prior sync when a new hint exists, and fences knowledge-list and
raw-search output with read leases and current authorization. Focused Go tests
in the repository and both handler packages passed for both pinned baselines;
the 27-route OpenAPI checker passed after applying each patch to a clean
source archive. The full suite and an exact pinned-`77c97fd7` RAG runtime
image have not yet been verified for this patch revision.

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
network access. Some upstream Dockerfile dependencies float, so this is a
development build rather than a bit-for-bit reproducible production image.
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

On 2026-10-01, the same patch SHA-256 also applied to newer RAG source head
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
run does not change the pinned baseline or claim the PDF
recovery, real AD or production performance acceptance above.
