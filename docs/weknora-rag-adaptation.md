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

For the existing local `weknora-ldap-local` stack, the local prerequisite
images are `weknora-ldap-app:rag-pdf-builtin-59671fac`,
`weknora-ldap-ui:rag-pr`, and `weknora-go-test:1.26-sqlite`. With those present,
run:

```sh
./scripts/build-weknora-rag.sh
./scripts/use-weknora-rag.sh
```

The first command creates patched local app and UI images from this exact
patch. The second saves a PostgreSQL dump in ignored `dist/backups/` before
switching the shared local app and frontend; its Compose overlays also join
the Nextcloud development network and enable the local HTTP source route.
Use synthetic local data. A disposable HTTP/API handoff, answer, citation and
revocation drill passed with mock models; see [its record](synthetic-ldap-compose.md).
Live enterprise AD, Team Folder ACLs, browser-click acceptance and production
load remain open until separately verified; see [V1 status](v1-status.md).
