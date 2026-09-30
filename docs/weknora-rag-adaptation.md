# WeKnora RAG baseline adaptation

`integration/weknora.patch` remains the reproducible patch for WeKnora commit
`c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` used by the local Docker
integration build. `integration/weknora-rag-06792ba5.patch` is a complete,
separate patch for the RAG branch at commit
`06792ba5d88ed8fd0ddb9cf77f927c6cb9ae29fc`. Apply **one** patch to its
matching base; the RAG patch is not a delta to the fixed-baseline patch.

From a clean WeKnora worktree at that exact RAG commit:

```sh
git apply --check /path/to/nextcloud/integration/weknora-rag-06792ba5.patch
git apply /path/to/nextcloud/integration/weknora-rag-06792ba5.patch
```

The RAG patch SHA-256 is
`0d6f73ceaa979a1e2de28e284a1634ed9891a2845f6fa8b19d3a290b2e111cff`.
On 2026-09-30 it applied cleanly to a fresh worktree at the recorded commit.
Focused Docker Go tests passed for the four SQLite migration and pairing guard
cases, Nextcloud publication, presigned file and guarded file routes, and RAG
evaluation datasets. Related container, router, handler, service, repository,
connector and agent-tool packages compiled. The local Docker service on port
18080 was not switched to this RAG adaptation. Its full browser and production
load acceptance remain open, as do the wider V1 gaps in `docs/v1-status.md`.
