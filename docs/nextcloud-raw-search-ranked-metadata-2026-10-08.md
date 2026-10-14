# Raw knowledge search: ranked metadata and denial latch

This correction covers the unified raw search HTTP handler. It does not enable
the separately staged RAG/Agent lineage consumers, accept complete V1 or enable
physical derived-copy GC.

An owned fixture on newer upstream F plus the preceding EA patch reproduced a
403 from `/api/v1/knowledge-search` while keyword hybrid search returned the
current authorized source. Reranking adds `base_score` and `model_score` to the
result metadata. Comparing that enriched map byte for byte with the persisted
source map treated an expected diagnostic as a source identity change.

The new matcher preserves every persisted metadata key and its value, including
any stored score key. Only extra `base_score` and `model_score` fields may be
present, and both must be finite numbers. Unknown additions, missing or changed
source keys, channel and KB changes remain denied. Neither map is mutated;
nil and empty retain the old exact-match distinction. Ranking diagnostics are
not source authority or a replacement for original-version lineage.

The same HTTP response now latches its first publication or lease denial.
Before that denial, every write boundary still checks current access. A later
successful check cannot release protected JSON after an earlier 403/503. The
existing durable read leases and live publication checks remain in place.

## Verification and scope

The original score positives and transient-denial cases were red on the
preceding source, then green with the two-file incremental patch SHA-256
`cdb45fa6a3779f91d048bf2b645d242f5b0dafc51bfaa7aa18a88b6e530bfe97`.
Original evidence also covers output revocation, source retirement, GC busy
while a lease is held and exact lease release. The preceding-source whole
session package recorded 364 passing tests and vet success.

On the two current canonical complete c6/RAG77 trees, the entire session package
passed independently in a networkless cached Go container using one CPU and
2 GiB of memory. These checks use local synthetic stubs and do not rebuild an
image or prove a new live event/sync deployment. CI now explicitly includes
`RawSearch` in both pinned-profile authorization gates. The safe
[integration record](evidence/nextcloud-raw-search-ranked-metadata-2026-10-08.json)
records complete before/after hashes. Historical evidence keeps its original
measured source hashes.

The F+EA reproduction is evidence for that older combined tuple only. Current
canonical patches remain pinned to c6 and RAG77; their package success is not
a hosted compatibility claim for the latest patch on newer upstream F.
