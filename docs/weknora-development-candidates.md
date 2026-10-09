# WeKnora development candidates

`integration/candidates/manifest.json` pins two complete source patches for
isolated development. Each patch applies directly to its named upstream commit;
do not apply it on top of `integration/weknora.patch` or the default RAG patch.
The ordinary build scripts still select the previously verified default patches.

| Profile | Upstream baseline | Candidate patch |
| --- | --- | --- |
| c6 | `c6c4bd445a8ee49e742da9d804957a3fe4bf52d4` | `integration/candidates/weknora-phase3-c6.patch` |
| rag | `b6ea8b560b886cef77fb32fb2e092123cdce4ed3` | `integration/candidates/weknora-phase3-rag.patch` |

Use a new checkout at the exact baseline, verify the patch SHA-256 against the
manifest, then run `git apply --check <patch>` and `git apply <patch>`.
`python3 scripts/ops/verify-weknora-candidate.py --profile=rag --source-repo=<repo>`
checks the hash and both resulting trees with a temporary Git index without
changing that checkout's files or index. Use `--profile=c6` for the other profile.
Build the RAG candidate with the pinned upstream Dockerfiles and AnyDoc enabled:

```sh
WEKNORA_RAG_CANDIDATE=1 scripts/build-weknora-rag.sh
```

This selects the candidate manifest and verifies both exact trees before build.
Its default output tags are `weknora-ldap-app:nextcloud-rag-candidate` and
`weknora-ldap-ui:nextcloud-rag-candidate`. The build does not start or switch a
running service. Use those images in a separately owned fixture. Build output
records baseline and patch SHA in image labels; deployment evidence must also
record the actual image digests.
The manifest also records the exact resulting Git tree. The captured patches
passed fresh Git-index application and reverse application, reproducing both
the candidate and baseline trees exactly. Reverse application warns about three
pre-existing upstream lines (two Neo4j raw-string indents and a SQLite
SQL-string trailing space); the generated source
passes `git diff --check`.

## Included implementation

The candidate extends the default integration with original input receipts and
current authority checks for RAG/Agent inputs, saved answer/history text,
knowledge and chunk output, graph input, processing spans, collection display
fields, tags and FAQ entries. Production dependency configuration connects the
new services and HTTP guards. Unknown retained inputs cannot become trusted by
reading and signing their current contents. Newly admitted inputs have usable
human, code and typed derived producer paths, with source withdrawal and actor
revocation checked again before output.

Summary, related-question and image processing bind the actual original input,
model/configuration controls and image bytes. Default runtime prompt files have
code-owned origins. Legacy custom process-wide YAML prompts remain unknown.
New KB templates have explicit current-admin/code/derived writers, immutable
model-input/result receipts and counters. Dedicated read/write/generate routes
are wired in production; template bodies are hidden from generic KB JSON.
The Nextcloud auto-tag staging path also has a remaining worker-purpose gate.

Publication recovery captures exact original source identities, retires old
immutable generations, applies the externally authenticated Nextcloud recovery
plan, and obtains fresh signed source reconciliation. The maintenance CLI now
supports current snapshots and reserves its private output before DB mutation.
Fresh source epoch `0` is valid; reconciliation requires a strictly newer epoch.
See [the coordinated recovery gate](coordinated-publication-recovery-gate.md).

The capacity prototype measures the actual local filesystem, serializes
admission across knowledge bases sharing a disk, retains pending reservations,
and exposes admin policy/status controls. The default watermarks are 80% warning
and 90% paused AI ingestion. It counts registered retained original objects;
local source-file write intents now retain exact inode/object identity across
partial writes, failures, concurrent attempts and process crashes. An owner-only
CLI reconciles actual storage/catalog state; orphan bytes remain charged to the
KB, and legacy/unknown objects cannot be adopted as a new intent. SQLite and PG
crash/retry contracts passed. Derived/index accounting, bucket adapters and
overdue-GC prioritization remain incomplete. The owned capacity probe now uses
read-only source, rejects dirty source and existing/unsafe evidence directories,
records actual before/after Git identity, preserves frozen evidence, and returns
nonzero on failure. It passed 82 cases per profile with no failures or skips.

## Measured scope and remaining gates

[Candidate evidence](evidence/weknora-development-candidates-2026-10-09.json)
records commits, source patch hashes and measured test boundaries. The FAQ and
enrichment combination passed 317 test/subtest cases per profile with no failed
or skipped cases, including owned PostgreSQL and SQLite; the complete Go source
compiled on both profiles. Separate capacity/recovery and tag/auto-span gates
are recorded with their original measured commits.

The newest source also requires a Wiki policy on every service entry. Missing
policy denies before storage; ID reads resolve only KB ownership before body.
Its SQLite policy/ordinary-reader tests passed 36 cases, and three original
revision restore/churn/delete regressions passed. The subsequent persisted-ID
metadata ownership repair passed six focused cases; the final sources require
new full CI. See [Wiki policy](wiki-nextcloud-source-policy.md).

[Local derived inventory](nextcloud-local-derived-gc.md) records the independently
frozen real SQL/filesystem/native-vector probes. Audit metadata can remain
90 days under PRD §7.4; body-bearing JSON is still pending the separate
source-state-specific purge module and is not authorized by that deadline.

The subsequently integrated FAQ/tag HTTP lease gate and existing knowledge
collection gate passed 367 cases per profile, zero failures/skips, and full
source compilation. They protect the selected KB through capture and output,
reject missing stores, and release on cancellation. Cross-KB original parent
material and the actual `RunDue` object-delete adapter were completed by the
following independently measured integrations; global coverage remains closed.

The earlier all-parent/object-GC combination passed 529 cases per profile, zero
failures/skips, and
complete Go compilation on owned stock PostgreSQL and SQLite. FAQ metadata-only
plans now locate every parent/source scope before body reads; actual broad and
exact leases are validated in the capture transaction and held through HTTP
output, including progress and ordinary knowledge-list/detail LastResult.
SQLite single-connection authority checks use transaction-bound repositories.
Actual object `RunDue` checks coverage/read/build fences, blocks late reads,
and holds the final source/resource transaction through provider unlink and ACK.
Crashes, failed providers, lost tokens and restore competition have real SQL/
filesystem probes. No production coverage row is inserted. The subsequently integrated
local-derived module records actual backend selection
before writes, exact chunk/index writer receipts, native SQLite FTS5/vec0 and PG
row deletion receipts, and fenced filesystem pathname replacement. Root combined source probes passed 41 cases per profile with no failures or
skips, including actual original input, SQLite FTS5/vec0, stock PostgreSQL
index/GC and Wiki policy. A separate native halfvec probe passed on the exact
C6 source, left it unchanged and removed all owned resources. These are
controlled model/source fixtures, not full live application or global coverage. Full external inventory
and reader/writer coverage remain unfinished; production coverage stays closed.

Both CI runs of submission `beb2646` passed all 12 jobs. Actual retained logs
contain 547 producer/recovery test and subtest cases per profile, zero failures
or skips, complete Go compilation and frontend typecheck/build. This CI used the
manifest's `a9d31fb4` / `2f6f75fc` sources; subsequent local-derived changes need
separate verification and submission. Earlier CI records remain in the evidence.

The live joint recovery probe uses actual Nextcloud, nested synthetic LDAP,
PostgreSQL dump/restore, signed HTTP endpoints and WeKnora maintenance CLI /
repositories. Its parser/index payloads are fixture rows. It does not establish
full application/external-vector recovery or permission to reopen global ingress.

These candidates have not been deployed to the shared test stack. Full personal
read/build lease coverage and physical derived GC inventory remain incomplete;
the GC coverage gate remains closed. Saved-history original material and
machine initial upload/auto-tag admission are still being completed.
GC list preview/manual collection UI and complete derived/bucket capacity
accounting also remain open. Production-directory acceptance, full application recovery, and the
PRD performance targets remain outstanding. This is a reproducible development
submission, not a V1 release acceptance.
