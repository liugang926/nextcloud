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
The manifest also records the exact resulting Git tree. The captured patches
passed fresh Git-index application and reverse application, reproducing both
the candidate and baseline trees exactly. Reverse application warns about two
pre-existing upstream Neo4j raw-string indentation lines; the generated source
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
code-owned origins. Legacy custom process-wide YAML prompts remain unknown;
an explicit knowledge-base template writer is still being implemented.
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
overdue-GC prioritization remain incomplete. The older isolated capacity probe
still formats its source and overwrites its evidence; root verification uses a
separate read-only source runner and the probe needs that safer output contract.

## Measured scope and remaining gates

[Candidate evidence](evidence/weknora-development-candidates-2026-10-09.json)
records commits, source patch hashes and measured test boundaries. The FAQ and
enrichment combination passed 317 test/subtest cases per profile with no failed
or skipped cases, including owned PostgreSQL and SQLite; the complete Go source
compiled on both profiles. Separate capacity/recovery and tag/auto-span gates
are recorded with their original measured commits.

The subsequently integrated FAQ/tag HTTP lease gate and existing knowledge
collection gate passed 367 cases per profile, zero failures/skips, and full
source compilation. They protect the selected KB through capture and output,
reject missing stores, and release on cancellation. Cross-KB original parent
material and the actual `RunDue` object-delete adapter remain separate coverage
gates. The tests use the generation claim protocol and do not establish complete
physical provider cleanup.

Both CI runs of submission `324425d` passed all 12 jobs, including each
candidate's 337 producer/recovery cases with zero failures/skips, 115 compiled
test packages and frontend typecheck/build. Those logs are retained and pinned
in the evidence; the newer read-lease candidate is verified separately above
and will receive its own CI run.

The live joint recovery probe uses actual Nextcloud, nested synthetic LDAP,
PostgreSQL dump/restore, signed HTTP endpoints and WeKnora maintenance CLI /
repositories. Its parser/index payloads are fixture rows. It does not establish
full application/external-vector recovery or permission to reopen global ingress.

These candidates have not been deployed to the shared test stack. Full personal
read/build lease coverage and physical derived GC inventory remain incomplete;
the GC coverage gate remains closed. FAQ import-progress output is being
completed. Production-directory acceptance, full application recovery, and the
PRD performance targets remain outstanding. This is a reproducible development
submission, not a V1 release acceptance.
