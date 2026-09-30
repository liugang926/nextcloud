# WeKnora RAG baseline adaptation

`integration/weknora.patch` remains the reproducible patch for WeKnora commit
`c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` used by the fixed-baseline
Docker integration build. `integration/weknora-rag-06792ba5.patch` is a complete,
separate patch for the RAG branch at commit
`06792ba5d88ed8fd0ddb9cf77f927c6cb9ae29fc`. Apply **one** patch to its
matching base; the RAG patch is not a delta to the fixed-baseline patch.
The fixed-baseline patch SHA-256 is
`f7b256ccb78f762954daae7765823bd3fc5072260dbc25d1ad6bc41d0b267baa`.

From a clean WeKnora worktree at that exact RAG commit:

```sh
git apply --check /path/to/nextcloud/integration/weknora-rag-06792ba5.patch
git apply /path/to/nextcloud/integration/weknora-rag-06792ba5.patch
```

The RAG patch SHA-256 is
`a7d638ac36f2e64b5adf998cdd16a811c238de343a5ab8d001262fa8207c3961`.
On 2026-09-30 both baseline patches applied cleanly to fresh source archives.
Focused Docker Go tests passed for source publication, direct and RAG search
authorization, HTTP read leases, worker build admission, MCP denial, and the
file-scoped question target. The fixed and RAG service and handler suites
passed. The RAG frontend typecheck, citation/export tests and production build
passed before the final authorization delta; CI rebuilds the final patch.

For the existing local `weknora-ldap-local` stack, run:

```sh
./scripts/build-weknora-rag.sh
./scripts/use-weknora-rag.sh
```

The first command creates patched local app and UI images from this exact
patch. If local `weknora-go-test:1.26-sqlite`,
`weknora-ldap-app:rag-pdf-builtin-59671fac`, and `weknora-ldap-ui:rag-pr`
images are present, it uses a fast overlay build. Otherwise it uses the
patched source archive's upstream Dockerfiles with `WITH_ANYDOC=0`; that full
build still downloads browser-skill/Rust, DuckDB and other dependencies and
requires substantial time, disk and network access. The upstream Dockerfile
contains floating build dependencies, so this fallback is a development
build, not a bit-for-bit reproducible production image. The fast frontend
build runs in a Node 24 container, so host `npm` is not required. The second command
saves a PostgreSQL dump in ignored `dist/backups/` before
switching the shared local app and frontend; its Compose overlays also join
the Nextcloud development network and enable the local HTTP source route.
Both build paths label their images with the fixed source commit and full patch
SHA-256; the built backend reports the same source-plus-patch revision.
Use synthetic local data. A disposable HTTP/API handoff, answer, citation and
revocation drill passed with mock models; see [its record](synthetic-ldap-compose.md).
Live enterprise AD, Team Folder ACLs, browser-click acceptance and production
load remain open until separately verified; see [V1 status](v1-status.md).
