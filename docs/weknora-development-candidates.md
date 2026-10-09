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
The normal build explicitly enables the Go checksum database (`sum.golang.org`
unless the operator supplies `WEKNORA_RAG_GOSUMDB`), because the upstream Docker
ARG default is off. It retains the normal AnyDoc app and UI build paths.
Its default output tags are `weknora-ldap-app:nextcloud-rag-candidate` and
`weknora-ldap-ui:nextcloud-rag-candidate`. The build does not start or switch a
running service. Use those images in a separately owned fixture. Build output
records baseline, patch SHA, exact candidate tree and source commit in image
labels, plus a distinct app/UI role; deployment evidence must also record the actual image digests. Before
starting the owned browser fixture, require the exact manifest's pair:

```sh
python3 scripts/ops/inspect-weknora-candidate-images.py --profile=rag \
  --app-image=weknora-ldap-app:nextcloud-rag-candidate \
  --ui-image=weknora-ldap-ui:nextcloud-rag-candidate
python3 scripts/ops/isolated-browser-ask-smoke.py --candidate-profile=rag \
  --weknora-image=weknora-ldap-app:nextcloud-rag-candidate \
  --weknora-ui-image=weknora-ldap-ui:nextcloud-rag-candidate
```

The read-only image check rejects old/incomplete build labels and captures
immutable image IDs. The browser fixture verifies those same IDs before
startup and its owner verifies that the tags still point to the captured IDs.
For a frozen older build use `--candidate-manifest=/absolute/snapshot.json`
on both commands. Label verification alone does not establish runtime success;
no current candidate image build or application acceptance is claimed here.
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
The actual Nextcloud first-upload and completion Auto path now has immutable
original byte/name/configuration controls. Its production integration and
remaining complete application gate are recorded below.

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
Complete derived/bucket capacity accounting remains open. Production-directory acceptance, full application recovery, and the
PRD performance targets remain outstanding. This is a reproducible development
submission, not a V1 release acceptance.

The8521d6a C6 PR candidate first gate failed13 native SQLite test/subtest
nodes because its broad `SQLite` selector reached the new FTS fixture without
the required tag. The raw log/artifact is retained. Candidate producer tests
now use `sqlite_fts5`; default complete source compilation remains untagged.
The isolated native41-case root gate was already tagged and passed.


## Administrative collection update

[Administrator cleanup](nextcloud-gc-admin.md) adds scoped status/pagination,
expiry, confirmed-versus-estimated bytes, category preview, retry and guarded
immediate attempts. It reuses the existing exact source/reference/read/build
checks without overriding retention or activating coverage. Current SQL web
admin/policy is checked in each actual destructive transaction.32 cases passed
on owned PG/SQLite, including real unlink, late account revocation and110-job
paging; exact cleanup verified. The actual Vue app TypeScript project and Vite
bundle passed;13 locale registry tests passed. Body purge and global/external
coverage are still pending.

Submission8426485 passed all12 push/PR jobs. Per profile, retained logs contain
566 producer cases,35 actual local-derived cases,11 native halfvec cases, all
with zero failures/skips, and complete Go compilation. Its old root-only
`vue-tsc --noEmit` command did not select the app project. The new candidate CI
uses `vue-tsc --noEmit -p tsconfig.app.json`; Vite builds were actual throughout.
The current GC candidates149b2a9f/523a123b need a fresh full CI run.


## Saved message producer core

The next candidate includes151/70 actual message generation/material receipts,
current owner/body/membership counters, transaction-local actor/share authority,
and metadata-only parent read plans. Saved HTTP reads retain actual leases and
source authorization through output. IndexMessageToKB consumes the original
committed pair rather than caller-supplied Q/A text; its actual queued worker
holds all history parent scopes through chunk writes. The core's raw user
projection covers only authored fields and does not certify generated captions,
checkpoints or attachments.

[Core scope and remaining consumers](source-lineage-saved-message-materials.md)
separates those paths from Agent model lifetimes, completed cached stream frames,
clone/steer/artifact/memory and partial generated-field writers still underway.
The owner's raw targeted log had80 pass/2 skip: two repository PG legacy-negative
cases lacked the lease DSN. Its actual PG151 producer/worker/output path passed;
those facts do not turn the skipped cases into passes. Root combination sets
all owned DSNs. Its actual combined message/FAQ/collection/local-index/admin
GC gate passed285 cases per profile with no failures or skips; both integrated
source trees stayed clean and exact owned resources were removed.152 is an availability
hook only until the body vault/journal module is integrated and verified.


For a candidate that includes152/71, pass `--body-journal` to the owned browser
fixture. The owner starts only its fresh database, invokes the packaged actual
migration CLI, provisions three independent nonce-owned volumes at0700 plus a
0600 random key under the runtime appuser, then runs init/verify before starting
readers/builders. Repeated up verifies the existing anchor; it never replaces it.
The three mounts cannot be repointed to the DB/data volume or shadowed. Offline
state/topology tests cover these exact bindings; full application execution is
pending the frozen body module and normal candidate image. This mode is for
fresh fixtures, while restore uses retained anchors plus reconcile/verify.


## Actual first-upload and classification integration

[Machine input evidence](knowledge-machine-original-input-validation-20261009.md)
records actual signed ProcessSync→CreateFile/catalog/capacity→Stage→parse/index→
Summary/Question→Auto→publish→metadata/chunk/RAG/span reads on PG and SQLite.
150/69 seals actual original bytes and initial grouping-name/configuration,
queued controls, actual model/tenant digests and revisions. Runtime credentials
are excluded from persisted snapshots/prompts. Question metadata output gets an
exact own-producer transition; external edits/ABA remain denied. The root's
production DI uses the same transaction authority for all three new hooks.

The first root combination passed309 cases but failed60 nodes per profile:
new149/68 plan schema was absent in the older machine fixture, and an old HTTP
Auto fixture lacked the new actual queue/model inputs. The source guards refused
before model work. After installing the real fixture migrations before new
knowledge and using actual model/ticket writers,84 affected cases per profile
passed with no failures/skips. These include all machine positives/negatives,
HTTP Auto span and actual SQLite fresh/upgrade migrations. Original failed logs
remain pinned. The old CI migration expectation68 versus actual70 was also
corrected with explicit new table assertions. New complete CI is still required.

A normal AnyDoc core app/UI pair (older sourcee8ca35f3) was actually built and
started with owned nested LDAP/Nextcloud. Registration/model/KB/pair/signed sync
produced1 chunk and1 embedding, but authorized personal read returned403. Its
attached grouping tag had no display origin. That concrete failure is retained;
the new150 grouping receipt must pass a fresh full application probe. All core
probe resources were removed. New machine candidate image build is underway.
No shared service switched and no full V1 completion is claimed.


## Normal machine candidate application probe

The normal AnyDoc app/UI build of source8d35d848 completed and its immutable
images matched the frozen source/tree/patch and distinct app/UI role labels.
A new owned Nextcloud/WeKnora/nested-LDAP stack performed actual registration,
model/KB configuration, source pairing and signed sync:2 chunks and2 embeddings.
The nested permission matrix passed: the authorized employee had DAV/source/
knowledge/search access, while the other department was denied. This repairs
the older core candidate's concrete grouping-tag403 without weakening guards.

Actual first personal RAG QA remains failed. The current request's newly created
assistant placeholder was included in completed-history material capture,
causing KnowledgeQA refusal; the SSE reader later timed out. There were2
messages/receipts but no completed assistant. The independent consumer module
is adding original completion/excluded-request controls before body reads and
holding them through model/output. It must pass a new first-QA positive plus
selection/source/actor negatives. This application probe is not full V1
acceptance. [Actual image/runtime boundary](evidence/normal-machine-candidate-app-20261009.json)
records IDs and the failed QA metadata, without credentials or body text.


## Failed QA stream termination

[Terminal error fix](nextcloud-qa-terminal-error.md) repairs the real application's
error-with-done stream waiting for a successful complete marker. Live/Continue
loops now stop on terminal error with a static message; they skip title waiting
and do not flush source-derived holdback or raw backend diagnostics. Actual
HTTP closure, source revoke and prior poll/error regressions passed11 cases,
zero failures/skips. Complete first-QA/history selection remains a separate
in-progress gate.

Submission53df55f passed all12 push/PR jobs. Retained per-profile logs contain
726 producer cases,35 local-derived and11 native halfvec cases with0fail/skip,
complete Go compile and actual frontend app-project typecheck/build. The new
SSE delta6a3df67e/1bc5cfd0 subsequently passed all12 push/PR jobs in
submissione01b417. Downloaded artifacts from PR run37879686759 contain, per
profile,726 producer cases,11 QA terminal cases,35 local-derived cases and11
native halfvec cases, all with0 failures/skips. Complete Go compilation and
the actual frontend app-project typecheck/build passed. The artifact hashes
and package results are retained in the evidence JSON. This source precedes
the independently developed body152, durable Auto153 and history consumer
changes, which still require combined application verification.

## Body, durable completion and original live consumers

The new full candidates include body152/SQLite71 and Auto153/SQLite72, the
original-history live HTTP holder, native same-instance running replay,
original Rewind cuts and physical RAG retrieval guards. Normal images now
package original-body-retention, publication-recovery and capacity-reconcile
from the same source. Their final Docker build is pending.

The readonly dual-profile combination passed551 tests/subtests per profile,
zero failures/skips, across eight packages with real PostgreSQL/SQLite/Redis
and both maintenance CLIs. Exact owned resources were removed. That measured
source precedes the final scope-completeness marker correction; its separate
six-case dual-SQL gate and four CLI forward/restore cases passed. Final full CI
and normal app startup/QA/reconnect/restore/P5 metrics remain required.

The previous combination failed485pass/11fail per profile. Old chunk HTTP
fixtures omitted actual actor/group/input authority; their corrected seven
cases passed. The old body GC fixture lacked private build context and
Auto153 controls. Failed logs remain in the evidence. Raw diagnostic bytes
are preserved with binary Git attributes; source whitespace checks stay on.

New generated parent records omit duplicate Messages bodies. Known old
generated/fork parent-copy leaves remain inventoried and retained when
original copy/lifecycle proof is insufficient; they are not reported as
physically cleared. That cleanup protocol, Steer154/73, remaining consumer
lifetimes, full application acceptance and performance are active work.

## Resumed build and recovery verification — 2026-10-09

Submission `e26b22d` passed all twelve push/PR jobs. Downloaded PR artifacts
contain, per profile, 934 producer cases, 16 QA terminal cases, 35 local-derived
cases and 11 native halfvec cases, with zero failures or skips. Complete
compilation passed 115 packages; the actual frontend app project typecheck and
build passed. The retained artifact hashes and source identities are in
`submission_e26b22d_ci` in the evidence JSON. These results cover the preceding
`4866f7be` / `c39e7e4e` sources.

The resumed normal build initially failed downloading `migrate@latest`, which
resolved beyond the module's pinned dependency. Candidates `85e29999` /
`400d4392` now pin the tool to the existing `go.mod` version `v4.19.1` and enable
the official Go checksum database by default. Both complete patches still
apply and reverse to their exact Git trees. The normal AnyDoc app/UI build for
`400d4392` completed with checksum verification enabled, and both image role,
source, tree and patch labels match the manifest. Shared services were not
switched. Fresh nested-LDAP application and restore acceptance is in progress.

The normal restore runner now compares role dumps while normalizing only a
strictly matched PostgreSQL `\restrict` / `\unrestrict` token pair; raw SQL
archives and their integrity hashes are retained. Role/password/grant changes
still fail the check. Its authorized body upgrade uses the actual packaged
`upgrade-scopes` mode. Seventeen restore guard cases and eight performance
method cases passed, and both suites are included in CI. These offline checks
do not constitute full application restore or measured performance acceptance.

The fresh `400d4392` nested-LDAP/body-journal fixture actually started, paired
and ingested two chunks and two embeddings. Alice's source authorization,
chunks, preview and search passed, but detail reads returned 403 and first QA
did not complete. The retained [normal application record](evidence/normal-candidate-resumed-app-20261009.json)
separates these failures from successful setup. Display-origin cache queries
used `Scan`, bypassing vault hydration; the `Model`/`Find` correction passed
actual PostgreSQL and SQLite regressions on both profiles. First-QA turn-guard
context reuse and static terminal-error delivery are being repaired separately.
Full restore and performance measurements have not started on this failed
application candidate.

The P5 probe now counts all active body leases for its owned tenant through
the payload catalog, including direct knowledge and message bodies without
parent-reference rows. Nine method checks passed on both macOS and Linux;
the new count case executes the actual predicate with direct/message, expired,
released and foreign-tenant rows. This remains method validation, not a P95
measurement.

## Normal application read and QA repairs

Candidates `426c1ad4` / `155f0732` combine the display vault fix, reused RAG
turn-fence context binding, and safe terminal-error delivery. `ContextFor`
keeps the current caller's authority, selection and lineage accumulator while
honoring both caller and fence cancellation. Actual PostgreSQL/SQLite tests
cover a fence created before the lineage accumulator, repeated search and
augmentation, and denial after source retirement, missing proof or cancellation.
Terminal controls carry a fixed message and error code bound to the current
request/session; provider diagnostics and source body tails are discarded.

Final read-only combination checks passed 21 C6 terminal cases and 42 RAG
cases (6 display, 5 context, 21 terminal and 10 actual turn cases), with no
failures or skips. An initial concurrent build/test compiler OOM executed no
tests; its log remains retained. The serial actual-turn retry with
`GOMEMLIMIT=2GiB` passed, and exact owned test resources were removed. Existing
tool-result fixtures with no producer lineage still fail identically before
and after this change; their nil-lineage rejection was preserved.

The normal AnyDoc app/UI `155f0732` build completed and its immutable image
IDs and role/base/commit/tree/patch labels match the manifest. The complete
patches apply and reverse exactly on both profiles. The [repair evidence](evidence/normal-candidate-read-qa-fixes-20261009.json)
records measured component gates separately from pending full HTTP, clean
restore and P5 acceptance. Fresh isolated application and performance fixtures
are being prepared; the shared runtime remains unchanged.

## HTTP material initialization and resource limits

Submission `a97ff93` passed all twelve push/PR jobs. Its fresh `155f0732`
application passed the complete nested-LDAP permission matrix, fixing the
earlier detail-read 403. First QA then exposed a real initialization race:
the first HTTP write checked the material holder before the asynchronous
pipeline had marked its actual original inputs ready. The response returned
503 and closed that holder before later Search/Merge accounting.

Candidates `35968820` / `eb7d5756` now distinguish pending initialization from
an actual authority refusal. The live HTTP loop waits without serializing or
advancing the original offset, rechecks the real holder after setup, and ends
safely after cancellation, refusal, failure or a bounded ten-second wait.
It does not mark a placeholder completed or fabricate a ready receipt.
Thirty-seven actual initialization/terminal/tail cases passed; the original
503 and corrected fixture-assertion failures remain retained. The full image
build and fresh application acceptance follow this source check. The [initialization record](evidence/normal-candidate-material-initialization-20261009.json)
retains the failed normal application result separately from these component
checks.

The isolated P5 trial passed its permission matrix but produced zero samples:
its app was OOM-killed before the initial change hints could be delivered.
The unlimited normal application was also OOM-killed during the same period;
there is no historical RSS evidence establishing a leak or a particular
container limit as the cause. All four owned failed fixtures were stopped
after marker verification while keeping their volumes, original files,
anchors and private evidence. Shared services were preserved. A separate
four-GiB trial with `GOMEMLIMIT=3GiB` is prepared, with serial build/test/start
ordering; it has not measured or accepted any P95 target.

## Proven summaries in ordinary RAG Merge

The fresh `eb7d5756` application passed the permission matrix and no longer
returned the early material-initialization 503. Its first QA closed HTTP 200
after Search/Merge but produced no answer. Read-only production-row evidence
showed a 102-byte text chunk and a 28-byte generated summary, both enabled and
indexed, with the summary pointing to the text chunk. The narrow parent
expansion treated every non-text parent relationship as unsupported and made
the source prefix unknown before the final model stage.

Candidates `6ccecc02` / `cec0e03b` verify the summary itself against its original
capture, including body, type, parent, revision, position, image and metadata.
They retain the summary and original proof without adding parent body text.
Unknown proof, cached-body changes, forged parent relations, source retirement
and unsupported image parents remain rejected. The actual signed short-file
producer and SQL retrieval/merge path failed its two positive scenarios before
the fix; after it, 42 RAG cases and 18 new C6 cases passed on PostgreSQL and
SQLite, with zero failures or skips and exact owned resource cleanup.

The [summary repair record](evidence/summary-parent-merge-20261009.json) separates
these component results from the still-required normal-image QA/history and
restore acceptance. The later Steer integration below supersedes these source
commits while retaining their summary fix.

## Original Steer inputs and completed successor provenance

The current manifest freezes C6 `afdfd48d` and RAG `f9a75a0e`, including
PostgreSQL154/SQLite73. Authenticated enqueue stores the actual authored input;
Redis holds transport mirrors. Promote/delete/consume name the exact immutable
control head. An `after` input keeps its original author/run and derives the
successor's real user admission through an immutable consumption transition.
The old material owner admits only its exact assistant +1 and user +1 changes,
retaining all preceding controls and original body proofs. The successor starts
after actual preceding completion and binds its saved initial input.

Actual debugging retained failures for automatic follow-up after refusal,
an unsupported cross-run consume, mismatched metadata/body exclusion rules and
an unfinished preceding turn invalidating history. The corrections preserve
the original proof comparisons. Source/actor/control changes reject the next
model or output; a failed live-run claim preserves the consumed input and
negative control rather than deleting its evidence. Consumed controls must
still equal the exact recorded result head. New empty exclusion lists are
omitted from JSON, preserving existing request bytes and digests. Migration
rollback refuses to erase the new proof tables; old unknown history is never
backfilled into signed history.

Each final profile passed 233 test nodes / 107 top-level tests: actual HTTP44,
service interoperation54, full relevant session59, real PostgreSQL/SQLite/Redis
Auto completion42, migrations16, Agent16 and request contracts2. Full compile
completed for 155 packages, including 40 with no test files. All selected tests
had zero failures or skips. Root rechecked every final raw-log SHA, test
terminal/count and the required actual after positive. The full patches apply
and reverse to the exact fixed baseline trees. [The component evidence](evidence/steer-original-material-20261009.json)
records those boundaries and the preserved failures.

The normal AnyDoc build and full application QA/running replay/restore remain
pending. Parent body-copy closure and the local withdrawal inventory extension
are separate prepared source work, not included here. Shared runtime, default
build selection and broad physical-GC/ingress acceptance flags remain unchanged.


## 2026-10-09 parent-copy closure and Agent title integration

The current manifest freezes RAG `595f241f` and C6 `f766a373`. Known legacy
parent-message copies derive their complete dependency closure from the
immutable original admissions and generated-field receipts, including later
cross-KB fields. Purge requires closed hosts, retired original sources, exact
closure and no active pins. Unknown or corrupted copies remain retained; no
current lookup recaptures them as known. Own generated growth keeps its old
pins while acquiring new KB and exact-source leases, rechecks source/actor,
epoch and cancellation after acquisition, and rolls back only the new pins
on failure. Actual PostgreSQL/SQLite and the built retention CLI were used.

Agent QA now honors `disable_title`. The observed CI failures were two real
Agent sender timeouts caused by starting a title despite that flag. Actual
true/default/explicit-false cases check the real delegated title call count,
completion and lease cleanup; the eight-second assertion remains unchanged.

Each frozen profile passed 425 executed nodes across the selected gates,
including repeated interoperability nodes: 414 distinct nodes / 165 top-level
tests, zero failures or test skips. Full compile completed for 155 packages
(115 with test files, 40 without). Root independently rechecked every raw
log hash, terminal, count and source tree. Temporary-index forward and reverse
application reproduced the candidate and upstream trees exactly. Self-owned
test resources were removed by exact ID and nonce; old failed fixtures,
uncommitted work and shared caches were preserved.
[The component evidence](evidence/parent-copy-title-original-material-20261009.json)
records the boundaries. CI now requires the parent-copy/growth and title tests
to execute rather than treating an empty selector as acceptance.

Normal AnyDoc images, full application QA/running replay/restore and P5 remain
pending. The PG155/SQLite74 local withdrawal extension is still prepared
source only and is not in these patches. Default build selection, shared
runtime and broad physical-GC/ingress acceptance flags remain unchanged.
The [exposed-consumer source follow-up](exposed-consumer-follow-up-20261009.md)
records separate memory, skill transcript and non-Web principal work without
claiming runtime exploit evidence or narrowing V1.
