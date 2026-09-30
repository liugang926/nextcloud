# WeKnora RAG baseline adaptation

`integration/weknora.patch` remains the reproducible patch for WeKnora commit
`c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` used by the fixed-baseline
Docker integration build. `integration/weknora-rag-b8a34e0b.patch` is a complete,
separate patch for the RAG branch at commit
`b8a34e0bae8fcf0d3c8273bba2e56414abed41e2`. Apply **one** patch to its
matching base; the RAG patch is not a delta to the fixed-baseline patch.
The fixed-baseline patch SHA-256 is
`f572633bf0d82546f517a136d3e5d0eee63d113869a2c470b90a8f985e986b2c`.

From a clean WeKnora worktree at that exact RAG commit:

```sh
git apply --check /path/to/nextcloud/integration/weknora-rag-b8a34e0b.patch
git apply /path/to/nextcloud/integration/weknora-rag-b8a34e0b.patch
```

The RAG patch SHA-256 is
`1bbeb4989ac21afb8c70754064f64aed5eb1d1d918ef9601b5afbe0728c6a1c8`.
The build script and CI reject a different patch hash. On 2026-09-30 both
baseline patches applied cleanly to fresh source archives. The RAG baseline
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
WEKNORA_RAG_TAG_SUFFIX=nextcloud-rag-b8-anydoc ./scripts/build-weknora-rag.sh
```

This creates `weknora-ldap-app:nextcloud-rag-b8-anydoc` and
`weknora-ldap-ui:nextcloud-rag-b8-anydoc`. The build downloads browser-skill,
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
