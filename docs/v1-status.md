# V1 implementation status

This repository implements a local development slice of the [PRD](development-plan.md), not a V1 enterprise release. The PRD describes requirements; the table records the historical implementation and tests through 2026-09-24, with a newer addendum below. Only synthetic local data has been used for cross-system testing.

## Current checkpoint — 2026-10-11

Ordinary original-model HTTP now passes10 selected nodes after fixing the
constructor-to-HTTP owner lifetime. Four remote embedding protocols pass80
SQL/HTTP nodes; a production-identical manual-fixture child passes24 memory
input/result and public-entry nodes in a separate window. Those component
results do not accept the full embedding feature. The reviewable source remains
separate from the adopted candidate manifest.

Current source `7c9b30e2` now passes all227 mandatory nodes in one serial
window: native55, productive derived24, ordinary model HTTP10, model authoring/
memory/body58, policy58 and queue/lifecycle22. Zero failures/skips/OOM; the
window took995.190s under the unchanged20-minute bound. All12 exact owned
resources are independently absent. This closes those complete selected gates,
including the formerly failing PG public-writer operation; it does not recover
the unchanged unbound515 test or accept new consumers/full V1. See the [227-node
record](evidence/memory-current-native-model-policy-227-GREEN-20261011.json).

The original partial processing test now passes SQLite and PostgreSQL under
the unchanged20s deadline on `ce006db6`, with its full test file byte-identical
to the failed71d source. Same-TX physical availability batching addresses the
repeated head/journal/file IO identified in the [paired diagnostics](evidence/memory-derived-performance-diagnostics-20261011.json).
Old inline22, new batch14, new actual lease-expiry2 and old worker final-clock6
nodes pass on production-identical test-only children in separate windows.
Actual mutation and expiry-readback prerequisites were strengthened before
acceptance; earlier failures remain preserved. This is not a single46-node run
or full PRD performance acceptance. See the [partial and clock record](evidence/memory-original-partial-and-clock-GREEN-20261011.json).

The next `1e70c75b` candidate combines query/vector/rank, ordinary Recall,
Local/Ollama original embedding, complete155/74→156/75 migration and latejournal
snapshot checks. All237 changed Go files parsed and11 selected packages compiled
in129.439s with zero runtime tests. The50-node original/body/clock regression now
passes in213.065s, zero failures/skips/OOM, with11 exact resources absent. The first
112-node consumer window stopped at a query expiry observer precondition after
nine SQLite leaves passed; no consumer gate is accepted. The full migration
window executed30 nodes (4PASS/26FAIL/0SKIP), exposing a removed fixture column
and real duplicate trigger semantics at155/74. Both are under repair. See the
[query failure](evidence/memory-query-observer-precondition-failure-20261011.json)
and [actual migration failure](evidence/memory-full-migration30-failure-20261011.json).
See the [latejournal50 record](evidence/memory-last-journal-50-GREEN-20261011.json). See the
[compile record](evidence/memory-recall-local-integrated-compile-20261011.json).

Source inspection found that actual Recall dependency binding is not stored in
the completed assistant input manifest. Its historical reader therefore lacks
an exact memory publication/body/policy/model graph; this is a confirmed source
gap, awaiting matched realhistory evidence and a durable bridge. Explicit local
model download also still needs its original endpoint/lifetime. Tenant-cloud
credentials, complete history, application/broker/recovery and PRD performance
remain required. The full V1 goal remains active; deployment and coverage flags
remain disabled.

## Prior component checkpoints — 2026-10-10

These entries preserve earlier evidence and superseded intermediate states. The current checkpoint above governs current acceptance.

Native fixture follow-up on `c42071de` passes40/55 nodes, with15 failures and
no skips/OOM; the gate is unaccepted. The new complete-effect verifier uses
a pending-only loader after successful completion, which needs a historical
metadata oracle; the public-writer barrier has a separate unresolved error.
Original partial tests still pass SQLite and fail PostgreSQL second Handle at
20s after same-TX metadata reuse on `71d92523`. The old20s bound and test body
remain unchanged. Query-count/profile pairs are prepared for real bottleneck
measurement; no optimized-performance acceptance is claimed. All failed
owners/raw/volumes/networks remain preserved.

Embedding memory follow-up now passes24 nodes on test-only child `5766a09b`:
exact leased document input and same-TX vector/output/body receipts, current
actor/policy/dimension refusals, and public Remember/Update/optional-failure
entry behavior. The genuine manual origin fixture repair changes no production
code; the prior80 four-protocol model gates remain separately attributed.
The window took77.147 seconds, zero failures/skips/OOM, with original write10s
unchanged. Query/receipt-backed rank/history/sync/local/cloud routes remain
in progress. The [embedding proposal](../integration/proposals/weknora-original-embedding-20261010.json)
round-trips to its exact tree and is kept separate from the adopted manifest.

New private embedding source `f3a4f1b1` parsed186 changed Go files and compiled
all11 selected packages (127.796 seconds, zero runtime tests). Its true remote
model original producers/held constructors now pass80 SQL/HTTP nodes across
OpenAI, DashScope, Ark and Google, including exact recipe/body/dimensions and
current-authority/active-cancel cases. Memory input/result tests failed all10
nodes before provider entry because their Remember fixture omitted explicit
manual origin; existing defaults classify it as unproven extracted output and
correctly refuse it. A two-line test-only authoring repair is prepared, not
executed. Query/vector-receipt/history/rank/sync and local/cloud variants remain
required. See the [embedding gates](evidence/original-embedding-models-and-memory-gates-20261010.json).

The matched ordinary original-model HTTP selection now passes all10 nodes
on private source `e8b2dd63`, zero failures/skips/OOM. Real old-source observer
confirmed constructor cancellation closed the model read while HTTP still
owned one reference. The fix preserves cancellation throughout acquisition
and transfers the proved read to the live message owner; provider calls keep
their original cancellation. All four legal current/Done-open cases publish
answer/references/complete and one assistant/original input manifest, while
actor/model revocations still cancel/join and create no completion. Original
HTTP8s/package20m remain; the window took86.350 seconds. Paired tests/diagnostic
are byte-identical, and all seven successful resource identities are independently
absent. See the [owner-lifetime GREEN](evidence/ordinary-model-owner-lifetime-GREEN-20261010.json).

Final-source policy58 and unit/lifecycle22 nodes now also pass, zero failures/
skips/OOM. Their independent two-stage window took182.309 seconds, with all
eight exact resources confirmed absent and original package20m retained. This
adds those current-source gates alongside model-author58; ordinary, native and
derived failures below remain unaccepted and under repair.

Remaining regressions on `e90ba8b6` expose gaps beyond the previous selected
passes. Model authoring/memory/body passes all58 nodes, including genuine model
producers and both databases. Ordinary HTTP passes4/10: all legal current/
done_open cases fail at final material checks despite actual provider call/join.
Source analysis identifies premature model cancellation at lineage completion;
a cancellation-preserving owner-lifetime repair and exact diagnostic
are prepared but unexecuted. Derived passes0/26: productive B cases stop at
the real five-minute subject cadence instead of the fixture7s relay bound;
partial Handle has an independent record-not-found failure. Native passes34/55
and is unaccepted; resident-block effects, policy-origin setup and proof hash oracles
need source-backed repairs. Failed owners are stopped and their raw/volumes/
networks retained. See the [remaining regression evidence](evidence/memory-final-remaining-regressions-20261010.json).

The embedding implementation now has an isolated genuine remote model recipe
and held client source draft. Exact input/result/vector receipts and service
backfill are being implemented separately; no format/compile/HTTP or functional
embedding acceptance is claimed yet.

Source-identical test child `e90ba8b6` now passes all32 additional nodes: all12
current-bound original workflows per SQLite/PostgreSQL (26 nodes), and exact
two-cluster prompts plus later-cluster whole-transaction rollback (six nodes).
The window took264.265 seconds, zero failures/skips/OOM, under unchanged20m
package and10s worker bounds. Genuine producer/DI/outbox scheduling honors
actual NotBefore and RelayOne; prior context is established by true completed
Handle/checkpoint. Later-cluster refusal is checked after actual first-cluster
SQL writes, with complete rollback. Production bytes match `54ce8e73`; the
original unbound515 function stays unchanged and is not declared recovered.
All eight successful identities are independently absent. The first fixture
compile missed an assertion import; it ran no business tests and its failed
SQL owner/three volumes/network/raw remain. See the [workflow and multi-cluster evidence](evidence/memory-current-workflow-multicluster-20261010.json) and [test proposal](../integration/proposals/weknora-original-memory-workflow-tests-20261010.json).

The [reviewable original-memory proposal](../integration/proposals/weknora-original-memory-20261010.json)
now contains the exact source patch from RAG `1bbd0cc8` to `54ce8e73`; its
SHA/tree round-trip is verified. It is kept separate from the adopted candidate
manifest. Source-identical test children now exercise all12 current-bound
original workflows and two-cluster prompt/rollback boundaries; their32-node passing scope is recorded above. The [remaining functional
contracts](evidence/memory-functional-remaining-contracts-20261010.json) identify
embedding/backfill/recall and model variants that still need genuine original
inputs/results and complete lifetimes.

Final private source `54ce8e73` now passes201 test/subtest nodes across12
serial component stages, zero failures/skips/OOM, with all182 mandatory
parent/leaf pairs observed RUN/PASS. The combined window took511.151 seconds
under a fixed20-minute dual clock bound; original package20m/HTTP8s/race3s
bounds remain. Current-policy rollback, productive consolidation, stale
maintenance, SDK/caller/worker/topic/actual OTLP span privacy, production SQL
bound-variable logging, and the earlier82 model/DAO nodes all pass together.
All18 exact successful identities are independently absent. The complete Go
package selection compiled156 packages:116 test-bearing PASS and40 package-only
SKIP with actual no-test-files output; no business tests ran or skipped. The
first wrapper miscounted no-test packages despite actual Go Exit0; its stopped
actor/temp/raw are preserved. A corrected rerun passed in16.124 seconds and
removed its two exact resources. Eight specified failed SQL owners remain
stopped with24 volumes and eight networks. See the [final component
record](evidence/memory-final-integrated-components-20261010.json).

This accepts those selected components. RootTrace/direct Generation/opaque
metadata, SQL literals/error bodies/independent handles, complete original
workflows, multiple clusters/final ancestor TTL, model embedding variants,
full migrations/broker/normal application/restore and V1 remain open.

The byte-identical strict policy diagnostic now passes all26 stale-maintenance
nodes on final private source `54ce8e73`, zero failures/skips/OOM. Both databases
still prove real policy2→3 replacement and model1, but the old call is rejected
with zero publication/receipt increments and importance5 unchanged. The14-line
production check locks tenant before actor/control and compares actual current
policy revision/proof in the same transaction. Legal maintenance/no-candidate/
model-decline/merge and original rollback assertions pass. Final source parsing
covered168 Go files;167 formatted outputs were applied while preserving the
one matched diagnostic byte-for-byte, and10 selected packages compiled.
The broader combined window passed as recorded above. See the [matched current-policy GREEN](evidence/memory-generated-policy-successor-GREEN-20261010.json).

A stricter test-only stale diagnostic on `e27ef96b` confirms an actual
generated-policy commit defect on both databases: real current policy proof
changed from revision2 to3 during model1; the old call still published one
derived record and one receipt and demoted importance5 to1 with nil error.
Those genuine before/after proof and fault/model assertions executed before
the unchanged expected rejection. The selection passed22 nodes and failed4
(two policy leaves and parents), zero skips/OOM. The metadata-only old-fact
oracle now lets both purged-body rollback cases pass. A same-transaction
current-policy comparison is being repaired; the gate remains unaccepted.
See the [policy successor RED](evidence/memory-generated-policy-successor-RED-20261010.json).

Generated consolidation now passed all 16 selected nodes on `7aff9c5f`,
zero failures/skips, in 133.810 seconds including preparation and cleanup.
Its retained `a01f9c40` baseline failed at fixture admission, repaired only
by retaining the genuine model lifetime reader. Legal original-bound merge
and post-model source/body/actor/input/model/invalid-result refusals passed.
Multi-cluster atomicity and final ancestor clock boundaries remain separate.

Stale-task maintenance on the same source passed 20 of 26 nodes, with six
failures including both parents and no skips/OOM. Both policy-change leaves
unexpectedly returned nil; both purged-body leaves failed at the final retained
fact read. Their exact production/fixture cause is under review. This gate is
unaccepted; its SQL owner, volumes and raw logs are stopped and retained.
See the [generated/stale record](evidence/memory-generated-stale-components-20261010.json).

Private integrated source `7aff9c5f` now passed all 82 selected component
nodes, zero failures/skips, in 200.643 seconds including preparation and cleanup.
This adds actual two-call first-create concurrency, repeated-subject/error/cancel
behavior, current/history model lifetimes and one-pool checks, captured provider
configuration, real HTTP wrapping and whole-result refusal. All required parents
and exact leaves were observed RUN/PASS. The original 20-minute package timeout,
eight-second HTTP bound and three-second race barrier were retained. Prior
formatting parsed 162 changed Go files; all nine selected packages compiled,
with zero runtime test nodes and the old515 business assertions byte-identical.
See the [integrated component record](evidence/memory-integrated-model-components-20261010.json).

The generated consolidation baseline on `a01f9c40` failed all 16 nodes before
the first model call: its model fixture omitted the genuine lifetime reader,
so Schedule returned before a pending row existed. This is a retained setup
failure, not a consolidation business RED. The isolated fixture repair supplies
the actual configured service capability. Its selected business rerun passed as recorded above.
Worker/topic-log and enabled telemetry privacy follow-ons, full consolidation,
stale maintenance, final ancestor TTL, full original workflows/migrations,
durable broker, normal application/restore and V1 acceptance remain pending.

The complete original service diagnostic on `1bbd0cc8` passed all 604 test
nodes and 178 parents, including all 28 required original service parents,
with zero failures or test skips. The original eight-second HTTP bound,
20-minute package timeout and count one were retained. Package JSON reports
905.111 seconds; compilation, test and attach took 957.246 seconds; the complete
driver took 1057.413 seconds. Both ordinary HTTP parents passed with their
original subsequent assertions present; no individual assertion trace was
captured. Original binaries and profiles are independently hashed. All nine
owned resources were removed and their absence verified. See the
[whole-service profiling record](evidence/ordinary-qa-whole-service-profiling-20261010.json).

A foreign runtime was observed during that timed run and subsequently
disappeared. This result does not establish strict isolation or explain old
CI failures. It accepts this service diagnostic only; full 42/new 47 selections,
normal application, restore and V1 acceptance remain pending for the current
proposals. The earlier storage preflight is retained as history below.

Private repaired source `ef58c603` now passed the matched repeated-subject
diagnostic on both SQLite and PostgreSQL: all ten nodes, no failures/skips.
The exact same tests failed before the sole EnsureSubject DAO change. The
original worker final-clock selection also passed all six nodes: model1 legal
commit and late actual current-model lease expiry rollback on both databases.
Its effect/checkpoint/cursor and actual final SQL hook assertions passed under
unchanged bounds. Total preparation, both stages and cleanup took 94.420 seconds.
All eight exact owned identities are absent; previous failed SQL owners/volumes
and the compile failure actor/temp are retained.

This accepts repeated-subject idempotence and current-model final-TTL component
behavior. First-create concurrency, all ancestor TTL variants, full model/
ordinary HTTP/derived/consolidation/privacy, migrations, durable broker, normal
application and restore remain pending. New extra DAO tests are source drafts
and do not expand this passing scope. See the
[matched GREEN and worker TTL record](evidence/memory-subject-idempotence-GREEN-20261010.json).

A test-only diagnostic on `fe26fa24` now confirms a repeated-subject
initialization defect on both SQLite and PostgreSQL: four model-service/
admissions lifetime leaves passed Begin/Snapshot/Recheck/Close; four scheduling
leaves passed existing-subject/enabled/config/actor checks, then repeated
EnsureSubject returned record-not-found. The resulting ten-node selection
passed four and failed six (including both parents), with zero skips/OOM.
The DAO's no-op upsert supplies a new UUID to a body-capture callback, which
looks for that uninserted row. This is a genuine subject-idempotence RED, not
a worker TTL RED. A guarded idempotence/concurrency repair is under development.
The failed owner is stopped with its three volumes/network/raw preserved.
See the [subject failure record](evidence/memory-subject-idempotence-RED-20261010.json).

Private memory source `856ea3b1` now compiles the final worker lease union and
original-bound consolidation across all six selected packages, with zero
runtime tests. All 121 changed Go files parsed/formatted successfully. The first
compile failed on one unused import; removing it passed in 96.385 seconds.
Successful owned resources are absent, while the first failure actor/temp remain.
Twelve host SQLite schema-only checks passed the fused guard: 17 fact tables
are checked before any DROP, mutated controls above seed revision one are
retained, and UNKNOWN seed rows still allow empty DOWN. Revision one cannot
distinguish initialization from a first raw insert. No full journal/chain or
business proof is claimed from these schema checks.

The first actual SQLite/PostgreSQL worker gate failed all six nodes: its four
leaves found no pending extraction row after ScheduleExtraction, before any
model call or final-clock hook. The gate is unaccepted and is not a TTL business
RED. Its Go is absent; stopped PG/Redis, three volumes, network and raw/source
are retained. A ten-node test-only enqueue diagnostic is prepared. Independent
stale-maintenance and model-SDK privacy repairs are also fused at `35c80847`,
but remain unformatted/uncompiled/unexecuted. RAG caller logs, embedding, full
workflows/migrations/broker/restore and V1 acceptance remain open. See the
[consolidation and enqueue prelude record](evidence/memory-consolidation-enqueue-prelude-20261010.json).

Private formatted memory source `2e088198` now passed parsing/formatting
on all 102 changed Go files and compiled all six selected packages, including
chat constructor tests, with zero runtime test nodes and no failures/skips/OOM.
65 files changed only through formatting; the original 515 business function
remained byte-identical. Compilation took 118.762 seconds; formatting and
compilation completed naturally in 121.787 seconds. All three owned identities
(two Go actors and one temporary volume) are absent. Raw logs and frozen
sources remain. Independent review found a cross-process clock-epoch flaw in
the executed wrapper: its passing natural duration does not prove strict
480-second outer-timeout enforcement; future drivers are being repaired.

Final all-hold TTL validation also remains incomplete: policy/current-model
and derived ancestor policy/historical-model holds need a final union check
after subsequent SQL IO. Compilation accepts types/wiring only. Actual new
model/derived/whole-result gates, full legacy workflows, migrations, broker,
normal application and restore remain pending. See the
[format and compilation record](evidence/model-whole-result-compilation-20261010.json).

The latest private memory source is `674e77cc`, integrating model lifetime
repairs with whole-result validation. Unsupported or invalid model decisions
now reject the complete result before writes; explicit NoOp and proven
snapshot duplicate/tombstone suppression remain. Two new SQLite/PostgreSQL
parents contain four mixed-valid-invalid cases (six declared nodes), with
genuine scheduling, dispatch and Handle plus zero-effect/checkpoint oracles.
A test-only baseline has identical tests for a future matched regression.
These are source checks only: neither baseline nor fix has executed, and the
fused source is still unformatted/uncompiled. The complete core patch again
round-trips to the exact base. See the
[whole-result source record](evidence/memory-whole-result-source-20261010.json).

Model provider lifetime repairs are now integrated privately at `dd7f6e24`.
The source captures complete remote protocol/URL/query/header/auth/settings
recipes, reauthorizes the current actor in the same SQL transaction, retains
original model bodies through ordinary message completion, and cancels and
joins the actual provider stream. It fixes unconsumed-stream Close and the
single-connection SQL pool lock ordering; 29 new cases in eight parent tests
are source drafts only. The full core patch round-trips exactly, and the old
515 business test function is byte-identical. Formatting, compilation and
all new business execution are pending. Whole-result invalid decision handling
is also under repair; builtin/YAML/Ollama/embedding and full existing-memory
workflows remain required. See the
[model provider lifetime source record](evidence/model-provider-lifetime-source-20261010.json).

Model/policy/derived-memory source is now fused privately, with the latest
test-adapter integration at `b1c24809`. Both sets of original body publications,
execution proof fields and pending schema modules are preserved. A complete
core patch round-trips to its exact base tree. Six host SQLite schema checks
passed: empty DOWN succeeds; four representative invalid schema facts refuse
before any DROP/ALTER, with all other modules retained. All 14 fact tables are
listed in the upfront guard, not claimed as 14 individually executed cases.
These sources have no formatting, compilation or business acceptance. Model
catalog/default fallback and current actor/provider lifetime defects remain
under repair; legitimate ordinary/builtin/embedding and complete old-memory
flows remain required. See the [integrated source record](evidence/memory-model-derived-integration-source-20261010.json).

Original-policy source `f9375193` now passed the complete six-parent SQLite/
PostgreSQL component selection: all 58 nodes (52 leaves), zero failures or
skips, in 151.950 seconds. Every node ran and passed once. A separate two-parent
authoring stage passed 20 nodes in 47.711 seconds; those nodes are included in
the full 58 and are not added to its count. The earlier ACK fixture repair now
uses the actual emitted queue payload, and both database rollback cases verify
that the hook ran, the owned context was canceled and all original state rolled
back. Production checks and business expectations are unchanged. The complete
prepare/authoring/component window finished naturally in 204.080 seconds under
its planned coordinated 300-second window, retaining the original 20-minute package
timeout. All eight exact owned runtime identities are absent; earlier failed
volumes, raw evidence and source are retained. This accepts the policy component
slice; old full legal memory, model/derived workflows, full migrations, real
broker and restore remain pending. See the
[policy SQL pass record](evidence/memory-policy-SQL-pass-20261010.json).

The preceding `862fd486` run remains recorded as 48/58 because its test-created
ACK payload omitted Language after successful Handle/model/item/checkpoint
checks. Its failed volumes and raw evidence remain retained. See the
[historical policy SQL partial record](evidence/memory-policy-SQL-partial-20261010.json).

Integrated original-policy source `862fd486` now compiled all five selected
packages successfully in 115.442 seconds, with no OOM or compiler errors.
This `-run ^$` check executed no business tests. Its owned actor and temporary
volume are confirmed absent; raw output and source remain retained. Genuine
policy authoring/successor/body lease tests and model/derived integration are
next. See the [policy compilation record](evidence/memory-policy-compilation-20261010.json).

Private source `a8c2a303` now passed the complete 19-parent native SQLite/
PostgreSQL component selection: all 55 nodes, no failures, skips or OOM, in
285.993 seconds. A separate focused run also passed both shared-Agent cases,
including actual source9 permission withdrawal and PostgreSQL epoch blocking.
The repair configures the same transaction-bound shared-resource readers as
production. All eight exact owned runtime identities are now absent; earlier
failed volumes and raw evidence remain retained. The 16-parent/22-node unit
result stays attributed to `a35ab0c3`; its production/unit bytes are unchanged.
This accepts the native component slice only. Complete custom policy/model,
existing-memory workflows, full migrations, real broker and restore remain
pending. See the [native component pass](evidence/memory-native-component-pass-20261010.json).

Repaired private source `a35ab0c3` passed all 16 selected unit/lifecycle parents
and 22 test nodes across three packages, without failures or skips. Its SQLite/
PostgreSQL component run passed 53 of 55 nodes; the PostgreSQL cross-input
checkpoint path now passed. Both shared-Agent cases still stopped at fixture
message hydration because the transaction authority lacks its Agent share
service, before memory scheduling/model execution. Full native acceptance
remains pending; failed runtime volumes are retained. Policy source `2a847047`
also passed formatting/parsing on 19 changed Go files, without compilation or
business execution. See the
[repaired component and unit record](evidence/memory-repaired-components-and-units-20261010.json).

A separate original-policy proposal is integrated as private source `71a1911b`.
It adds authorized full-policy authoring, immutable origin/body receipts,
retained version controls, real successor binding and owned policy body leases.
The integration preserves the native fixture repairs and fixes an inherited
context marker in the rollback test. Seven host SQLite fragment checks passed
for UNKNOWN initialization, ABA/delete-recreate versions, immutable controls
and DOWN guards. They used no full body schema, Go, PostgreSQL or model calls.
The source is not formatted, compiled, business-tested or adopted. Model
originals and complete existing-memory workflows remain under development;
mutable settings views are separate from the policy lifetime reader. See the
[policy source and schema record](evidence/memory-policy-original-source-20261010.json).

The original-memory SQL proposal `fe254893` now passed its native admission
selection on both SQLite and PostgreSQL: six nodes, zero failures or skips.
The subsequent 19-parent component run passed 52 of 55 nodes and failed three,
with no OOM or skip. Whole-result commits, capacity rollback, durable intent
relay, queued policy rejection, configured single-pool reads and public writer
concurrency passed. PostgreSQL cross-input cursor comparison and both shared
Agent fixtures remain under repair; the complete component stage is unaccepted.
The first attempt exposed a missing permission epoch schema in its fixture;
adding its empty schema introduced no grants or original facts. Failed runtime
volumes and private raw evidence are retained. Unit/lifecycle, full legal memory,
broker and restore gates remain pending. See the
[memory SQL component record](evidence/memory-native-components-20261010.json).

The private original-memory format checkpoint is `652df78f`, tree `4e523143`,
with atomic output/capacity receipts, durable recovery, Lite worker shutdown and
shared permission/public mutation fences integrated. Formatting and parsing
passed, but its subsequent actual compilation exited 1 on two lineage-interface
wiring errors; no business tests ran, and its runtime owner was cleaned up.
Current source `5e071eef`, tree `a2ceaa09`, corrects those interfaces and the
transaction-bound KnowledgeBase service wiring. Its second compilation passed
all five selected packages in 212.012 seconds, with zero test skips and zero
business tests (`-run ^$`). Its owned compile actor and temporary volume were
removed while shared caches remained. This accepts compilation only; complete
derived-memory workflows, remaining writer/permission paths and business gates
still require work. The candidate manifest is unchanged. See the
[memory compilation record](evidence/memory-integrated-compilation-20261010.json).
The [service evidence](evidence/ordinary-qa-whole-service-profiling-20261010.json)
retains the historical memory format checkpoint separately from its tested
service source.

An earlier original-memory atomic-result and recovery integration froze
private source proposal `386a68a5`, with four overlapping files resolved
and all additional SQL test sources bound to its exact tree. A private index
applies its patch to `1bbd0cc8` and reverses it to the exact original tree.
Six in-memory SQLite checks passed on its fused pending schema; a separate
13-case schema-only probe checked foreign keys, immutable facts and DOWN guards.
These checks executed no model, Go, PostgreSQL, broker or restore cases. The
formatting, fence and capacity gaps described by that earlier record have
subsequent source work above; complete workflows and actual acceptance remain
pending. This historical proposal is not in the candidate manifest. See the
[integrated source record](evidence/memory-original-integrated-source-20261010.json).

Memory task source preparation now persists an original-bound dispatch intent
with the admission and pending revision in one transaction. Its recovery relay
retains that exact intent after broker or post-commit failures, while completion
still requires an immutable successful receipt. A separate Lite executor change
cancels and joins its workers and refuses new tasks after shutdown. Fixed-image
source parsing/formatting passed; 14 test declarations have not run, and no
compilation, SQL, broker or restore acceptance is claimed. These earlier changes
were frozen on private source `ca5f9233` and are included in the newer integration
above; full legal memory workflows and actual gates remain pending. They are
not in the current candidate manifest. See the [memory source preparation record](evidence/memory-original-source-preparation-20261010.json).

The first whole-service attempt stopped at storage preflight: host free space
was 10.380 GiB against its fixed 12 GiB diagnostic floor. It created no runtime
resources and ran no business tests; all nine prospective names were verified
absent, and 24 pure driver checks passed. This is a historical attempt, not a
current blocker for the completed service diagnostic above. See the
[preflight record](evidence/ordinary-qa-whole-service-preflight-20261010.json).

A fresh source-exact PostgreSQL ordinary-QA profiling run on `1bbd0cc8`
passed its single required parent under the original eight-second HTTP and
20-minute package limits. The original binary, CPU/block/mutex profiles and
raw output are source-bound and independently hashed. This isolated run did
not reproduce the four a38 Linux-CI body deadlines; it does not accept the
full test selection. The complete selected service diagnostic above has now
passed under its original cumulative package budget. See the [focused profiling record](evidence/ordinary-qa-fresh-focused-profiling-20261010.json).

The proposed directory-publication correction reads the unique user binding,
current directory, identity and groups in one consistent transaction. Its
matched SQLite/PostgreSQL regressions passed 16 nodes, the existing access
selection passed 42, and the real open-provider HTTP selection passed 10 with
actual cancellation and released leases on revocation. Seven PostgreSQL
transaction-overlap nodes also passed. The C6 mirror passed 62 component nodes;
both final source trees compiled 155 packages with zero runtime tests.
The exact test owner, network and both disk volumes were removed while sources,
raw logs and shared caches remained. These are component results on proposed
RAG `853c19cc` / C6 `7e1d5ce4`; the current manifest still pins `1bbd0cc8` /
`b43afa53`. The new full 47-parent selection, normal images, application replay
and restore remain pending. See the [atomic directory component record](evidence/directory-atomic-components-20261010.json).

The current development candidates are RAG `1bbd0cc8` and C6 `b43afa53`. Root verified the final RAG source's 50 ordinary-HTTP nodes across
10 parents, 59 session-terminal nodes and all 1177 selected nodes, including
all 42 mandatory parents. The full selection finished in 1020.933 seconds
within its original 20-minute package limit, with no failures or test skips.
Full compilation covered 155 packages: 115 with tests and 40 without, and
executed zero runtime tests. On its exact final source, C6 also passed 50
ordinary-HTTP nodes / 10 parents and 59 session-terminal nodes, and its two
source-bound CLI builds passed with verified bytes, mode and single link.
C6's full selection also passed all 1177 nodes and 42 mandatory parents in
1007.649 seconds, with zero failures or test skips. Its package metadata has
11 passed packages and one without tests; that no-test package is not a skipped
test. C6 full compilation also passed for 155 packages: 115 test packages and
40 no-test metadata records, with zero runtime tests. Final exact owner cleanup
removed the test containers, network and disk volume and released the heavy
window while preserving private logs, source and shared caches. These results
permit adopting the exact pair as development candidates. The final RAG
ordinary AnyDoc app/UI build subsequently completed in 155.666 seconds from
the frozen `a38e152` runner; distinct immutable images and all five source/role
labels match the exact `1bbd0cc8` manifest. This records RAG image provenance,
not a C6 normal-image build. The fresh final-RAG owner passed up, bootstrap and
its permission matrix, then the actual running-window gate failed after 48.038
seconds. It produced no history receipt: QA2 and replay are unaccepted, and
restore did not execute. Pending Auto restore and P5 still need actual
acceptance; V1 remains incomplete.
All eleven owned containers subsequently stopped with their volumes and network
retained; the watcher exited with terminal 1 after the stop sequence interrupted
a Docker CLI query. Its fourteen samples and removed marker are preserved,
without accepting that observer exit as success. The application recorded a
directory snapshot denial during stream publication; its exact rejecting
predicate and the separate snapshot-read race still need causal verification.

The empty-stream correction reduced successful renewals of eight held bodies
from 80 to eight in the matched poll window. Actual source revocation stops
the model in both databases. Separate late actor-deactivation and body-lease
expiry regressions verified actual provider cancellation, service return,
released pins and zero completed rows/manifests. Earlier failed preparation
and causal baselines retain their own source identities; the earlier 44/59
component gates on `3f327fc3` are not attributed to the final source. The
[final-gate record](evidence/ordinary-qa-idle-material-final-gates-20261010.json)
records the completed two-profile source gates and cleanup. The new
[normal-image/controller record](evidence/ordinary-qa-final-normal-images-controller-20261010.json)
accepts the final RAG image build and records the subsequent normal-running
failure separately. QA2/replay remain unaccepted; full restore, pending Auto
restore and P5 are pending.

Integration head `9df5dfc` tested the preceding `1ab47546`/`aac58fa7` candidates.
Both push and PR CI failed their candidate producer jobs at actual ordinary
HTTP's unchanged eight-second termination assertion. Eight other jobs passed.
Three service packages later exhausted their cumulative 20-minute limit while
running different boundary cases; PR RAG ended failed without a timeout panic.
These runs did not test the new final idle sources and establish no observer
Python or Docker-pull terminal failure. Their original ZIP/log hashes and
exact failure nodes remain in the final-gate record. Earlier `06811da` CI
failures, including independently proved image-pull quota and direct-upgrade
startup failures with missing diagnostics, remain in their separate
[historical CI record](evidence/ci-06811da-failures-20261010.json). Neither CI
record is application acceptance; no deadline or safety assertion was relaxed.

The first final-source build was interrupted with actual BuildKit `canceled` /
`context canceled`; no successful driver result or images were inferred. Its
original logs and cancellation receipt remain separate from the fresh R2 build.
The private controller also retained a pure fake-child regression: an actual
fast terminal could race its PID/start lookup before phase recording. The
minimal correction records the real exit, refuses still-live unknown identity
and retains all PID/start/group fences. Eight simulated old/new cases passed;
these are not normal application tests. The subsequent normal lane ended with
actual failure: its single HTTP reader returned 200, two events and EOF, while
the required running window was not observed before the deadline. Those
transport facts do not certify an answer or completed QA2. Reader threads joined
and the running marker was removed. Guarded stop returned 0 and recorded all 11
owned services stopped; volumes/CURRENT preservation has a separate independent
audit. The observer ended 1 with 14 samples and its marker removed; its exact
cause is under review and is not asserted as the running-window failure's cause.

The preceding manifest candidates were C6 `aac58fa7` and RAG `1ab47546`;
the current manifest pins C6 `b43afa53` and RAG `1bbd0cc8`.
Use the [latest candidate record](weknora-development-candidates.md) and its
frozen evidence for current implementation facts; the table below is historical.
The preceding ordinary AnyDoc app/UI images for RAG `1ab47546` built from
the frozen integration runner `06811da` in 324.927 seconds. Their distinct
immutable image IDs and all five source/role labels match the private frozen
manifest. The first attempt stopped before Docker build because the host had
6.2 GiB free; exact retired private compile-cache cleanup restored 32.06 GiB
while preserving source, failed logs and original results. The existing
16 GiB build minimum was retained. The [actual image record](evidence/ordinary-qa-normal-anydoc-images-20261010.json)
accepts only the build and image provenance; fresh normal QA, running replay,
dual-database restore and P5 still require actual acceptance.
The readonly observer now diagnoses the exact kind and names involved in
disagreeing prefix inventories, verifies current resources and limits complete
rescans to three. Mutation helpers still reject the same mismatch immediately.
[Ninety-one offline preparation checks](evidence/resource-watch-prefix-snapshot-preparation-20261010.json)
passed without skipped tests; a fresh actual startup must still verify this fix.
The two [fresh startup failures](evidence/ordinary-qa-normal-startup-failures-20261010.json) remain unaccepted and ran no QA or restore.
The first stopped before resources; the second observer recorded zero samples and removed its marker, and all owned services stopped with volumes/CURRENT preserved. Its exact unlabelled-prefix resource remains unrecorded.
The preceding normal AnyDoc app/UI build for RAG `595f241f` completed; its immutable IDs and
source labels were verified. A new owned nested-LDAP environment passed startup,
signed ingestion and the permission matrix, but its first actual QA failed:
the references producer emitted no checked source lineage, so the strict stream
consumer refused it and retained an unknown prefix. No two-round baseline or
restore acceptance was created. All owned services were stopped with volumes,
controls, originals and CURRENT anchors preserved. The observer also failed
during the startup resource inventory; its exact Docker failure is not yet proved.
The `63e03f2` full push/PR CI failed despite successful isolated checks: the
service package exhausted its cumulative 20-minute budget, while a retained-source
HTTP sender did not finish in time. Title-control regressions passed. These
[actual failure records](evidence/normal-application-ci-failure-20261010.json)
remain unaccepted and are being corrected without removing required tests.

The resource observer now bounds complete readonly ownership rescans when an
exact allowed one-off inspection reports that its container disappeared. All
ordinary container, owner, image and budget mismatches still refuse, and the
mutation helpers are unchanged. Safe command diagnostics avoid losing the
next failure's cause. [Nineteen independent preparation checks](evidence/resource-watch-transition-preparation-20261010.json)
passed; the earlier exact command and the suspected startup race remain
unproved, and the next fresh startup must verify the observer in practice.

The ordinary QA correction now carries checked lineage on progress, references
and fixed fallback, propagates asynchronous publication errors, and waits for
the owned producer before releasing its last material holder. Successful Done
ends the producer even when the provider keeps its channel open. Actual
PostgreSQL/SQLite boundary tests passed 28 nodes. The final combined sources,
including HTTP-caller disconnect, provider-error/EOF and real schema-probe
cancellation regressions, passed 77 focused nodes per profile with no failures
or test skips. The original caller-disconnect test sends normal model Done
after HTTP cancellation; it does not establish producer-context cancellation.
That early completed-state assertion is now replaced by actual service-return,
HTTP-handler-return and detached stop-watcher-exit observations. The strengthened
real StopSession test verifies provider cancellation, every completed write
being refused by the original-material boundary, released leases and final
completed/manifest counts. It also verifies legitimate background completion
after HTTP disconnect. The inline-body checker now
checks all heads once per row and rechecks the original head set, journal pin
and cancellation at the return boundary, including a real canceled schema
probe without changing actual absent-schema compatibility. Its 22 row-boundary
nodes passed, and
actual cancellation during an actual journal-key file read failed on the old
source and passed on the correction in both databases. A matched 20-node
parent-copy run fell from 27.71 to 22.90 seconds for SQLite and 57.13 to 43.03
seconds for PostgreSQL; this single comparison is not P5 acceptance.
Root independently verified the [raw-log records](evidence/ordinary-qa-body-combined-boundaries-20261010.json).
The corrected sources are now the manifest candidates. The complete selection
and compile records remain labelled with their tested production commits
`48ce1d3c`/`0388c309`; the final candidate trees add only three test files,
whose full ordinary QA target passed 38 nodes per profile. Fresh normal
application QA/restore still needs actual acceptance. CI now explicitly selects ordinary QA
and requires all twelve new test parents without changing its timeout.

The first final RAG full selection ended within its original 20-minute budget
with 1155 passed nodes, 8 failed nodes and no skipped tests. The owned PostgreSQL
cluster recorded OOM and a recovery window while its 512MiB PGDATA tmpfs was
96% occupied. Seven actual cases failed before their business assertions with
EOF or SQLSTATE 57P03; one additional node aggregates a child failure. Root
independently verified the [failed full-gate record](evidence/ordinary-qa-full-gate-PG-OOM-20261010.json),
including the exact owner, image, memory limit and cgroup OOM event. This run is
not accepted. A fresh owned disk-backed PGDATA fixture will rerun the complete
selection with the same memory/CPU budgets, timeout and required tests; the
failed logs, diagnostics and binaries remain preserved.

The fresh disk-backed RAG full selection subsequently passed all 1163 test
nodes with zero failures or skipped tests, including all 38 mandatory parents.
The same frozen `48ce1d3c` source also passed 59 session-terminal nodes and 35
local-derived nodes, plus compilation of 155 packages (115 test packages and
40 packages with no tests). Root independently verified raw hashes, actual
required terminals, unchanged before/after source identity and removed owned
Go containers in the [disk-gate record](evidence/ordinary-qa-full-disk-gates-20261010.json).
C6 `0388c309` subsequently passed the same 1163 selected nodes, all 38 mandatory
parents, 59 session-terminal nodes, 35 local-derived nodes and compilation of
155 packages. Root verified the corresponding raw records and exact source
identities too. The first strengthened StopSession test run had 12 fixture
failures: its older fixture lacked the real completion-manifest migration,
and its one-second model-start check preceded actual start. These are
preparation failures, not a business red or accepted cancellation test. The
fixture is being upgraded through the real forward migrations while preserving
the eight-second HTTP bound. The final strengthened tests subsequently passed on RAG `1ab47546` and C6
`aac58fa7`: 38 ordinary QA/boundary nodes per profile with no failures or skips.
The [real StopSession record](evidence/ordinary-QA-real-stop-chain-20261010.json)
keeps the original full-suite identities separate and proves that production
bytes did not change in the test supplement. Both patches reproduce and reverse
their exact baseline trees. These sources are promoted as development candidates.
The later ordinary image build is recorded above; application/restore and P5
remain unaccepted.

Normal AnyDoc images for the preceding `eb7d5756` source have been built and
their exact source labels verified. A fresh nested-LDAP application passed startup, signed ingestion and the
permission matrix. The early HTTP material-initialization 503 is fixed, but
first QA failed after Merge: a legitimate generated summary's parent
relationship was rejected as an unsupported input. The new candidates preserve
the summary's original proof and verify its own captured body and relationship;
actual PostgreSQL/SQLite regressions passed 42 RAG and 18 new C6 cases.
The latest normal image's broader QA failure is recorded above. Its corrective
source still needs normal application QA/history/replay, clean restore and
measured P5 acceptance.

Steer's original-material integration is now included, with PostgreSQL154 and
SQLite73 forward migrations. The actual HTTP input keeps its immutable author
and original run; consumption derives the next real user and completion inputs.
Exact controls and owned membership transitions are checked across the handoff,
and the successor waits for the preceding producer's real finalization.
Refused inputs cannot trigger automatic follow-up, and failed claims preserve
their evidence while denying consumption. Both frozen profiles passed 233
test nodes / 107 top-level tests with zero failures or skipped tests, plus full
compilation; [the recorded logs](evidence/steer-original-material-20261009.json)
were independently rechecked. Old request JSON/digests and unknown legacy
history remain unchanged. The new normal application run is still pending.

The `fc2682b` push CI completed all six jobs. Its actual retained logs were
rechecked for both profiles: 1043 selected producer/recovery test nodes, 59
session nodes, 35 local-derived nodes and 11 native-vector nodes passed per
profile, with zero failed or skipped selected tests and full compile for
155 packages. The required after, migration, JSON-contract and actual Redis
tests were confirmed present in the [CI evidence](evidence/steer-candidate-ci-20261009.json).
These checks do not accept the upcoming normal application or full restore.

Known old parent-body copies now include the immutable original dependencies of
later generated fields in their retention scope. Cleanup requires closed hosts,
retired source fences and the exact complete closure; unknown copies remain
retained. Own generated-field growth acquires and retains its additional KB and
source leases and rechecks withdrawal, epoch and cancellation before installing
new material. Agent QA also honors `disable_title`, fixing the observed CI
sender timeout without extending its deadline. The combined sources are now
included in this manifest. Each profile passed 414 distinct test nodes / 165
top-level tests, zero failures or test skips, and compilation of 155 packages.
Root independently verified the [raw-log evidence](evidence/parent-copy-title-original-material-20261009.json)
and resource cleanup. The subsequent normal images are verified, but application
acceptance failed as recorded above.
The historical local withdrawal inventory still needs immutable backend plans
and writer artifacts observed after current rows disappear; its prepared
forward migration remains untested and incomplete.
Independent review found that its pinned SQLite rollback fixtures copied only
UP files. A separate test-only preparation now also copies the original matching
DOWN files for Steer73 and inventory74, keeping all downgrade assertions and
production SQL unchanged. Its real upgrade/down execution, C6 mirror and the
complete withdrawal/ACK/retirement chain remain pending in the
[source-review record](evidence/remaining-consumer-inventory-source-review-20261010.json).

Candidate CI now checks that selected tests actually executed and completed,
including required HTTP, PostgreSQL, Redis and summary regressions. The
[evidence check](evidence/candidate-test-evidence-gate-20261009.json) was
verified against both preceding candidate CI artifacts and preserved real
failure logs. This does not establish full application or V1 acceptance.

The history/clean-restore probes now pin the actual producer identities,
assistant content and structured citations across live generation, saved
history, completed replay and restoration. They require successful terminals
and an actual two-round baseline. The
[criterion regressions](evidence/actual-history-criteria-20261009.json)
passed, while running replay and the next full application trial remain
unaccepted.

A fresh-fixture running replay driver is now prepared. It delays one actual
model HTTP response for at most 20 seconds, reads the same answer prefix and
citations concurrently through native replay, and checks the real unfinished
assistant before allowing completion. Its model code and opt-in configuration
are frozen with the fixture; the default delay is zero. Preparation regressions
are [recorded here](evidence/running-replay-preparation-20261009.json);
the actual running replay, midstream source revocation and full restore remain
unaccepted. Candidate CI's complete compile and frontend checks now have a
55-minute outer budget after an observed 35-minute timeout interrupted the C6
frontend step; the selected Go test timeout and assertions are unchanged.

The clean restore driver now restores the actual private control files and
requires a genuine Nextcloud `default_language` fault to change `config.php`.
The checkpoint must contain that exact file in its cold `nc-html` archive;
after full data restoration, PHP and `occ` must prove the original configuration
hash and value returned. The driver checks the same owner, source, candidate,
image, model and resource profile before and after restoring controls, and
excludes CURRENT body/publication anchors. [Preparation checks](evidence/configuration-restore-preparation-20261009.json)
passed 70 tests, including actual temporary-file restoration; the complete
application configuration fault and restore have not yet run.

The separate pending-Auto restore driver is now source-prepared. It pauses a
real final enrichment response, stops the owned broker, and requires an actual
undelivered SQL intent with both immutable input manifests before checkpointing.
It reuses the full database, files and configuration restore path, then checks
one genuine startup execution and no additional execution after restart.
Source, actor and model rejection each require an independent original checkpoint.
[Preparation evidence](evidence/pending-auto-restore-preparation-20261009.json)
records 89 independently rerun pure tests with zero skips. No broker fault,
full application restore or startup execution has yet been accepted.

The pending driver's separate `closed` phase can now precede `reopen` without
rewriting its evidence file. Each call repeats all current gates; an existing
receipt must match the freshly computed canonical bytes and pass private-file
checks. [File and flow regressions](evidence/pending-auto-closed-receipt-preparation-20261009.json)
passed 97 tests with zero skips, including changed current state and tampered
receipts which must refuse startup. These remain preparation checks.

The remaining V1 work includes the full normal QA/source-revocation loop;
clean and stale-publication recovery plus real pending-Auto fault recovery;
Steer and other exposed consumer original-material/lifetime coverage; old
parent body-copy closure and purge; complete indexed-source withdrawal
inventory, physical receipts and credential retirement; external derived GC
and capacity coverage; 100 independent latency samples and upload comparisons,
followed by the full AI workload pilot; fault/retry/rotation and operations UX;
and enterprise AD, real-model, deployment and recovery acceptance. Existing
failed-candidate retry and local selected-resource cleanup are implemented
components and must not be mistaken for these broader acceptance results.

Manual publication snapshots, explicit file replacement and delegated embedded
Q&A/SSO are V1.1; unrestricted private/shared/file-ACL expansion is product V2.
PRD §7.7's V2/V3 document revisions are V1 consistency requirements. Security
of already exposed history, Agent, download and other consumer paths remains
required in V1 regardless of later product phases.

## Historical implementation table

| PRD area | Implemented and checked locally | Remaining before V1 |
| --- | --- | --- |
| Docker and app | Nextcloud 34.0.4, PostgreSQL 16 and Redis 7 use separate volumes; the app installs and upgrades; Compose includes cron, separate five-second event sender and applied-status workers, and a mock embedding service. A deterministic app packaging script emits a runtime-only 0.4.24 archive and verifies its install layout. A disposable fresh Nextcloud stack installed the preceding 0.4.23 archive without a source bind mount, created the expected migration tables, and returned authenticated DAV 207. The local shared stack upgraded to 0.4.24; a fresh 0.4.24 install and direct 0.4.6-to-0.4.24 upgrade passed GitHub CI at `a134851`. The existing `weknora-ldap-local` app can use an image built from a fixed WeKnora commit plus this repository's patch. | Pin a production image and dependency matrix for both stacks; verify rollback and coordinated backup/restore. |
| Binding and source API | Administrator settings can create bounded, nonoverlapping folder bindings, stop or resume an entire binding, and withdraw or republish individual files. Stop preserves originals, machine keys and per-file exclusions while new authorization, manifest and content reads fail closed; resume revalidates the current root and emits a reconciliation hint. The service API supplies capabilities, paginated manifests, conditional file reads, ordered change hints, and current per-user source authorization with a live file ETag. The WeKnora repository serializes data-source writes on the owning knowledge base and rejects mixed or historical sources and documents when establishing a Nextcloud-dedicated knowledge base. Nextcloud and WeKnora now expose a headless two-phase source-pairing protocol for a new dedicated knowledge base, with a one-time operation key, exact signed commit, a paused pending source and idempotent retry. The operator CLI and focused Nextcloud HTTP, Go and SQLite checks cover this protocol. Local HTTP smoke exercises a 207-file manifest and authorization/event cases. Machine API requests use a canonical HMAC signature, timestamp, database-backed nonce and overlapping key rotation; replay, tampering and immediate revocation have local HTTP checks. Each machine key belongs to one binding, and cross-binding requests are denied. WeKnora requires an operator-approved destination, and transaction checks prevent a KB editor from repointing a stored token or reviving a cleared credential. A disposable cross-system fault smoke verifies lost-key recovery, exact pending abort, wrong-operation rejection and active-pair abort rejection. An active paired source completed a live key rotation and subsequent manual sync. Ordinary administrator DELETE returns HTTP 409 for any binding with pairing history and preserves its credentials. A signed empty-source retirement path now handles only new pairs with a durable never-touched proof; both sides must acknowledge the exact operation before Nextcloud retires its binding and keys. | Validate the live GUID proof against the target AD, production TLS and reverse proxy; test larger folders, source failures and rotation faults against the isolated live pair. PostgreSQL now has a first-stage indexed-pair withdrawal that pauses publication, revokes its event receiver and retains an append-only observed local inventory. It always reports inventory incomplete and never ACKs Nextcloud; a complete historical/external copy inventory, physical GC receipts and credential retirement remain open. The empty-source path leaves a paused WeKnora tombstone and does not clean indexed copies. |
| Change journal and connector | The Nextcloud outbox records file hints. A daily job retains at least 30 days and advances a transactional cursor floor; an old cursor returns 409. The WeKnora connector requires exactly one selected binding, consumes hints, periodically reconciles a full manifest, downloads with ETag checks, and requires two complete scans before treating absence as deletion. The fixed-baseline Go integration tests pass locally. The patch has a pair-scoped signed event receiver with a transactional inbox and receipt watermark. Its administrator API can provision, rotate and revoke a connection after live binding validation; the one-time secret is encrypted at rest. Nextcloud 0.4.24 stores a scoped encrypted sender credential, signs bounded batches, verifies durable-only 202 receipts, persists backoff or pause state, exposes an administrator retry for a paused sender without advancing its receipt cursor, and protects hints from retention until a signed same-scope applied watermark covers them. Local fault tests cover retry, false receipts, pause, rotation, redirect rejection and fairness across bindings; a synthetic cross-system run matched both receipt watermarks. The WeKnora dispatcher persists a leased queue intent, checks source inventory and instance identity, and serializes Nextcloud manual, scheduled and event sync admission through PostgreSQL/SQLite. A later paired-source run verified real queue acceptance and matching verified applied watermarks for both an upsert and a two-scan delete. | A 60-second Nextcloud background job now emits redacted, deduplicated log warnings for paused senders, old pending hints, failed or stale signed applied-status polling, and unavailable binding roots, with recovery logs; it executed in the local stack. External alert routing, complete item-level publication evidence, and recovery of uncertain manual/scheduled enqueue remain incomplete. The connector streams one downloaded file at a time only after a complete metadata manifest preflight and keeps the old cursor until all emits succeed. Metadata and one file body remain buffered; scoped event scans re-download touched files after a complete manifest preflight, while broad hints and the independent daily content audit re-download all importable files. There is no mid-scan resume. The dispatcher now polls every five seconds by default and the local sender runs every five seconds, but the end-to-end P95 10-second task latency and 10,000-file/100-GB performance targets remain unaccepted. An item already being written can finish after connection revocation; long-running and partial-failure acceptance remains. Event-connection HMAC rotation accepts only the immediately previous key for up to two minutes; a second rotation discards the oldest key, and revocation rejects both keys immediately. A sender that receives 401 still requires operator inspection. Active source machine-key rotation has a maximum 24-hour overlap after commit, ends it at final ACK, and supports crash retry; a finalized rotation invalidates any event connection pinned to the prior source config hash. A 202 receipt does not mean a hint has been applied. Connected hints can be pruned after the minimum retention window only when the verified applied watermark covers them, so cross-service restore still cannot assume full historical replay; a verified full manifest reconciliation remains necessary. The outbox is a hint feed, not a deletion authority. |
| Source identity and read guard | Nextcloud administrators register one-to-one AD objectGUID ↔ Nextcloud UID mappings only after a fresh LDAP GUID comparison; each authorization repeats that comparison. A local PHP contract and strict local-account HTTP denial passed. The synthetic authorization smoke passed only with an explicit, local-Compose-only identity bypass. The WeKnora patch requires an explicit linked web user, fresh directory state, exactly one selected binding, a live Nextcloud allow decision, and equality between the imported and current source ETag. Guarded Go tests cover search/RAG, Agent tools, direct knowledge/chunk/file/resource reads, history and live streams, embed/MCP/public routes, and derived-content restrictions. Missing identity, stale source, unsupported caller, or unavailable authorization fail closed. Local HTTP smokes check direct and group folder shares, group membership removal and restoration, share revocation, disabled account, mapping revocation and publication withdrawal against Nextcloud's live mount view. A separate synthetic Team folder probe on groupfolders 22.0.6 checks group access, a file-level advanced ACL deny and restoration, and group membership removal. | Prove both services use the same enterprise AD and verify real user, nested AD group, primary group and production Team folder ACL semantics. Complete a browser/API entry-point audit and a permission/revocation matrix with authorized and denied users. Conservative history rules may hide otherwise permitted mixed-source answers; existing presigned URLs require expiry planning. |
| Publication and version lifecycle | Manual withdrawal and binding-wide Stop publication block new Nextcloud source reads immediately. A changed source ETag prevents WeKnora from serving an older imported version through the guarded paths. Nextcloud provenance is persisted on imported resources, including a fail-closed legacy/unknown state. A durable per-source version row now stages an invisible candidate, publishes only the currently desired candidate after parse completion and a signed, exact source recheck, then probes again after commit; confirmed deletion is tombstoned. PostgreSQL migration 126 / SQLite migration 45 add an append-only observation for every subsequent local version-row write; pre-upgrade rows permanently carry `legacy_history_unknown`, and the sequence is not a complete external-copy inventory. Focused Go and Nextcloud HTTP tests cover the recheck. An isolated live file was published, overwritten with a new candidate, and tombstoned after deletion using the new image; signed HTTP fault injection now covers withdrawal and ETag change between publication probes; the second probe rejects both with published=false. A transient SQL marker between the local commit and final probe remains visible to direct SQL/vector readers, so all external read paths require live source authorization. Repository tests cover failed parsing, old-task completion and physical-row retention. Durable GC inventory, retry state and tenant-admin status are implemented. A leased transaction claim now releases registered, tenant-scoped local source objects only after their retention deadline and last binding disappears; provider-confirmed unlink is counted once. An isolated PostgreSQL 120→121 upgrade and two synthetic tombstone jobs confirmed two 37-byte local unlinks, zero remaining bindings and 74 fewer bytes under the local file volume. | Complete the PRD's cross-system document-revision and publication ledger, an atomic cross-system publication guarantee, production-scale failed-staging recovery, cancellation and version-aware history; clean derived indexes, chunks, graph/vector and cloud objects, 90-day history retention, metrics, and restore/replay tests. The two tested jobs remain blocked on derived indexes even though their local originals were collected. An exact-ID local chunk/embedding deletion helper remains dormant: selected retrieval, streaming and build paths now hold leases or write fences, but complete reader and external-writer coverage is still missing, so GC coverage remains denied by default. The [read/build lease gate](nextcloud-derived-gc-read-lease.md) records the concrete paths and race tests needed before enabling deletion. Migration 122/41 reopens older falsely collected jobs and retains a derived-index blocker for every historical Nextcloud GC job; chunk and local embedding IDs are inventoried, but physical vector/chunk cleanup remains unimplemented. The version row provides a narrow visibility barrier, not an atomic cross-system publication guarantee. |
| Product and operations | The administrator publication settings and focused HTTP/Go smoke tests exist. The admin page can inspect the latest local source-pairing state, install a one-time WeKnora event credential, inspect local delivery status and revoke its local sender. It displays durable received and separately verified applied watermarks, including the last check result. Separate operator CLIs prepare, retry, abort pending pairs and rotate source pairing credentials; pairing actions are not yet integrated into the admin page. A Files sidebar shows each readable file's source scope, withdrawal or binding-stopped state and modification time, with a configurable link to the WeKnora login only while source publication is open. It now accepts a signed, pair-scoped WeKnora status for the current file ETag and reports ready only for a completed, enabled publication of that ETag; Q&A authorization remains separate. The signature contract, source-side HTTP smoke and a live paired-status/withdrawal check pass. After a signed current-ETag `ready`, withdrawal immediately changed the sidebar result to `withdrawn / unverified`; the temporary event connection was revoked afterward. Administrator diagnostics show binding-root availability, retained source hints, explicit withdrawals, and configured senders' local pending outbox count/oldest age alongside their receipt and separately verified applied watermarks. These counters exclude unconfigured bindings and already received retained hints, and do not establish WeKnora task queue, synchronization, parsing, indexing or per-file publication health. An isolated Docker backup/restore drill verifies synthetic Nextcloud and WeKnora database, file and key fixtures without touching live volumes. | Enterprise failure/retry UX acceptance with real AD and pilot files, physical WeKnora copy cleanup after Stop, consumer-aware monitoring, load/fault tests for the PRD's 10,000-file/100-GB pilot assumption, coordinated restoration of the actual application stack and pilot acceptance. Personal embedded Q&A and SSO are V1.1 work. |

The WeKnora patch also exposes an administrator-only paired-source health GET.
It reports scoped event inbox watermarks and backlog, retained sync-log states,
and current candidate parsing counts. SQLite, PostgreSQL, handler authorization,
and router tests pass. These database observations do not count the shared
Redis task queue or prove that a file is indexed, published, or readable.

## 2026-09-30 integration update

Nextcloud app 0.4.29 was packaged as a runtime archive and upgraded in the
shared local Docker stack. Its LAN listener is `http://10.106.105.128:18082`;
`scripts/allow-lan-access.sh` sets the browser origin used for original-file
citations. The Files sidebar shows a question handoff only for a readable,
current, signed `ready` file. WeKnora's browser route and interactive
`ask-target` endpoint resolve that exact paired file after current personal
knowledge-base, group, source and ETag checks. The manifest keeps separate
machine content and browser Files URLs. Existing indexed files need reindexing
to acquire the latter citation metadata. A URL contract checks IPv4, bracketed
IPv6 and reverse-proxy path handling for original-file links.

The 0.4.29 runtime archive has SHA-256
`54f2de890d784b8f0296948209e8f9dfff0864be5b7fed2b199bb2e908947b5e`
and 73 verified runtime files. `occ upgrade` completed on the shared stack;
the installed app reports 0.4.29, maintenance mode is off, and both LAN entry
points returned HTTP 200. A fresh disposable Nextcloud stack installed that
archive, created migrations and returned authenticated DAV 207; it was cleaned
afterward.

Another isolated install used the packaged 0.4.29 app to verify an actual
WebDAV file across app disable, re-enable and Nextcloud restart. The test
read and overwrote the same file while the integration app was disabled,
then read the new exact bytes after restart. Its private app configuration
also survived the lifecycle. The disposable containers, volumes and network
were removed; this establishes local core Files availability through those
app operations, not behavior during an enterprise deployment.

A separate disposable `0.4.6` to `0.4.29` Nextcloud upgrade-recovery drill
failed deliberately in the final app migration (`occ upgrade` exit 5), after
the late migration table existed while maintenance mode remained on. Its cold,
matched PostgreSQL and HTML checkpoint restored the original instance ID,
file ID and exact WebDAV bytes,
binding configuration, publication-state row, enabled `0.4.6` app and live
administrator binding route. The database and file were both changed after
the checkpoint before restoring, proving the restored state replaced those
changes. The successful run used project `nc-upgrade-recovery-8dcc3a45` and
confirmed its containers, volumes and network were removed. Its checkpoint
archive SHA-256 values were
`62259c6fc67a98fde048f3ce6c3d37c3c41ee115c769a2a2186dad24c95fe8e5`
(PostgreSQL) and
`ef390b54cabb2012eae8e23d54f7f8537552c94307258084a72c7e948fd1aec5`
(HTML); the recovered file SHA-256 was
`366f8cde9b04c3fd66cd6004d0cd95836bb5dd3d6f63c1a41b112e3afc9bc55b`.
This is an isolated same-image rollback exercise, not cross-service recovery,
queued-event replay, a production migration rollback or an RPO/RTO result.
See [the runbook](isolated-nextcloud-upgrade-recovery.md).

The shared development stack's three configured binding roots were later found
in the Nextcloud trashbin, making administrator diagnostics report unavailable
roots. Before recovery, the Nextcloud database and its config/data volume were
archived under ignored `dist/backups/` with mode `0600` and verified SHA-256
digests. Restoring only those three exact trash items preserved file IDs 76,
6718 and 6720. Diagnostics then reported all binding roots available; the
`dev-published` signed manifest and its file 77 content returned HTTP 200.
The shared WeKnora source records still include a paused historical pair and
another missing data source, so this repair does not establish a fresh
cross-system sync or user question flow. Disposable paired fixtures provide
the isolated integration evidence below.

A fresh disposable direct-group OpenLDAP fixture paired and indexed one
synthetic file, then exercised the Files question link as a mapped user.
Alice received a file-scoped mock-model answer with a citation to her
Nextcloud original; Bob and a stale ETag were denied. Before revocation,
Alice's persisted history contained the answer marker and citation URL.
After removing her only group grant, the same pre-revocation JWT received no
direct document, chunk, preview, search or prior-answer content. The second
isolated run observed all denials about seven seconds after the mutation.
These are HTTP/API and synthetic-LDAP observations, not a browser-click test,
real-model quality assessment, enterprise AD permission matrix or revocation
SLA. The [fixture record](synthetic-ldap-compose.md) has the exact checks.

A separate fresh fixture removed only the Nextcloud Engineering folder share
after first recording an authorized answer and original-file citation. Alice
remained in the LDAP Engineering group and WeKnora's Engineering knowledge-base
grant remained active. Her pre-revocation JWT was then denied by Nextcloud
DAV/source authorization and by WeKnora's ask target, direct content, preview,
both search scopes and history display. The first synthetic poll observed the
denials about three seconds after share removal. This isolates the source-side
permission boundary; it is not a production Team Folder ACL or revocation SLA.

A further disposable fixture installed pinned groupfolders 22.0.6 on
Nextcloud 34.0.4 and paired a genuine Team Folder root. Alice and the owner
initially resolved the same file ID; Bob was denied. An advanced `-read` ACL
on only Alice's `acl-note.txt` then denied her original DAV file, signed source
authorization, Files status, ask target, direct content, searches and earlier
answer/citation through the old JWT. The first poll observed the denials about
four seconds after the ACL mutation. Her LDAP group membership, Team Folder
root access and WeKnora Engineering KB grant remained in place; the owner
still read the same original file. This covers one isolated real groupfolders
ACL path, not enterprise AD, production ACL configuration or an SLA.

Source-side diagnostics now separate retained hints awaiting a durable receipt
from those already received but still above the last verified applied watermark.
The admin page shows both counts and oldest hint ages. A fresh, successful
signed applied-status check plus a received-but-unapplied hint created at least
five minutes ago produces a redacted `application_overdue` warning. The age is
measured from hint creation, not receipt, and the counter does not prove an
individual file is parsed or ready. The PHP alert contract and the shared-stack
HTTP smoke passed, including anonymous, machine-token, administrator and
ordinary-user access checks. CI additionally exercises the delivery watermark
transitions and their diagnostics fields.

A separate disposable dual-service restore drill stopped both apps, created
PostgreSQL logical dumps and cold physical PostgreSQL/file-volume archives,
and checked their hashes. After the live file was deleted and two full scans
tombstoned it, the drill restored the older checkpoint while app ingress
remained stopped. It verified the original Nextcloud instance ID, pair
operation, root/file IDs and source ETag, then replayed the persisted
post-checkpoint deletion journal on Nextcloud before starting WeKnora. The
same old JWT still authenticated Alice but could not read stale document,
chunk, preview or search content before or after two reconciliation scans;
the final source row was a tombstone with no visible candidate. Checkpoint and
restoration took 30.159 and 63.946 seconds for this small synthetic fixture.
The script confirmed its own Compose containers, volumes and network were
removed. This is local recovery evidence, not a production RPO/RTO promise,
external vector/Wiki/object restore or a durable production replay ledger.
See [the drill and its limits](isolated-dual-service-restore.md).

An earlier pinned RAG build compiled the WeKnora app with `WITH_ANYDOC=1` and the
`anydoc` Go build tag from source commit
`e5cc3e4491ee10fb85e0c2ad79f1e3329826d02e`; both candidate images carry
the complete patch SHA-256
`a7d638ac36f2e64b5adf998cdd16a811c238de343a5ab8d001262fa8207c3961`.
An isolated, loopback-only dual-service fixture verified the candidate app
image `sha256:c8e8b10b275bc0abfe8446278132e35e4b87d79338d4f55c331cf4f84e42e551`:
AnyDoc was available, the synthetic PDF reached two ready chunks and two
embeddings with its protected text, Alice received a file-scoped answer with
an original Nextcloud `/f/<file_id>` citation, and Bob was denied. Removing
only the source share made Alice's old JWT lose DAV, source, ask, direct,
search and prior citation access on the first poll; LDAP membership, WeKnora
grant and owner's PDF remained. The PDF had complete DocReader text, so this
first run did not trigger the short-text AnyDoc recovery branch. A second
independent fixture forced only its DocReader to rasterize the same PDF: the
primary result had 46 searchable characters, the candidate logged AnyDoc
recovery to 283 characters, and the same indexing, answer, citation and
old-JWT revocation checks passed. This verifies the running fallback branch
under controlled input, not natural PDF truncation or scanned-PDF OCR. The
shared WeKnora LAN service still uses its prior image while the candidate is
reviewed. See [the PDF candidate record](isolated-pdf-candidate.md).

An earlier combined RAG candidate has complete patch SHA-256
`ed055900b1eca78cc15a14021794fb6dc95dafb3e8e5592ce3f865ca1538af03`,
app image `sha256:d052febfcd39d3ea20e136a12a9dc10fda2389d14c118748764318f2d32f22dd`,
and UI image `sha256:699f4ae751e57a838f875093bbd739ee0e1a64418d38bf00a56958b0b28e2b08`.
Both normal and controlled short-primary PDF flows passed again against the
current app. In the controlled flow DocReader supplied 46 searchable
characters and the local AnyDoc recovery supplied 283; indexing, source-scoped
answer, original-file citation, and old-JWT denial after source-share removal
passed. A separate disposable real-browser flow used the current app and UI:
Alice followed the Nextcloud Files sidebar handoff, signed in through LDAP,
asked about the exact published file and received the synthetic marker with
HTTP 200. The ordinary request omitted Agent identifiers. Clicking its
citation opened the original Nextcloud `/f/92` route; the same browser session
read the exact source file over WebDAV with HTTP 200. The browser check passed
again after the fixture began checking that state ports and images exactly
match its owned Compose configuration. All owned Compose projects were removed
after their runs. These are synthetic local checks;
there is no real AD/Team Folder acceptance, natural PDF truncation/OCR proof,
or enterprise answer-quality evidence.

The same current app image also passed the separate local BGE candidate
smoke with its pinned source and patch labels: the built-in embedding returned
512 dimensions, ReRank ranked the matching fictional clause first, one
synthetic knowledge item completed parsing, and pure vector search returned
one hit from one enabled 512-dimensional vector. That owned Docker project
was removed. This checks model wiring and a tiny retrieval example, not
answer quality on approved enterprise questions.

The current RAG app image also passed a fresh owned, loopback-only dual-service
event exercise in `nc-synldap-51dbd280`. Event e1 reached verified applied
watermark 2 on both services. Finalizing an exact source machine-key rotation
made the old event sender pause with `receiver_unauthorized`, without
advancing its receipt. The operator rebind command retained the connection ID,
HMAC key and inbox watermarks, resumed that sender through the scoped
compare-and-swap retry, and returned `already_active` on an identical replay.
Event e2 then reached verified applied watermark 4 on both services. The
fixture's containers, volumes and network were removed. This is one synthetic
rotation and continuation, not production fault-rate or latency acceptance.

Both complete WeKnora patches were regenerated from their pinned fixed and RAG
baselines and passed clean-apply checks. Selected direct HTTP reads,
HybridSearch, Agent read/list and live answer paths acquire renewable read
leases and recheck current source and user grants at output. Enumerated
Asynq/Lite document build workers acquire build leases; same-transaction
chunk, knowledge and version writes are fenced. MCP machine read routes reject
Nextcloud source documents. Focused fixed and RAG Go suites, including
revocation-during-hydration and HTTP-write tests, passed locally.

The coverage marker remains absent, so physical derived-copy GC stays denied.
External vector, graph and object writes, Wiki and other read paths, old task
drain, production coordinated restore, production alert routing and the 10,000-file /
100-GB performance target remain open. The same-directory LDAP fixture is
synthetic; a real AD and production Team Folder permission matrix is not yet available.

## Local cross-system evidence

The first cross-system ingestion and event probes preceded the source-pairing gate. Their legacy synthetic data source is now intentionally denied for new sync and event intake. A second, isolated WeKnora stack paired a new dedicated knowledge base with Nextcloud binding `isolated-pair-8dc8867a58`; both sides reported `active`. The existing local WeKnora test stack was connected to the isolated Nextcloud Compose network. A synthetic embedding model and dedicated knowledge base were created; syncing the `dev-published` datasource imported `department-notes.md`. WeKnora reported successful sync and completed parsing, and the resource carried Nextcloud source provenance. The local WeKnora administrator has no linked AD identity; direct knowledge access and the knowledge-base list returned HTTP 403. This is one ingestion and denial case. It does not validate an authorized AD user's retrieval or a generated answer; no real chat model or enterprise AD test account was used.

After the minimal version-state migration, a repeated local sync succeeded and left one completed, enabled candidate with a matching active source ETag in `nextcloud_source_versions`. On the newly paired source, a real manual sync imported `paired-note.md`, parsing completed and the version row became `published`. Moving that same Nextcloud file ID to `paired-note-renamed.md` and syncing updated the visible knowledge filename and provenance path without changing its candidate ID; moving it back and syncing restored both names. A separate synthetic `version-probe.md` was uploaded, synced, overwritten with different bytes and deleted; each version reached one visible candidate, and the candidate ID changed. After WebDAV deletion and two complete syncs, the source had a durable tombstone and zero visible candidates. The probe file was removed. On the later 0.4.18 image, another isolated synthetic file (file ID 6958) published, was overwritten with a new candidate and ETag, and became a tombstone with zero visible candidates after two deletion scans. Failed parsing and late-task handling still have repository tests rather than a cross-system fault drill. The Nextcloud stack has local accounts and groupfolders 22.0.6 for a synthetic ACL probe, but `user_ldap` is disabled. The existing WeKnora stack connects to enterprise AD while its OpenLDAP fixture is inactive, so neither an authorized AD read nor the full nested-group and production Team folder matrix has been demonstrated here.

The app's local HTTP smoke scripts cover service authentication, pagination, publication withdrawal, source authorization, change hints, root-folder events, runtime binding scope and retention. The change-hint smoke additionally verifies same-name delete/re-upload with a new file ID and a folder move out of and back into the bound scope; content access returned 404 while out of scope and 200 after return. A binding-wide Stop/Resume smoke verifies stopped reads, retained machine binding visibility, stale cursor rejection after resume, audit persistence and preserved per-file withdrawal. The optional Team folder probe can be run after installing the compatible groupfolders app in local Compose: `docker compose exec -T nextcloud chown www-data:www-data /var/www/html/custom_apps`, then `docker compose exec -T -u www-data nextcloud php occ app:install groupfolders --no-interaction`, then `python3 apps/integration_weknora/tests/team_folder_acl_http_smoke.py`. It removes its temporary user, group, folder, binding and identity. An isolated 0.4.6→0.4.22 upgrade smoke passes with valid and invalid old binding configuration. Fixed-baseline Go tests cover the connector and the guard paths in the patch. A 0.4.22 source-pairing HTTP smoke and WeKnora focused Go/SQLite suites also pass locally. The isolated paired-source ingestion and rename probe uses only synthetic data. These checks establish a development environment and selected safety behavior, not the PRD's acceptance matrix.

The 0.4.22 local diagnostics HTTP smoke passed after upgrade: anonymous and machine-token callers were denied, an administrator received only the documented non-sensitive sender fields, and a temporary non-administrator was denied. The event-delivery fault smoke observed a pending hint before receipt, zero pending after a durable receipt, and a paused sender with a pending hint and error code. Its single worker wake-to-receipt sample was 4.27 seconds, not a P95 result. The app archive contains 69 runtime files and passed its install-layout check. The loopback and LAN login URLs returned HTTP 200, and authenticated WebDAV on the LAN address returned 207. This is host-side verification; a second physical LAN client has not been exercised.

The fresh-install smoke extracted the 0.4.22 archive into a new Nextcloud 34.0.4 container backed by unique PostgreSQL and Redis volumes. `occ app:enable` completed on that empty instance; `installed_version` matched the archive, the binding, source-pairing and event-connection tables existed, and a newly generated administrator authenticated to DAV with HTTP 207. The script removed its Compose project and volumes afterward. This validates a clean local package installation, not production upgrade or rollback.

A later disposable run installed the 0.4.29 archive, disabled and re-enabled
the app, and restarted Nextcloud. `app:list` reflected each state; a private
app configuration value survived re-enable and restart. Authenticated DAV
created and read a test file before disable, read and overwrote it while the
app was disabled, and read the updated bytes after restart. Its isolated
volumes were removed. This covers local disable/re-enable and restart behavior,
not an upgrade-failure recovery or production rollback.

The WeKnora event-connection administrator API was exercised against the earlier local synthetic data source. A signed hint received HTTP 202 with a durable receipt watermark; nonce replay and a rotated or revoked key returned HTTP 401. Nextcloud's sender then delivered a temporary WebDAV file event, and both durable receipt watermarks matched. That earlier dispatch probe observed queue acceptance while the applied watermark was still `0`. On the newly paired isolated source, the revised `local-event-pipeline-smoke.py --expect-applied` created a temporary file and then deleted it. Each hint reached WeKnora's real full-source sync queue, and both WeKnora's applied watermark and Nextcloud's separately verified signed applied watermark covered the upsert and later delete. The delete required two complete scans and a dispatcher retry; the test cleaned up its file and revoked both temporary connections. This proves one synthetic event and publication cycle, not the PRD's sustained latency or enterprise load targets. A later isolated rerun covered upsert event 4973 and delete event 4974: both WeKnora and Nextcloud reached the applied watermark, the deleted source version became `tombstone`, and zero active knowledge rows retained a visible source ETag for that file. The first attempt used an unapproved isolated receiver origin and failed at sender configuration; a temporary Compose-only allowlist entry enabled the rerun and was then removed. The 0.4.18 rerun with `--verify-index` then covered upsert 4980 and delete 4981: both applied watermarks advanced, the upsert candidate had a ready chunk and enabled embedding, and the delete ended with a tombstone and zero visible candidates. Its temporary receiver allowlist and event connections were removed.

A separate isolated administrator-withdrawal probe used only temporary file ID 6962. Before withdrawal, its candidate was published and parsed, with one ready chunk and one visible knowledge row. Immediately after withdrawal, the Nextcloud manifest excluded it, its content endpoint returned HTTP 404, and publication recheck returned HTTP 409. Before a full sync, WeKnora still held the published candidate, so immediate denial relies on the live source/read guard rather than completed index cleanup. Two successful manual syncs then left a tombstone, no candidate ID, and zero visible knowledge rows. Republish restored HTTP 200 content; WebDAV deletion and two more syncs returned to a tombstone with zero visible candidates. The temporary file and machine key were removed; the specific trash DAV item returned HTTP 404 after deletion, while a `oc_files_trash` metadata row and publication audit state remain. The old WeKnora knowledge row was soft-deleted, but a ready chunk and enabled embedding remained; derived-index physical cleanup is still incomplete. This manual-sync probe is separate from the event applied-watermark acceptance above. Synthetic share, group-membership and Team ACL revocation tests do not establish the real AD two-account or authorized-retrieval acceptance matrix.

The isolated `local-source-pairing-abort-smoke.py` run used a separate Nextcloud Compose project and a fresh folder, binding and empty knowledge base. A one-shot loopback relay returned HTTP 503 only for that operation's signed commit, leaving a paused WeKnora source and pending Nextcloud intent; an exact abort closed both sides and removed the paused source. The same run verified Nextcloud-only lost-key abort, wrong-operation rejection, idempotent retry and active-pair abort rejection. It removed its fixtures and relay, and the temporary WeKnora origin approval and network attachment were reverted. This is a synthetic fault path, not a production network failure drill.

The isolated active pair then rotated its source credential with operation `024e590d-c574-46c1-830a-b8965de980bb`. Both services reported `finalized`, the old Nextcloud machine key disappeared, and the following WeKnora manual sync succeeded with two items and zero failures. The optional event connection had already been revoked, so this did not test event-connection repair after source-key rotation or a lost response during rotation.

The isolated WeKnora database upgraded from migration 120 to 121 without a dirty state, preserving its GC inventory. After making only two synthetic tombstone jobs due for a local retry, the new worker removed both registered local original objects. Their resource rows changed to `deleted`, their bindings fell from two to zero, and confirmed released bytes totaled 74; `/data/files/10000` shrank by 74 bytes. Both jobs remained `blocked` with derived index items outstanding, so this is a local object deletion proof rather than complete GC or capacity acceptance. Migration 122 / SQLite 41 reopens any prior GC job incorrectly marked complete and retains a derived-index blocker for every historical Nextcloud job. Migration 124 / SQLite 43 invalidates earlier empty-pair proofs, including a possible false proof minted by repair of an old pending pair; a durable operation-ID lineage prevents a deleted pair from regaining virgin status after reconstruction. Existing decommission records stop this upgrade for manual review.

The isolated WeKnora PostgreSQL database then upgraded from 123 to 124 without a dirty state. In a separate disposable Nextcloud Compose project, `local-empty-source-decommission-smoke.py` created a fresh empty folder, binding and dedicated WeKnora knowledge base after that upgrade. The exact pair became active; Nextcloud recorded a stopped decommission intent, rejected a different operation UUID and premature finalize, and WeKnora returned a signed empty-inventory ACK. Both sides reported `acknowledged` before Nextcloud finalized; retries with the same UUID were idempotent. The old machine credential returned HTTP 401, the binding disappeared from the active list, and WeKnora retained a paused source with an `acknowledged` decommission tombstone and historical `active` pair identity. The one-shot relay and empty WebDAV folder were removed. This proves the virgin-source path only; no indexed source was retired or physically cleaned.

The first-stage indexed-source withdrawal was tested against disposable PostgreSQL schemas with migration 125 and SQLite schema 44. Its PostgreSQL tests cover a signed event batch before withdrawal, atomic event-key revocation and dispatch blocking, a denied later signed batch with an unchanged receipt watermark, concurrent connection locks, write fences, append-only observed inventory and a permanent external-index blocker. A fresh, fully disposable Nextcloud 0.4.21 / WeKnora PostgreSQL 125 dual-stack HTTP drill then paired and indexed one synthetic text file (one ready chunk and one enabled embedding). An exact stopped Nextcloud intent led to WeKnora HTTP 202 with `logical_withdrawn=true` and `inventory_complete=false`; the old event key returned 401. Nextcloud retained its stopped binding and source key, had no ACK, and rejected premature finalize with 409. The drill used an isolated mock embedding endpoint after the initial built-in model endpoint was rejected by SSRF policy; its owned containers, volumes and temporary credentials were removed. This is one synthetic first-stage withdrawal, not proof of full historical copy inventory, physical cleanup or enterprise behavior.

PostgreSQL migration 126 and SQLite migration 45 add a local source-version observation ledger. Disposable PostgreSQL 123→126 and SQLite 43→45 upgrades preserved current rows while marking pre-existing histories unknown. A PostgreSQL migration lock blocks writes across the backfill and trigger installation; a concurrency regression checked a writer that committed before the lock and a later writer that appended revision 2. Eight concurrent UPSERTs remained monotonic in both databases, and rejected writes against the indexed-withdrawal fence did not append a revision. Tests also rejected ledger edits and automatic rollback. These observations do not enumerate pre-upgrade candidates or external vector, graph, Wiki, backup and object copies, and they do not change indexed-withdrawal `inventory_complete=false` or authorize GC or an ACK.

PostgreSQL migration 127 and SQLite migration 46 add an inert per-knowledge
read/build lease and retirement fence foundation. Disposable PostgreSQL and
SQLite tests cover acquire-versus-retire and GC-claim-versus-KB-read races;
an active exact GC claim now rejects new KB-wide read leases until release or
expiry. The coverage table is empty after migration, so GC claims are denied
by default. Direct knowledge, chunk, preview and download HTTP handlers now
acquire renewable exact read leases and recheck publication and reader grants
at bounded response writes; handler/router/container and repository tests
passed in isolated Docker. The 2026-09-30 update above adds selected search,
Agent, MCP and build paths. Frequent remote publication checks still need
large-download performance measurement. Wiki, graph, other readers, old task
drain and external index adapters remain uncovered. No coverage marker is
written and this does not authorize physical deletion. The [content lease
design](nextcloud-derived-gc-read-lease.md) tracks the remaining gates.

## Isolated synthetic LDAP acceptance

A separate disposable stack connected Nextcloud `user_ldap` and WeKnora to the
same AD-shaped OpenLDAP directory. Both synthetic users could log in. With
only A in Engineering, A could read the published file and retrieve its
WeKnora knowledge, chunk, preview and search hit; B was denied. Removing A
from Engineering made Nextcloud DAV and signed source authorization deny the
file. A's **old JWT** then received HTTP 403 for direct knowledge, chunk and
preview reads; search returned zero hits. A fresh-login matrix agreed after
directory sync. The protected fixture, containers, volumes, images and network
were removed after the drill. [Reproduction and limits](synthetic-ldap-acceptance.md)
record the exact synthetic scope; this does not establish enterprise AD, nested
groups, production Team ACLs or the full Q&A citation path.

The same isolated stack, rebuilt with `77c2f9f`, indexed two files and ran a
signed event for an update to just one. WeKnora's received, dispatched and
applied watermarks reached event 4; Nextcloud verified the signed applied
watermark 4. In the event sync window, the changed file had one content GET and
the unchanged file had none. Paired-source health returned HTTP 200 to the
administrator, 403 to a non-administrator and 401 anonymously. The source
retained two published, parse-completed candidates. This is one scoped event
case, not the PRD's 10,000-file/100-GB or P95 latency acceptance.

A disposable paired pilot harness now measures bulk sync and event-to-durable
queue admission with explicit small and large fixture controls. Its final
reduced run synchronized two 256-byte files in 0.335 seconds and observed
three independent event-to-queue intervals of 6,073.6, 5,069.7 and 5,266.1
milliseconds; all three reached the applied watermark. Nearest-rank P95 for
these **three** samples was 6,073.6 ms. Five samples saw a 343,703,552-byte
WeKnora process RSS peak. This is a tiny, sequential, synthetic check of the
measurement path, not acceptance of the 10,000-file/100-GB, sustained P95,
upload-latency or connector-memory targets. The two pilot projects and their
private credentials were removed after recording a credential-free report;
see [pilot load runbook](../scripts/ops/PILOT-load.md).

The acceptance scripts now require a fresh, attribute-limited LDAP topology
export before claiming a synthetic primary-group or nested-group case. A
separate loopback-only probe checks GUID/UID mapping conflicts, and a watcher
holds the original JWTs across a synthetic group-revocation drill. Their 16
offline contracts and five WeKnora OpenLDAP network tests passed. A fresh,
disposable dual-service stack then passed the six-field nested-only group
HTTP matrix: Alice had file/source/knowledge/search access through
Alice→Platform→Engineering, while Bob could log in but was denied all four
content checks. Its one synthetic file had a ready chunk and embedding. The
earlier primary-only case failed across services: WeKnora recognized Alice's
`primaryGroupID` membership, but Nextcloud `user_ldap` omitted Alice from
Engineering because raw OpenLDAP did not match its textual SID lookup against
binary `objectSid`. A later fresh, loopback-only fixture added a narrow
OpenLDAP frontend rule that converts only the generated Engineering SID
assertion to binary. Neither application received an extra LDAP member edge.
The six-field primary-group matrix then passed with Alice's sole Engineering
grant through `primaryGroupID=2000` and Bob denied. After Alice's primary group
changed to Domain Users, her original WeKnora JWT lost direct knowledge and
search access by 08:03:18 UTC; separate DAV and signed-source checks also
denied her. WeKnora's directory membership, Nextcloud's group view and a
fresh topology/login matrix confirmed the removal by 08:03:20 UTC. This is a
synthetic AD-like query simulation; real AD primary groups and LDAP-backed
Team-folder ACLs remain unverified. See [the disposable fixture](synthetic-ldap-compose.md).

A fresh direct-group fixture also exercised the complete file-path lifecycle
against candidate image
`sha256:b75fbae3386625c213d7ae49f701c69d34884508aec0951f70928236e7a24056`.
The same Nextcloud file ID 242 was created (event 21), renamed (22), moved out
of the binding (23), moved back (24), moved out and back before delivery
(final event 26), sent to trash (27), and restored (28). Both services'
applied watermarks reached every target event. Rename updated the indexed
path/name without replacing its candidate; each confirmed withdrawal became
a tombstone with zero visible candidates after the connector's second full
scan. Move-back and restore created new candidates, and rapid movement ended
with the currently authorized source published. Source bytes and Alice/Bob
allow/deny checks passed after restoration. The isolated project's containers,
volumes, network, and private scratch directory were removed. The
[reproducible probe](../scripts/ops/README.md) covers a synthetic direct share,
not production Team Folder or enterprise AD behavior.

A separate owned two-binding synthetic fixture used the source-65 backend
image `sha256:eb558b7ab6d8ae53d3165263bea577ad4d6d12c62dcd75c62aa394c473d4625b`
to move a nested directory with three descendants (file IDs 167, 168 and
169) from A to B and back. Each source binding produced three tombstones;
each target binding published three new candidates while file IDs stayed
stable. The corresponding A/B event watermarks reached 12 initially, then
13/14 and 15/16 across the two moves. Alice's current source and direct
knowledge were readable and her ask returned 200; Bob's direct read and ask
were denied. Alice's unauthorized DAV DELETE returned 403 without any new
delete or subtree-delete hint, and the original file and candidate remained.
The fixture's containers, volumes and network were removed. This verifies the
synthetic two-binding event path; production ACL and load remain open.

## Failed-candidate recovery

The two pinned WeKnora patches now store a per-file failed-candidate retry
intent. A five-second dispatcher scans active paired Nextcloud sources, using
keyset pages rather than a fixed first page, and claims one exact failed
generation for a bounded 24-hour automatic window. Retry delay grows from one
minute with stable jitter. Each task carries the source ETag, failed candidate,
pair operation and epoch, source config hash and one-use lease token. The worker
rechecks these selectors before reading source bytes and before Stage; a stale
queued task cannot replace a newer candidate. The administrator-only API can
inspect the exact file's retry state and restart an exhausted window with a
compare-and-swap request. No parser error text or source credential is exposed
through that status response.

A retry of an unchanged ETag downloads only the failed file after a complete
metadata preflight. A separate event retry still refreshes the full metadata
manifest when its changes page has become empty; this advances the source's
reconcile timestamp so a completed event can reach the verified applied
watermark without re-downloading unchanged file bodies. Fixed and RAG focused
Go tests cover the retry claim, same-ETag staging, stale task denial and event
timestamp transition. Both pinned patches apply to clean source archives and
the SQLite migration-47 upgrade tests pass. At that stage the RAG baseline was
pinned to
`b8a34e0bae8fcf0d3c8273bba2e56414abed41e2`; its evaluation-cost Go tests
and frontend typecheck also pass. The isolated candidate that established the
automatic retry behavior before the administrator list was added has app image
`sha256:35dbcdf20a01fe22998da6dbd141bc1375722af1c3f8b3aaa5dbeb1ce37dd727`
and UI image
`sha256:2c093023f7522f92614f921af4bc12d6f44760192e856a170344308fd03a1a25`.
Both carry the pinned source and full patch SHA-256 labels; neither replaced
the shared WeKnora service.

The earlier loopback-only `--phase full` fault drill used this app image and the
packaged 0.4.29 Nextcloud app. After a new, same-file V2 WebDAV overwrite
created outbox upsert event 1, its first candidate failed while only the
fixture's embedding service was stopped. The V1 recovery copy remained stored
but its old target, direct document and answer/citation were inaccessible.
The durable retry job recorded one automatic attempt and a sync-log ID; after
the model service returned, a distinct V2 candidate published with a current
ETag, one visible copy, ready chunk and embedding. Alice's file-scoped answer
cited the original Nextcloud file. Bob's target and direct document returned
403, and his selected search and answer stream exposed no content or citation.
The old V1 target, document, selected search and stream remained denied after
V2 publication. Both services' verified applied watermarks reached the new
event. The fixture's containers, volumes, network and private scratch data
were removed. This synthetic direct-share run does not verify enterprise AD,
production Team Folder ACLs or a performance SLA; reproduce it with the
[isolated fault probe](../scripts/ops/README.md).

The administrator failed-candidate drawer now lists only current failed
staging files for an active paired source, one tenant and one knowledge base.
Its server page is capped at 50 rows and returns only numeric file IDs and
static retry state codes. Selecting a file fetches the exact candidate and
ETag; the restart request uses those selectors in a compare-and-swap. A `409`
clears the stale selection and reloads current status. Ordinary users cannot
call the list API. PostgreSQL with `en-x-icu` exposed a bug in the original
prefix upper bound: `~` sorted before numeric file IDs. The query now uses an
exact prefix check and database-consistent keyset cursor. A dedicated
PostgreSQL collation test and SQLite malformed-row pagination test pass. The
earlier retry candidate's fixed and RAG patch hashes were
`1d60b7db90ae67aefcf7f19f77f11291918dfd071a07fb58b87bdc5bac8bc78e`
and `2883e0d64518c3bb0da04d74136545677dbfc29f71d907d41065bc80e87901d5`.
The RAG source is pinned to
`77c97fd72f26e84435503d24eeed88cb5dfe1f01`, which also includes the
evaluation selected-model guard. The isolated backend image is
`sha256:1a6d27d3a35f6056552449aadb1e14c34840fb7dd9afc9e4f13eb6dd5ebc27dc`
and the UI image is
`sha256:861693296338a30560d3353916ceb9bc3b6a428ef8dc77bdb8bfadf7649ac951`.
Both labels contain that source revision and the full patch hash; neither is
used by the shared WeKnora stack. A fresh loopback-only `--phase full` run with
this backend image passed: V2 produced a new outbox event and one failed file
(`file_id=92`); the administrator list returned exactly that file and its
active operation ID, while Alice and Bob each received HTTP 403. After one
durable automatic retry, a distinct V2 candidate published, the list became
empty, and its desired and visible ETags matched the new source ETag. One
visible copy had at least one ready chunk and embedding containing the V2
marker. Alice's answer cited the original Nextcloud file. Bob's target and
direct document returned 403; selected search and answer stream exposed no
V2 content. The old V1 target returned 404, direct document 403, and selected
search and stream exposed no V1 content both before and after V2 publication.
The receiver's durable receipt and both sides' applied watermarks reached
event 1. These watermarks prove event processing, while the separate current
ETag, ready chunk, embedding, answer and citation checks prove actual
publication. The owned project, volumes and network
were removed. The first 77-image attempt timed out on an isolated PostgreSQL
read while other Docker builds and fixtures were active; the serial rerun
passed all assertions and removed its owned resources. A separate real
Chromium run used an isolated failed V2 candidate with the current editor:
the administrator list showed file ID 92, the exact drawer showed attempts
and next attempt, an injected `409` reloaded both list and exact status, and
the real retry POST returned `202`. A file-count probe returning `403` did
not hide the knowledge-base editor and displayed a disabled storage selector.
The later backend and frontend guard fix prevents that disabled selector from
silently rebinding a legacy knowledge base; its focused tests cover files,
deleting rows and count errors. Alice saw no settings menu and received `403`
from the list API. The browser run used separate candidate images and cleaned
its own fixture.
All eight GitHub checks on the earlier `30de4d5` pinned-source commit passed, including
both patch applications, new frontend retry tests, PostgreSQL collation tests,
direct upgrade and local smoke.

The WeKnora-facing OpenAPI contract now records 27 Nextcloud integration
methods across 22 paths, including event receipt/status, pairing, failed
candidates, source authorization and file-scoped ask. A standard-library
contract checker runs after each pinned patch is applied in CI. On both clean
source archives it verified all 27 routes, 26 success response shapes and 11
Go JSON structures. Deliberate route-method, cursor-key and ETag-tag drift
were rejected by the checker. This freezes the implemented interface shape;
the live enterprise network and identity integration still need acceptance.

## 2026-10-01 candidate stabilization

The preceding fixed c6 and RAG77 full patches had SHA-256 values
`5611d1c3b562410a5314f1dd82312fb485816d740f5ac603fa25253b619263a8`
and `045ff1a17b244c90d1263f3e458fabfa0e142268e7b5df7f1f70cbae91a72852`.
Both apply back to their pinned upstream commits. The combined Go tests for
read-lease cancellation, knowledge-base settings, and event-key rotation pass
on RAG77; the 27-route OpenAPI checker passes on both applied patches.
The isolated RAG77 candidate backend image is
`sha256:590d91c9d9e963c49c68cbd3b3dd93731d53df0e1eb302858fa1c28d2ff788e3`;
its UI image is
`sha256:710bbe26743b809bad18302f1c1722363e781e6a3a257610979a6f86d0b91a21`.
Both carry the exact RAG77 source revision and full patch hash as OCI labels;
the shared WeKnora service still uses its earlier image. An immediately
preceding candidate with identical runtime source, before a Go test-only
PostgreSQL assertion correction, passed a fresh loopback-only `--phase full`
probe. File ID 92's V2 parse failed while its V1 recovery bytes stayed
stored; the administrator list showed one failed candidate, and Alice and Bob
received `403` from that list. A durable retry with a new sync log published
a distinct V2 candidate with ready chunk and embedding, and the list became
empty. Alice's file-scoped answer cited the original Nextcloud file. Bob's
direct/ask routes denied access, and selected search and streamed answer
suppressed its content. The old V1 direct/ask routes and answer remained
denied. Both verified applied watermarks reached event 1. The probe removed
its owned containers, volumes and network.
A separate real Chromium run with that preceding backend and UI runtime
opened the failed-file drawer even when the editor's file-count probe
returned `403`.
It showed file ID 92 and its exact manual-retry status, reloaded both list and
exact status after an injected `409`, and received `202` from the real retry
POST. Alice had no settings menu and the list API returned `403`. The browser
fixture also removed its owned containers, volumes and network.
The same backend runtime, before a test-only PostgreSQL rotation assertion
was corrected, passed `--cross-bindings-only` with three nested files. A
forbidden DAV DELETE returned `403` without a deletion hint or lost
candidate. Moving the subtree A→B→A produced three source tombstones and
three distinct destination candidates on each leg; event receipt and applied
watermarks reached 12–16. Alice retained direct and file-scoped answer
access, while Bob remained denied. This owned fixture was removed. The
subsequent full-patch hash change touches only that Go test assertion.
The corrected PostgreSQL event-connection lifecycle test passed against a
separate PostgreSQL 16 container; its container and network were removed.

A canceled read request now returns its parent context error even when a
concurrent lease renewal observes a closed database transaction. The original
cancel/close test and a deterministic renewal-failure test each passed 100
repetitions on both source baselines. A legacy knowledge base can no longer
silently adopt the current default storage backend when its file-count probe
fails. Server-side storage binding and Embedding-model changes check both
ordinary and deleting knowledge rows and reject a failed count. Focused Go
tests on both baselines, eight frontend tests, and frontend type checking pass.

The event receiver now keeps the immediately preceding HMAC key for two
minutes after rotation. A second rotation removes the oldest key, while
revoke and source withdrawal still disable the connection immediately. Event,
connection-status and file-status signature checks use the receiver clock
after taking the connection lock; the file-status response key is rechecked
at signing. Focused Go tests on both baselines cover the old/new interval,
expiry, second rotation, replay and scope, and the event smoke script now
expects the same behavior. This two-minute event-key window is distinct from
the source machine-key rotation protocol's maximum 24-hour overlap.

## 2026-10-01 isolated follow-up candidate

Nextcloud app 0.4.30 adds a narrow V1 publication ACL gate. A binding can use
its publisher's local home directory or the verified Team Folders 22.0.6 base
permission mount with advanced ACL disabled. Creation and resume check the
publication tree; manifest scans and individual content, authorization and
Files status reads reject a different nested mount. When an active Team Folder
enables advanced ACL, an ordinary source read fails closed and persistently
stops the binding with an audit entry, a new publication epoch and a
reconciliation hint when the database and outbox are available. An
administrator must disable that ACL mode and explicitly resume. A fresh,
owned Nextcloud-only Team Folder smoke checked normal child-file publication,
the 409 creation/update/resume denials, the exact stop audit and epoch, denied
content and authorization, diagnostics, and recovery; its resources were
removed. A separate synthetic LDAP/WeKnora drill had already verified an
indexed uniform Team Folder and the same stop path. A later disposable
Nextcloud 34.0.4 fixture enabled `files_external` and mounted local storage
inside both an active Team Folder binding and an unbound local folder.
`files:mount:list` and DAV PROPFIND confirmed the nested files were readable.
New binding creation returned `409 unsupported_source_acl`; the active
binding's manifest failed closed and persistently stopped it with epoch 1,
an audit entry and diagnostics. Resume returned 409 until the mount was
removed; then resume reached epoch 2 and the manifest returned 200. The
fixture's containers, volumes and temporary files were removed. This policy
does not establish enterprise AD or arbitrary external-storage ACL parity; see
[synthetic scope](synthetic-ldap-compose.md). The 0.4.30 runtime package
contains 75 files and has SHA-256
`8e8a7355f6a127f4ecf7620d4ffc18e98c64685672306d15c778b1f77f69ec8b`.

The fixed c6 full patch now has SHA-256
`4f976461f099da9ba6a53ce637b1ae7f285f46b0177ad95fc8bcee6a23b76792`;
the RAG77 full patch has SHA-256
`cc6325eac6cb5ef128e32a803cf5610f2a627ab5a6c7119849efda055744214c`.
PostgreSQL migration 129 and SQLite migration 48 admit append-only source
revision identities into the private indexed-withdrawal inventory. A hard
deleted old knowledge row remains represented by its historical candidate
revision. Fixed and RAG PostgreSQL focused tests, SQLite migration tests,
exact patch reverse checks and both 27-route OpenAPI checks passed. This
extends observed evidence only: pre-ledger and external copies are unknown,
`inventory_complete` remains false, no Nextcloud retirement ACK is sent and
derived-index physical GC stays disabled.

The RAG77 candidate images were built with AnyDoc and the exact patch hash in
their OCI labels: backend
`sha256:c2b78f72f0c352949416b17f649b50271555c4eec2ecbad670264e783bec868c`
and UI
`sha256:770e0ed1048bbb7d71f848d481e68110de158a56493a4edfd6fbaab3eb884eec`.
They are separate from the earlier images still used by the shared WeKnora
stack. The previous end-to-end failed-candidate and two-binding drills used
the earlier runtime; this follow-up changed inventory and Nextcloud ACL code,
so those earlier drills are not claimed as tests of the final image labels.
The final backend image then passed an owned dual-service physical backup and
restore drill. Nine checkpoint files included the fixture's state, Compose
configuration and WeKnora JWT/AES keys; their checksums and private modes were
verified and those files were restored before container creation. A stale
published file remained denied before and after two complete reconciliations;
the source ended as `tombstone` with zero visible candidates. The observed
checkpoint and restore durations were 31.828 and 64.234 seconds. The fixture
and volumes were removed. This is local synthetic recovery evidence, not the
PRD's production RPO/RTO, external-backend recovery or complete historical
publication ledger; see [restore drill](isolated-dual-service-restore.md).

A further fixed c6 / RAG77 patch revision has SHA-256
`dd7e09a9df65085d31e99737220dd3a60ff8b2dabfe823f6cc75fabace1b73ee`
and `f627d20af4edd053163d5f8594f8e75fc55393395d2135701073a1a75df0d475`.
It adds a read lease and output-boundary source check to global
`/knowledge/search`; focused tests on both baselines show a live lease blocks
a GC claim after retirement and a revoked result's title is not sent. Derived
GC remains disabled because other paths lack coverage. New manual and scheduled
Nextcloud syncs also derive a stable queue task ID from the persisted log ID.
If enqueue outcome is uncertain, a static redacted status and task identity
are available to administrators while the running admission slot stays
occupied. Two queue fault-injection cases and a repeated cron tick passed on
both baselines. There is still no automatic recovery for an uncertain enqueue,
because a missing Asynq task entry is not proof that a task never ran. Older
ambiguous logs predate stable task IDs and require manual investigation.
The new RAG77 AnyDoc backend image is
`sha256:9880ab2b915f0f2ac6a40650370f7698945a540043afed4602cb6823edb6f3ab`;
the UI image is
`sha256:f7d1671dcd6c1010f59b5072795cae380327fe4046beadabd643755ecdb7145a`.
Both carry the pinned source and new patch SHA-256 labels. An owned disposable
Nextcloud/WeKnora/LDAP Compose fixture booted them, both HTTP health endpoints
returned 200, and its containers, volumes and network were removed. This is a
fresh-image startup check, not a repeated answer/revocation or restore drill.

## Consistency and release boundary

The pinned fixed and RAG WeKnora patches now fence PostgreSQL pgvector
`Save` and `BatchSave` with the current Nextcloud build lease and source
candidate in the same transaction as each embedding write. A candidate's
early parse task waits for its committed Stage record before it enters the
handler; a retired fence or absent Stage fails closed. Two-connection
PostgreSQL tests cover write versus retirement, Stage versus Tombstone,
stale Publish, late exact-ID GC inventory, a failed transaction and a Lite
worker that starts before Stage. Both patches apply to their pinned source
commits and passed the focused repository, pgvector and router suites.
The RAG patch was also built into separate backend and frontend Docker images;
the backend image `sha256:b75fbae3386625c213d7ae49f701c69d34884508aec0951f70928236e7a24056`
passed a fresh, isolated six-field synthetic primary-group matrix and
old-JWT revocation drill on 2026-09-30. The drill's containers and volumes
were removed; [the exact result](synthetic-ldap-compose.md) records its scope.
The exact-vector delete helper remains dormant and the `derived_index` GC
blocker remains active because external writers and all readers are not yet
covered. See [the lease gate](nextcloud-derived-gc-read-lease.md).

The first manifest page scans the bound tree and stores a sorted list for ten minutes. Later pages read that list and reject a changed source or publication revision. A final publication-revision check now also covers a withdrawal that commits after page selection or snapshot persistence. A 207-file smoke covered pagination, a concurrent write, withdrawal between pages without an ETag change, and conditional content reads. This is not a transactional source snapshot; a change during traversal can invalidate a page, and large lists still need performance testing. The connector treats events as hints, recovers an expired cursor by full reconciliation and confirms absence twice. It cannot substitute for a durable publication state machine or a tested backup/recovery protocol.

The Nextcloud app was upgraded in the shared local stack to 0.4.31 and Apache
restarted after the bind-mounted PHP change. The local API smoke, LAN login
HTTP 200, authenticated LAN DAV PROPFIND 207, and WeKnora LAN root HTTP 200
passed. The reproducible 0.4.31 app archive contains 75 runtime files and has
SHA-256 `110e4b9814c808f2f39f9121f77588ac96f9b114a4d36f913770308226b75876`.

## 2026-10-01 follow-up

A disposable `post-accept` queue pilot found a 74.8-second event-to-dispatch
sample in the preceding RAG image. An event-only five-second proof-poll
candidate shortened ordinary applied checks but still measured an 80.0-second
sample when a second WebDAV write superseded a candidate that was still
parsing. Both runs used two 256-byte files and three measured events; neither
is a sustained P95 acceptance. Their exact image IDs, sample values and
watermark transitions are in the [isolated event queue pilot](isolated-event-queue-pilot.md).
The PRD's ten-second event-to-task target remains unaccepted.

Nextcloud 0.4.32 accepts a signed, allowlisted
`failure_code=no_retrievable_content` for a failed current file. The Files
sidebar shows a specific no-content message and does not create a question
link. The shared local stack upgraded successfully; maintenance mode is off,
LAN login returned HTTP 200, authenticated LAN DAV returned 207, and the
employee file-status HTTP smoke and PHP signature contract passed. Its
reproducible runtime archive has 75 files and SHA-256
`df9fb1c261453197183aa2ffbf57b5936ea2e494f3deca45e304ff6e660e5f07`.

Nextcloud 0.4.33 persists the binding-root-relative target path with new
file upsert and metadata hints and signs it in the event batch. The local
0.4.32→0.4.33 upgrade and an isolated HTTP smoke covered create, overwrite,
nested file creation and same-binding rename. The shared LAN stack upgraded
with maintenance mode off afterward and its login returned HTTP 200; the
WeKnora receiver's cross-service path proof is recorded below. The
runtime app archive contains 76 files and has SHA-256
`cbc989cbac2795f216b2b51d7372e38fb62a7d34c52e0258b05480e8953ce440`.

The fixed c6 full patch SHA-256 is
`5da6f751906b938db68c014607f2adc22feb151f6d012f527c1400f10608f6e7`;
the RAG77 patch SHA-256 is
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`.
Both applied to clean pinned source archives and passed the 27-route,
26-response-shape, 11-Go-struct OpenAPI contract. Focused Docker Go tests
passed on both baselines for zero-text publication/status/ask-target denial,
completed-sync event fast wake, and knowledge-list/raw-search read leases
with revocation and GC races. A local fixed-baseline backend image built as
`sha256:1206e84f0e883492735bfe6efa9690adb0ad8ee6f7cb7d6f15d045d603bc5b91`.
It was built before the test-fixture-only `sync_logs` schema correction in
`140c8e1`; the runtime source is otherwise the same. On 2026-10-01, a fresh
isolated direct-group LDAP fixture (`nc-synldap-72933917`) started this image,
indexed two chunks and embeddings, and passed the six-field Alice/Bob matrix:
Alice could log in and access DAV, signed source, knowledge and search; Bob
could log in but was denied the four protected content paths. Its owned
containers, volumes and network were destroyed. This is synthetic direct-group
evidence, not real AD or the latest RAG runtime. The latest RAG AnyDoc,
browser-skill and Go backend stages compiled, but two final image attempts
ended on different Debian mirror single-package HTTP 502 responses. The shared
WeKnora stack still uses an older image.

A separate candidate used the newer WeKnora RAG head
`3ad3b31f2b3e6409c6a9c196bbab70e2eac6c666` with the same RAG patch SHA
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`.
Using a different Debian mirror, its AnyDoc backend and UI built as
`sha256:2ec7e6b31463e6764130973cc1d636d2f65c83268376943cbae59713b7156f9e`
and `sha256:5d9272403683d4fafc0d48c07543ba10c9918041d4d3ece922c4952fb9a21075`.
Both images carry that exact source and patch in their OCI labels. A fresh
isolated direct-group LDAP fixture (`nc-synldap-bc78b1d4`) started backend and
UI healthy; UI returned HTTP 200, bootstrap indexed two chunks and embeddings,
and the Alice/Bob six-field matrix passed with the same allow/deny pattern.
Two further owned fixtures tested the same backend: `nc-synldap-f089ff7b`
confirmed Alice's sole Engineering grant through her AD-shaped primary group,
then changed that group to Domain Users; her existing WeKnora login could no
longer read DAV, signed source, knowledge, direct content or search, and a new
directory snapshot removed the membership. The first denial was observed after
seven polls, 25.5 seconds after the baseline checkpoint in this fixture.
`nc-synldap-521230a5` verified a sole nested-group edge and passed the same
Alice/Bob six-field allow/deny matrix. Each fixture indexed two chunks and
embeddings, and all owned containers, volumes and networks were destroyed.
An additional direct-group fixture (`nc-synldap-ce9c73be`) installed the
digest-checked Team Folders 22.0.6 app inside its owned Nextcloud container,
paired a real Team Folder root, and indexed two chunks and embeddings. Its
file-scoped baseline answer carried the synthetic marker and original Files
citation. After a file-level advanced ACL `-read` rule for Alice, her Files
status became 404, DAV and signed-source reads failed, ask-target returned
403, knowledge and direct-content reads failed, search returned no content,
and the prior history was redacted; Bob remained denied. The first denial was
observed 8.2 seconds after the ACL mutation in this fixture. Its owned
containers, volumes and network were destroyed. This is a synthetic Team
Folder result, not a production revocation SLA.
This checks the patch against a newer RAG source head, not a runtime image
built from the repository's pinned `77c97fd7` baseline; it does not prove
enterprise AD permissions or the PRD load target.

The same newer-head AnyDoc candidate completed two further owned PDF fixtures.
The natural born-digital PDF path (`nc-synldap-d2d04e24`) indexed the protected
marker in two ready chunks and embeddings, exposed an available AnyDoc engine
and connected DocReader, answered a file-scoped question with the original
Nextcloud file citation, then denied signed source, ask-target, direct content,
document search and historical citation to Alice's old JWT after source-share
revocation. The natural primary parse was complete, so the short-text AnyDoc
recovery branch did not run. A separate controlled short-primary fixture
(`nc-synldap-65f5b37f`) forced that branch: DocReader yielded 46 primary
characters, AnyDoc recovered 283, and the same protected text, citation and
old-JWT revocation checks passed. Both fixtures retained LDAP membership and
the owner's original file, then destroyed their owned resources. These are
synthetic PDFs with mock embedding/chat services, not a scanned-PDF OCR or
enterprise answer-quality acceptance.

The pilot harness now has a `post-accept` event pattern that times an update
sent just after a previous event enters the queue. Its offline schedule test
passed. At that point no new Docker load run or PRD 10,000-file/100-GB/P95
acceptance had been made. The safe dispatch fast wake preserves same-source single flight
and publication proof; it is not a measured latency guarantee. Derived-row
physical GC remains disabled because other read and external-write paths lack
complete coverage. Enterprise AD and production Team Folder acceptance still
await a real target environment.

## 2026-10-02 signed event proof follow-up

The fixed c6 and RAG77 patches now persist each signed upsert's ETag and
binding-relative path in the WeKnora event inbox. Applied ACK requires that
exact file version in the completed source scan and published candidate.
Broad or excluded-file hints require two complete scans of the same target;
legacy file hints without a relative path enter manual review. The exact
patch SHA-256 values are `748866423a51e6aa0d50b151366e80adb5325a9447d499164f9162b821fdaa40`
and `2542c3f423c2c143191de4d01d508db3761242cfa8e7decd1b3bc43ef98e7d12`.
Both applied to their pinned clean bases, passed the 27-route OpenAPI
contract and focused SQLite tests, and passed isolated PostgreSQL event,
pairing, indexed-withdrawal and rebind tests. No enterprise AD fixture was
available, as agreed for this phase.

A disposable 0.4.33 Nextcloud and newer-head WeKnora event-only pilot
observed seven signed inbox hints; all seven carried ETag and relative path.
For three post-accept updates of two 256-byte synthetic files, the
event-to-durable-job upper bounds were 5,463.7, 4,938.7 and 4,909.8 ms
(nearest-rank P95 5,463.7 ms). Event-to-applied-proof bounds were 15,306.3,
14,820.0 and 15,270.9 ms. The backend binary and migrations were layered
onto the preceding AnyDoc image solely for this event experiment; this is
not a rebuilt release image. The original report and image ID are in the
[pilot record](isolated-event-queue-pilot.md). Three tiny samples do not
establish the PRD's sustained P95 or 10,000-file/100-GB targets. Bounded
overlap while a prior parser is still running remains unimplemented, so a
later sample can still wait behind a parser. At the time of this pilot, the
shared LAN WeKnora image had not been upgraded to this receiver; the later
coordinated upgrade is recorded below. Connected sources still require
watermark and historical-hint review before event delivery is enabled.

The current patch also rechecks every retired knowledge's chunk image
references at each due GC retry, before any local source-object claim. A
late image URL is added to the durable inventory; a late malformed
`image_info` value records `invalid_image_inventory` and blocks the provider
delete callback. Correcting the value permits an idempotent retry. The
focused SQLite and isolated tmpfs PostgreSQL GC tests passed on both pinned
baselines. This closes the previously observed retry gap, but external
writers can still race the scan and physical claim. Derived-index physical
GC remains disabled until its full writer and reader lease coverage is
proved. The three-sample event pilot above used the preceding patch revision;
its historical hash remains in the pilot report.

## 2026-10-02 pinned RAG77 image and LAN upgrade

The pinned `77c97fd7` source and RAG patch SHA-256
`2542c3f423c2c143191de4d01d508db3761242cfa8e7decd1b3bc43ef98e7d12`
produced full arm64 AnyDoc backend and UI images. Their image IDs are
`sha256:4106172dec93957e94ab8c2e1a23d7844579b4b25a4dc1db644f1cf624d64d3a`
and `sha256:5f6842d7206558e825d9ac877596caba6a2a5b5cc42c953610a8509241c8d081`.
The backend contains its binary, migration CLI and browser-skill artifact;
the isolated parser-engine API reported AnyDoc available.

An owned direct-group LDAP fixture (`nc-synldap-18925d9f`) booted both images
and indexed two chunks and embeddings. Its six-field access matrix allowed
Alice's Nextcloud DAV, signed source, WeKnora knowledge and search, while Bob
could log in but could not read those four resources. A further synthetic file
upsert carried a matching signed `relative_path` and ETag in the Nextcloud
outbox and WeKnora inbox. Event #2 reached received, dispatched and applied
watermarks on WeKnora and received and applied watermarks on Nextcloud; the
candidate was published with two ready chunks and embeddings. WebDAV deletion
produced event #3, which reached both applied watermarks and a tombstone with
no visible candidate. The [redacted event evidence](evidence/rag77-full-image-event-2026-10-02.json)
records this single functional probe; its 31.7-second upsert and 94.2-second
delete timings are not P95 acceptance. The owned fixture was destroyed.

A read-only dump of the shared WeKnora database was restored to a disposable
PostgreSQL 17 container on an internal Docker network. Running only the
candidate image's migration CLI advanced the clone from schema `127/false` to
`130/false`, including the event inbox ETag, path and relative-path columns.
The clone, its volume, network and temporary credentials were removed. Before
the shared upgrade, both Nextcloud and WeKnora had zero active event
connections. A verified shared-database backup was saved at
`dist/backups/weknora-before-rag-20261001T194752Z.dump`, and the old images
were retained under `pre-rag77-20261002` tags. The coordinated upgrade
installed the two image IDs above; the shared database is `130/false`, both
containers are healthy, both LAN entry points return HTTP 200, and an
authenticated Nextcloud LAN WebDAV `PROPFIND` returns HTTP 207.

The employee Files status lookup now compares the binding publication epoch
and append-only publication audit revision around a slow signed WeKnora status
request. A stop/resume or withdraw/republish transition cannot reuse an old
`ready` observation with the same ETag. A focused PHP contract and CI checks
pass. Real enterprise AD accounts, sustained 10,000-file/100-GB and P95 load,
complete parser-overlap admission, and physical derived-index GC remain open.

## 2026-10-02 optional LAN HTTPS entry

The optional Nextcloud Nginx gateway is running on the host's private LAN
interface at `https://10.106.105.128:18482` with the existing WeKnora LAN
certificate. The certificate chain and IP SAN verified against
`../weknora-ldap-local/certs/weknora-lan-ca.crt`. Nextcloud trusts the exact
gateway container IP, and its CLI browser origin is the HTTPS URL. The stock
Nextcloud Apache image rewrites `REMOTE_ADDR` from `X-Real-IP`, so the gateway
explicitly clears that caller-controlled header and sends a reset
`X-Forwarded-For` chain; Nextcloud can then validate its trusted proxy and
`X-Forwarded-Proto`. A spoofed forwarded-host, protocol and real-IP request
still redirected to the configured HTTPS origin.

CA-verified requests returned 200 for the HTTPS login and status endpoints,
301 with relative `Location` for CardDAV/CalDAV discovery, 401 for anonymous
WebDAV and 207 for an authenticated WebDAV `PROPFIND`. The login page's
generated absolute canonical and icon URLs were HTTPS, its CSS and JavaScript
resources returned 200, and its cookies carried `Secure`. The original
`http://10.106.105.128:18082` login remained 200 with HTTP canonical URLs.
The address is assigned to an active host interface, Docker Desktop listens
on that exact address and port, and the host application firewall is disabled.
The repeatable HTTPS helper passed nine focused offline cases and two live
idempotent runs, retaining only the exact current gateway IP in
`trusted_proxies` and a mode-600 local state file for later recreation.
These are local requests to the bound LAN interface; a second physical LAN
client and a trusted-CA browser have not yet been tested. Existing indexed
citations retain their old HTTP origin until the documents are reindexed.

## 2026-10-02 bounded parser-overlap code follow-up

The parser-overlap fixed-c6 and RAG77 patch SHA-256 values are
`a7cd69f12332f9f881999038f84df6f6bc7a41141cce875fe3e6ef3499b73fad`
and `84afeffc37040b2a85c2c795c05600817ea738b7862decc22f0310b4a0b23a2f`.
These overlap hashes identify the currently running shared RAG77 images; the
earlier measured pilot used its separately recorded patch. Both overlap
patches apply to clean
pinned source bases and pass their Nextcloud repository, service and handler
Go tests.

The dispatcher may now admit a newer signed, importable upsert for the same
file while an older version is parsing, after the older source scan completes.
It requires a changed ETag and binding-relative path and allows only one
unfinished parser generation or live build lease before the replacement. A
third generation waits until a slot is genuinely released. Focused race tests
prove prompt V2 queue admission, denial of late V1 publication and writes,
no applied ACK during V2 parsing, and ACK only after V2's exact publication
proof. This narrows the parser wait observed in the earlier three-sample pilot.
The full pinned arm64 app and UI images were subsequently rebuilt with this
patch and exercised in an owned, loopback-only synthetic LDAP fixture. The
[redacted overlap evidence](evidence/rag77-parser-overlap-2026-10-02.json)
records signed same-file V1 and V2 events: while V1 was still parsing, V2
reached the receiver dispatched watermark, but neither side advanced its
applied watermark. After release, V2 alone became visible with two ready
chunks and embeddings, and both sides applied event #2. Alice's direct
knowledge read and selected search denied the baseline and stale V1 candidate.
The old V1 still had one chunk and embedding in the fixture database at the
final read, so physical derived-index GC remains unproved. The owned fixture,
its volumes, network and temporary credentials were destroyed. This one-file
probe is not a latency, scale or real AD acceptance run.

At that stage, the shared LAN stack ran the same full app and UI image IDs,
`sha256:e0b2b932dca4d988930ac1972ec582ad883e155f4c044f00d9230baa592804f7`
and `sha256:e9d606d5115dc5c93f2328f671d0fe2c12c8cdc1126ca7c73cd0f64c8d1cd458`.
Before switching, the receiver had zero active or unknown event connections
and Nextcloud had zero sender rows. The previous running images remain under
`pre-overlap-20261002` tags. The verified, mode-600 shared database backup is
`dist/backups/weknora-before-rag-20261001T225001Z.dump`. Those containers
were healthy at schema `130/false`; both LAN HTTPS entries returned 200 with
the local CA, the WeKnora application health endpoint returned 200, the
original Nextcloud HTTP login returned 200, and an authenticated Nextcloud
HTTPS WebDAV `PROPFIND` returned 207. All eight PR checks passed at
`5664b6b`. No fresh shared-service parser-overlap, second physical LAN-client,
sustained latency or scale acceptance run has been made.

## 2026-10-02 manual and scheduled sync admission recovery candidate

At this checkpoint, the fixed-c6 and RAG77 patch SHA-256 values were
`08b6f45addf7d753d8df1962bda538444e0290e2acbf5db26d2eb56eebe468d2`
and `a74bc9739a43cdc668c44201ac8221fed69c85642d46a8ca75533b6e0ce0cee1`.
They add PostgreSQL migration 131 and SQLite migration 50. Manual and
scheduled sync logs retain their exact queue task ID, trigger and worker-start
claim. The worker claims a versioned log before source I/O; an Asynq retry of
the same task can reclaim that log. After five minutes, a bounded rotating
scan releases an unstarted slot only when the exact Asynq task is definitively
absent and a row compare-and-set still proves no worker started. Redis recovery
requires `WEKNORA_NEXTCLOUD_SYNC_RECOVERY_ENABLED=true` after all queue workers
have this claim logic; the single-process Lite queue recovers automatically.
Legacy logs and started attempts still require manual investigation. Both
patches applied to fresh pinned bases and passed focused Go package tests.
The patch also classifies ETag-raced HTTP 412, 429/5xx and interrupted source
content reads as retryable: a failed event log keeps its retry/backoff and
does not turn an active source into `error`, while 401/403 still indicate
credential failure. A source-status/cursor compare-and-set protects normal
and pre-stream completion from a concurrent administrator pause or resume;
on a missed update, the run cannot yield an applied event ACK. The Agent graph
tool now holds a KB read lease across answer formatting and checks each
displayed chunk against current source/version and user scope. Physical
derived GC remains disabled. The complete RAG77/AnyDoc candidate built as app
`sha256:d0e695504b503034744b8a08388d0818b14fc3fb04ef30bc59a8d2638357915d`
and UI
`sha256:545b6ff7321931f63e8c8ebb85aa2fa0cbca2d29aa953f1fef285f77b0c52f71`.
Both images carry the pinned RAG source and full patch hash. A read-only dump
of the shared PostgreSQL 17 database was restored to an isolated container;
the candidate migration advanced the clone from `130/false` to `131/false`.
Six new `sync_logs` columns, its index and old-row defaults passed checks,
while the 20 sync logs, two data sources and three knowledge bases kept their
row counts. The clone's container, network and volume were removed without
modifying the shared database.

A fresh owned direct-mode synthetic LDAP fixture used these exact app/UI
image IDs and installed the pinned groupfolders 22.0.6 package. It published
one Team Folder document with two ready chunks and two embeddings. The
six-field HTTP matrix allowed Alice's login, DAV, signed source, WeKnora LDAP
login, knowledge and search; Bob could log in but could not access the file
or knowledge. After a real file-level Team Folder `-read` ACL for Alice, her
old JWT lost DAV, signed source, Files status, ask-target, direct knowledge,
search and historical answer/citation access on the next poll. Bob remained
denied. Its owned containers, volumes, network and scratch were destroyed.
This is isolated AD-shaped LDAP and groupfolders evidence, not real enterprise
AD or production Team Folder acceptance.

A ten-sample post-accept queue pilot attempt was invalidated by a signed
source GET HTTP 412 on a same-file update; the captured error was overwritten
by fixture revocation before a precise receiver error code could be retained.
The harness now captures receiver state before cleanup. A later two-pair
minimal rerun completed five applied events and five successful sync logs
without reproducing 412. Neither run establishes a P95 latency result.

The complete candidate then ran a ten-sample full-image `post-accept` pilot.
All 21 signed events reached applied, all 21 sync logs succeeded, and the
source stayed active without a reproduced 412. The ten-file initial sync
created ten items in 1.448 seconds. The nearest-rank event-to-durable-job P95
was **10,101.2 ms**, 101.2 ms above the PRD's 10-second target; the
event-to-applied-proof P95 was 20,467.4 ms. The eighth sample took roughly
one extra poll interval, but its cause was not established. The full
[redacted pilot report](evidence/event-queue-pilot-2026-10-02-syncfix-full-image.json)
contains all samples and limits. Its private environment was destroyed.
This small run does not accept sustained latency or 10,000-file/100-GB scale.

All eight PR checks passed at `610d2d1`. Before the shared LAN upgrade,
WeKnora had zero active/unknown event connections and running sync logs;
Nextcloud had zero sender rows. The script saved and verified the mode-600
backup `dist/backups/weknora-before-rag-20261002T044132Z.dump`, and prior
app/UI images remain tagged `pre-syncfix-20261002`. The shared WeKnora app
and UI now run the exact candidate image IDs above, the database is
`131/false`, the app health endpoint and both LAN HTTPS login entries return
200, the original Nextcloud HTTP login returns 200, and authenticated
Nextcloud HTTPS DAV `PROPFIND` returns 207. After verifying only one shared
app worker was running, the local Redis admission recovery flag was enabled
and the app/frontend/gateway restarted healthy. No live uncertain-enqueue
fault or second physical LAN client was tested on the shared stack.

Use synthetic data only in this local stack. Enterprise documents require the remaining publication, identity, permission, security and operational acceptance work above.

## 2026-10-02 dispatch contrast and file-publication reconciliation

The full RAG77 image passed two disposable ten-sample `post-accept` runs with
read-only hop diagnostics. At the default five-second dispatcher interval,
event-to-durable-job nearest-rank P95 was 10,076.4 ms; setting only the
WeKnora dispatcher interval to one second reduced the isolated P95 to
5,977.8 ms. Both runs applied all 21 signed events and had 21 successful
sync logs. Inbox timing around the queued recheck is consistent with a
missed five-second polling phase, but the short dispatcher claim was not
sampled. The [pilot record](isolated-event-queue-pilot.md) and its two
redacted reports carry the samples and limits. App image, host and harness
were the same, but each run used a new binding/source and only ten samples.
This is not sustained PRD latency or 10,000-file/100-GB acceptance.

The shared LAN development WeKnora app now has
`WEKNORA_NEXTCLOUD_EVENT_DISPATCH_INTERVAL=1s` in its local override. The
upgrade script saved a verified private PostgreSQL dump, kept the candidate
app image `sha256:d0e695504b503034744b8a08388d0818b14fc3fb04ef30bc59a8d2638357915d`,
and returned it healthy. The WeKnora and Nextcloud LAN HTTPS entries returned
200. Faster polling increases idle scan frequency; sustained database load
has not been measured.

An isolated indexed file exposed a separate reconciliation gap: withdrawing
one file immediately denied source access but did not append a change hint.
Two manual syncs within the configured full-scan interval each reported zero
items and left the old source version published. Nextcloud app 0.4.34 now
appends a durable, hint-only `reconcile` row after each administrator
withdrawal or republish, including idempotent retries. A failed append is
reported without hiding the already committed state. The new PHP failure
contract and HTTP outbox assertions were added to CI. The shared local app
was upgraded from 0.4.33 to 0.4.34 after a verified private DB dump;
maintenance mode is off, its container is healthy, and authenticated LAN DAV
returned 207. Its runtime archive has SHA-256
`301e9d529c1e9fd75dc0641969fa8a37692b8597afb64a1a6397923cc8977807`.

The [redacted publication/GC drill](evidence/nextcloud-publication-reconcile-gc-2026-10-02.json)
has SHA-256 `5be4afcbef61a45a156f278cf0a7a295de75e451b8b7992e4d61b4d202bc9959`.
In the disposable pair, repeating the withdrawal wrote a broad hint; the
first manual scan marked the file missing, and the second tombstoned its
source version. A signed manifest excluded it and signed content returned
404. After a disposable app restart, the old chunk and enabled embedding
remained because the derived-index GC job was blocked and the coverage
marker was absent. Republish restored a new published candidate and signed
content. This proves the fail-closed deletion gate on one synthetic file,
not completed physical derived cleanup. The broad hint also updated all ten
unchanged files. Real AD, sustained load, full read/build coverage and
physical derived cleanup remain open.

## 2026-10-02 file-scoped publication reconciliation candidate

Nextcloud app 0.4.35 now writes a `reconcile` hint with the affected
`file_id` after withdraw and republish. WeKnora still reads the complete
authoritative manifest, but forces content re-read only for that file. A
binding-wide hint with a null file ID retains the previous full-content
fallback. The receiver still requires two complete manifests before
acknowledging these hints; focused fixed and RAG77 Go tests cover both rapid
withdraw/republish orderings and prevent a one-scan ACK. Both full patches
apply cleanly to their pinned source archives. The full RAG77/AnyDoc candidate
backend image is
`sha256:7bca5e4408bf34849f0aadea989f519ab2537583de0bbd3d3ef2b1bc8e579b06`.

The [redacted targeted publication drill](evidence/nextcloud-publication-targeted-2026-10-02.json)
has SHA-256 `ea5541c4237d8c2454dc3de0351d9271d0d5ae592fa9c9d6663266b4f5521eb0`.
Its disposable source held eleven published synthetic files. Withdrawal took
two manual scans to tombstone the target; each scan processed zero or one
item instead of updating ten unchanged neighbors. Republish and both rapid
reversal orderings also processed at most one item per scan, with the target
eventually published or tombstoned as requested. No broad hints were written.
After reinstalling a disposable event connection, WeKnora acknowledged the
replayed baseline through event 16, then the two rapid reversals through 18
and 20, and the final restore through 21. The observed source version matched
the final state at each applied watermark. The event credential and both
isolated stacks were removed. These 256-byte-file checks do not establish
100-GB throughput, sustained latency, real AD ACLs or physical derived
cleanup.

The shared LAN stack was upgraded after confirming zero active WeKnora event
connections, zero Nextcloud sender rows and zero running sync logs. A verified
mode-0600 WeKnora dump (`weknora-before-rag-20261002T080705Z.dump`, SHA-256
`338a2b7e3ddc09fa3eb296583e0a01f64ba87f3d553287eb0c4f0c9de2ae826a`)
preceded the backend/frontend switch; the previous images remain tagged for
rollback. A verified mode-0600 Nextcloud dump
(`nextcloud-before-targeted-20261002T080845Z.dump`, SHA-256
`961a50f3c5b3b8a1d79257091f9c1434372271acba525114fe87e4c51b8ca47e`)
preceded `occ upgrade` to app 0.4.35. The reproducible app archive has
SHA-256 `f333a675a5dbc69a3deb613293ecb862b2b79e22fa4edb6d1bfc84cf03bca76f`.
Nextcloud maintenance mode is off, no DB upgrade is pending, and both app
containers are healthy. The host-to-LAN-IP HTTPS login pages returned 200,
authenticated Nextcloud DAV returned 207, and the publication HTTP smoke
passed with the local CA and a separate login on the Files citation origin.
A second physical LAN client has not yet been tested.

## 2026-10-02 pending source-pairing recovery guidance

The administrator page in app 0.4.36 now shows operation-specific WeKnora
status and retry paths only while the selected source pairing is pending. It
clears them when the binding or state changes, and states that a WeKnora
tenant administrator must use the approved WeKnora origin. The Nextcloud page
does not proxy a WeKnora administrator credential, read a one-time token or
claim that it performed the retry. The headless pairing protocol and
two-sided reconciliation remain the authority.

Before upgrading the shared local app, a mode-0600 Nextcloud dump
(`nextcloud-before-pairing-ui-20261002T083701Z.dump`, SHA-256
`b18e41e206c809f3758e4a4a9034dea929d66355afa65d093eb26156d1cf7e7c`)
was verified. `occ upgrade` completed to 0.4.36 with maintenance mode off;
the authenticated administrator page and its JavaScript asset returned 200,
and the panel markup was present. The reproducible 0.4.36 app archive has
SHA-256 `36e3ca0cc46ff065da314c88493acca57306ce8fc1b78cba2d6489c4536cb431`.
This UI check did not create a pending production pairing or test real AD.

## 2026-10-02 synthetic 10,000-entry manifest check

An offline PHP contract now calls the real `ApiController::manifest` and
`ManifestSnapshotService` with a synthetic 10,000-file metadata tree. The
[redacted result](evidence/manifest-10k-synthetic-2026-10-02.json) has SHA-256
`329b807ab9b41afbc0e13b245d11a27cdc3a3aabff16a64ba20ab1350447b79f`.
All 50 pages had unique, ordered IDs, and stored pages did not rescan the
tree. The recorded run serialized a 2,600,001-byte snapshot, took 41.27 ms
for the first page and 574.25 ms for the other 49 pages together, and reached
35,573,760 bytes of PHP peak memory. Root ETag, withdrawal revision,
stop/resume epoch, snapshot expiry, invalid cursor, and an unreadable node
failed closed. A local rerun passed the same assertions.

This uses in-memory file and database substitutes in an isolated PHP
container. It does not measure Nextcloud filecache or PostgreSQL I/O, HTTP,
WeKnora indexing, real AD/Team Folder permissions, or 100 GB of content. A
real 10,000-file/100-GB pilot and sustained latency target remain unaccepted.

## 2026-10-02 derived cleanup safety follow-up

The fixed-c6 and RAG77 full patch SHA-256 values are now
`02a77dcf42dc28871c344885251c514f834759aa64139fbbfebb7478c2875fa6`
and `176a514658adbe949ec5f12490fda4f48655cda5abd65e3e943c5ca21720acef`.
`SaveChunkRevision` now checks a persisted Nextcloud knowledge marker and
requires an exact live build lease before a chunk edit; its update is scoped
to the tenant, KB and knowledge ID. The dormant PostgreSQL exact-vector GC
helper rechecks the persisted job `not_before` while holding its row lock.
Both baselines passed focused SQLite tests and disposable PostgreSQL vector
tests, including a regression that failed against the prior vector helper.

Both patches also contain a dormant exact-chunk-and-revision delete helper.
Its scope, claim, delay, item inventory and atomic receipt passed SQLite and
disposable PostgreSQL tests, including a forced receipt failure rollback.
It is not called by the collector, and the migration still creates no
coverage activation marker. Unknown concurrent writers, unenumerated read
paths and external stores leave the `derived_index` blocker active. The
[lease-gate checklist](nextcloud-derived-gc-read-lease.md) records the
remaining preconditions; these helpers do not establish physical cleanup
acceptance.

## 2026-10-02 disposable real Nextcloud 10,000-file manifest pilot

An isolated, loopback-only Nextcloud 34.0.4 stack with app 0.4.36 accepted
10,000 WebDAV `PUT` requests for 19-byte synthetic files and held exactly
10,000 live matching filecache rows. The five measured upload batches took
391.815 seconds in total, excluding gaps between batches (25.52 files/s).
The [redacted pilot result](evidence/manifest-10k-real-2026-10-02.json) has
SHA-256 `a8e85bf0678ec7f85392ccdc75e6402cc4a45fbe2928a67bea7303de4b8b9eba`.

The real HTTP manifest returned 50 successful pages of 200 unique files.
The first page took 202.04 ms, the remaining pages took 2,812.21 ms in
total, and per-page nearest-rank P95 was 82.67 ms. The largest stored
snapshot JSON was 3,577,448 bytes. A file overwrite and an administrator
withdrawal each invalidated an old cursor with HTTP 409; the file was then
republished. The three saved snapshot rows match the three first-page
requests, although SQL tracing was not performed. The dedicated containers,
volumes, network and private credentials were removed and the port released.

This is a single small-text-file manifest pilot. It does not test 100 GB of
originals, WeKnora indexing, mixed file types, real AD/Team Folder ACLs or
sustained event-to-job P95. Those PRD acceptance gates remain open.

## 2026-10-02 RAG77 image rebuild and LAN switch

The pinned RAG77 build now downloads DuckDB's signed `httpfs`, `spatial`, and
`excel` archives for the engine's reported version and platform, decompresses
the official `.duckdb_extension.gz` files, then installs and loads them
locally with DuckDB's default signature check. Both pinned source patches
apply cleanly; an arm64 Docker check loaded all three extensions. The first
full image build reached AnyDoc/Go compilation but an alternate Debian
mirror lost the `gcc-12` download. A container-network check found a working
mirror; the retry completed the full AnyDoc backend and frontend builds.

The backend image is
`sha256:ac6c3a5c975886ada55fbcd0c65058516fa4ac7d245a9a32c656d39e428b9365`
and the frontend is
`sha256:b8f0c6dc13847c48f2a5b6e7e08777389b272f1736ad0d45053d7cb3579d1d7b`.
Both carry pinned source commit `77c97fd72f26e84435503d24eeed88cb5dfe1f01`
and the exact RAG patch SHA-256
`176a514658adbe949ec5f12490fda4f48655cda5abd65e3e943c5ca21720acef`.
Scoped BuildKit cache cleanup after the build preserved images and data
volumes and restored about 12 GiB of host free space.

Two owned, loopback-only synthetic LDAP/PDF fixtures exercised the new image.
The forced-short-primary run recovered 46 DocReader characters to 283 AnyDoc
characters; the normal run kept DocReader's complete text. Each published
two ready chunks and embeddings, returned a protected answer with the
original Nextcloud Files citation, and denied the old JWT's source, question,
direct, search, and historical-citation reads after source-share revocation.
The fixtures' containers, volumes, and networks were removed. The
[redacted build and LAN record](evidence/weknora-rag-duckdb-lan-2026-10-02.json)
has SHA-256 `1a3ad18a5912ff75f1a731070fbd8ba013aa0892f7f913d002c943f96e3ea5b3`.

Before switching the shared WeKnora stack, both databases were healthy,
schema 131 was not dirty, all seven event connections were revoked, Nextcloud
had zero sender connections, and no sync logs were running, pending, or
queued. The old backend/frontend images remain tagged `pre-duckdb-20261002`.
The switch script made a mode-0600 WeKnora dump, verified its archive list,
then recreated the app/frontend and restarted the LAN gateway. The dump's
SHA-256 is `d85509b9e541920170d946c2a32adb8cb5d03c70723a781d7bf82aba29f0ed86`.
The running containers now use the new exact image IDs, WeKnora remains at
schema 131/clean, and the local-CA-verified WeKnora and Nextcloud HTTPS
pages return 200. Nextcloud still runs app 0.4.36 with maintenance mode off;
authenticated DAV over the LAN URL returns 207. A second physical LAN client,
real enterprise AD/Team Folder permissions, 100-GB content, sustained task
latency, and derived-index physical GC remain unaccepted.

## 2026-10-08 local LAN address rotation

Docker Desktop restarted after the host's private address changed from
`10.106.105.128` to `10.106.105.121`. The existing containers resumed, but
their old host-IP port bindings were absent. After a mode-0600 backup of the
local settings and public certificate, the WeKnora leaf certificate was
reissued under the existing local test CA with `10.106.105.121` in its SAN.
The local WeKnora gateway and external URL, Nextcloud LAN listener and HTTPS
gateway, `trusted_domains`, `overwrite.cli.url`, and `weknora_web_url` were
updated to the new address.

Both HTTPS gateways now publish only on `10.106.105.121` (`18482` for
Nextcloud, `18443` for WeKnora); Nextcloud HTTP also publishes there on
`18082` and on loopback. CA-verified HTTPS login/root and WeKnora health
requests returned 200, Nextcloud HTTP login returned 200, and an
authenticated Nextcloud HTTPS DAV `PROPFIND` returned 207. Nextcloud remains
34.0.4 with integration app 0.4.36 and maintenance mode off. The WeKnora
backend/frontend image digests are unchanged from the 2026-10-02 entry.
An independent Docker bridge client with the test CA also reached both HTTPS
endpoints and received 200. A second physical LAN client and enterprise AD
have not been tested. The later `scripts/rotate-lan-ip.py` helper passed 13
offline tests and read-only checks against the current Compose overlays,
running image/config hashes and certificate. Its mutating path has not been
exercised on the shared stack; this address change was performed manually.

## 2026-10-08 isolated tiny mixed-format timing

An owned, loopback-only synthetic LDAP fixture at Nextcloud commit
`d1c48929` measured two tiny text PUTs (500/505 bytes) and one born-digital
PDF PUT (969 bytes), sequentially after the preceding event applied. Each PUT
created two outbox upsert hints and one successful source sync job. From just
before each PUT, the first observed durable-job bounds were 3,461.5 / 5,464.5 /
5,434.5 ms; published, current-ETag, parsed, ready-chunk and embedding bounds
were 4,678.3 / 7,832.2 / 8,398.9 ms; WeKnora applied-watermark bounds were
13,406.9 / 15,008.0 / 14,644.7 ms. The PDF's protected synthetic text was in
a ready chunk. These are host-monotonic upper bounds with 0.5-second status and
database polling. The three sync logs themselves lasted about 0.17 seconds;
the 6.25–8.73-second publication-to-applied observation gap is the next
latency component to isolate.

The fixture used local AD-shaped LDAP and mock embedding. Its Nextcloud
container ran the repository event-delivery command in an owned, manually
launched five-second loop because the synthetic fixture has no dedicated
event-worker service. The existing WeKnora image carries patch SHA-256
`176a514658adbe949ec5f12490fda4f48655cda5abd65e3e943c5ca21720acef`,
while the fixture checkout at `d1c48929` had `integration/weknora.patch` SHA-256
`b81174da65cbfcc4bebe72d9b49e78a60bfc58e56c97d500236c32f2f70b07c2`.
The [redacted per-file evidence](evidence/isolated-mixed-format-load-2026-10-08.json)
has SHA-256 `2bd5f8fd12b5e4c58e6aedb0ff68c48e5c0efb25785341e2a6682c4b40610863`.
The owned containers, volumes, network and private scratch were removed.
This three-file run does not accept the current RAG patch or the PRD's
sustained P95, 10,000-file/100-GB, upload-latency, scanned-PDF or real-AD targets.

A subsequent read-only scheduler trace found that the tested image queues
applied-proof work for five seconds after dispatch commits, while its default
dispatcher ticks every five seconds. A queue commit just after a tick can miss
the next tick and wait nearly ten seconds for proof evaluation, consistent
with this run's 9.21–9.95-second dispatched-to-applied observations and older
five-second-poll samples. The fixture did not record its dispatcher setting,
so that attribution remains an inference. The shared LAN overlay explicitly
uses a one-second dispatcher interval, and an older isolated comparison
observed about six seconds with that setting; see the
[event queue pilot](isolated-event-queue-pilot.md). The PRD's separate event-to-job
and tiny-text publication targets do not impose a ten-second applied-watermark
limit. A future isolated A/B run should record the configured interval and
safe tick/due/proof timestamps before changing the scheduler.

## 2026-10-08 pinned patch safety and complete frontend checks

Both pinned WeKnora patches now recheck the persisted coverage marker and
active exact/KB leases inside the dormant exact-chunk delete transaction.
The generated-profile repository write also requires
`ever_had_nextcloud_source=false`, so a source registration that commits while
the model is running prevents the stale profile from being saved. Focused
SQLite and disposable PostgreSQL race tests passed on both source baselines;
these guards do not activate physical derived-index deletion.

The full frontend suites exposed missing failed-candidate translations in
Korean, Japanese and Russian and one theme-token fallback. After repair, the
RAG77 suite had 1,217 passes, zero failures and one skipped browser-harness
case; the fixed baseline had 1,213 passes, zero failures and the same skip.
Both passed type checking and production builds. An opt-in PostgreSQL
migration test now verifies the full 0→131 chain and agent-history plan in
its terminal database, then tests the legacy 106→105→106 concurrent-index
roundtrip in a separate schema. The intentional 126/129 rollback guards were
not changed. Both pinned baselines passed this PostgreSQL and the relevant
SQLite migration/pairing tests; both CI jobs now run the PostgreSQL check.

The fixed-baseline patch SHA-256 is
`db842d71468fd663347e70898e20db982293a34af599df400bf368abe19a5021`;
the RAG77 patch SHA-256 is
`34c560e03bbc20d4eff3c4ce9d652389f2a4f6056e738d01b8cdc0c05ee2c877`.
These are source and test results. The shared LAN WeKnora containers still
run the 2026-10-02 image built from patch SHA-256
`176a514658adbe949ec5f12490fda4f48655cda5abd65e3e943c5ca21720acef`;
no current patch image switch or full production acceptance is claimed.

## 2026-10-08 local publication-decision audit pointer (proposed 0.4.37)

Nextcloud migration 21 adds `decision_audit_id` to each current file
publication state. A withdraw or republish now writes its audit row and updates
the current-state pointer in the same database transaction, under the existing
publication ordering lock. The administrator state response exposes that local
audit ID; a point read rejects a missing or mismatched audit row (source,
action, actor, or timestamp). Existing rows retain `0`, meaning that their
current decision has no proven audit link; the next administrator action
establishes one. The migration
does not infer history from old audit rows.

`weknora_pub_audit` currently has no retention job. Its current referenced
row must remain available for the pointer check. Any future 90-day metadata
retention implementation must preserve referenced rows or replace the pointer
and its verification in one safe migration; the PRD's 90-day policy is not
implemented here. The controller still appends its reconciliation hint after
the state transaction; a hint failure leaves a committed withdrawal in place,
and an exact retry can append another hint. The outbox remains a separately
retained hint feed, and its consumer watermark is not this decision ID. This
local link does not record automatic source deletion, permission generations,
document content revisions, consumer publication, or a restore checkpoint.
Backup recovery still requires an external post-checkpoint withdrawal record
and full reconciliation while reads remain denied.

The migration and real writer/reader transaction contract passed in an
isolated, networkless PHP container with an in-memory SQLite database,
including legacy `0`, rollback after either audit or pointer write failure,
and mismatch rejection. The existing controller hint-failure contract also
passed. After a private 7 MB PostgreSQL dump, the shared local Nextcloud test
stack upgraded successfully to integration app 0.4.37. `occ status` reported
maintenance off and no pending DB upgrade, the new PostgreSQL column existed,
and the LAN HTTPS status endpoint returned 200 with a valid test-CA chain.
This shared upgrade did not exercise a live withdrawal/republish transition or
change the WeKnora runtime image.

## 2026-10-08 ordinary empty-result chat guard correction (source patches)

An isolated diagnosis of a normal document-KB chat found that an empty
`search_knowledge` result was treated as missing source identity and stopped
the live answer before the model request. The current fixed/RAG patches now
accept only the producer's explicit empty search/list shape or a diagnostic-only
tool failure. They still run the current message/reference and KB-grant check,
and continue to deny opaque or nonempty content without document identity.
The incremental fix had focused access/service/session and complete session
package tests on merged-main plus the preceding RAG patch source. Both pinned
combined patches passed static apply checks and the GitHub patch suites; the
`c1c45cf` run passed all eight CI jobs, including the isolated Nextcloud
Docker smoke and direct upgrade. At that source-only stage the shared image
predated the correction. The subsequent deployment and runtime checks are
recorded below.

## 2026-10-08 built and running RAG77 chat correction

The shared local app and UI now run the complete pinned RAG77 + patch
`7f9c7f6a0027e42a4dd01b33366051ee7533aeddb9369653b83653165d5b5517`:
app `sha256:be9fa7b1514edbbe97e99253aaeb4f38f138c964a4317e414732711c63f5b46d`,
UI `sha256:42649eebce2a6f4b55b6dff59b9e9170a8ee73534523b666ceb932bf56128e54`.
The normal backend build links AnyDoc and includes the BrowserSkill daemon;
the normal frontend build uses its exact verified Nginx base. Runtime system
info reports `77c97fd72f26e84435503d24eeed88cb5dfe1f01+nextcloud.7f9c7f6a0027`.
PostgreSQL remains `131/false`. This is the pinned RAG baseline, not newer main.

An owned isolated manual-document fixture completed two zero-result turns in
one session and a third diagnostic-only tool-failure turn. All three emitted
answer and complete frames with no source-stop or error; six local chat-stub
calls were observed and the fixture was removed. The [redacted HTTP evidence](
evidence/rag77-empty-chat-http-2026-10-08.json) identifies the exact image.
After deployment a new, empty manual KB and local mock model also completed
an actual shared-stack question; its four temporary resources were removed.
The existing administrator authenticated, both LAN HTTPS chains verified, and
Nextcloud authenticated DAV returned 207. Its app remains 0.4.37 with maintenance
off and no pending database upgrade. [Runtime evidence](
evidence/rag77-chatfix-shared-runtime-2026-10-08.json) records the observed scope.

Before the image switch, the corrected cold-checkpoint helper captured both
databases, eight cold volumes, runtime inputs and app code: 16 private artifacts,
1,594,309,659 bytes. Independent SHA and permission checks passed; the manifest
SHA-256 is `3ad12fbd4edbefb33b3986ab375c0d85deb6bd7a4518d74e16f79f068f9c7bae`.
Two earlier attempts refused stopped-port metadata and produced no usable
backup. Their original services were restored before the validated lifecycle
fix and final capture. The old app/UI images remain under `pre-chatfix-20261008`
tags. Capture integrity does not prove restoration or withdrawal replay.

This running revision does not include subsequent Agent-history, source-lineage
schema132 or frontend dependency updates. Full source/version/history coverage,
derived GC, real AD/Team Folder and sustained 100GB acceptance remain open.

## 2026-10-08 isolated original-volume pilot harness

The [original-volume harness](../scripts/ops/PILOT-original-volume.md) now
creates 20 local synthetic staff users, two ten-member department groups and
two independent normal-folder publication scopes. Both scopes use one isolated
publisher, preserving the app's current rejection of unverified cross-owner
bindings. Its opt-in full preset streams exactly 10,000 synthetic binary files
of 10,000,000 bytes each, with host and Docker data capacity preflights,
64-KiB generation chunks, native-file/filecache/manifest count and byte checks,
permission samples, and exact ownership checks for teardown. It can separately
measure matched app-disabled/enabled PUT phases in a temporary unbound folder.

The [small runtime report](evidence/pilot-original-volume-small-2026-10-08.json)
has SHA-256 `df912a1a9485e589a5cb9176866305194435ad0a5d612f4b40a6bfd752c71d52`.
On Nextcloud 34.0.4/app 0.4.37, 402 new 1-KiB originals produced exactly 411,648
logical bytes. Native files, filecache and four total manifest pages agreed;
four original hashes matched. All 20 local staff principals were allowed for
one sample in their own department and denied for one sample in the other;
both sibling machine-key reads were denied. Uploading the corpus took 79.803
seconds with two workers. The separate 20-sample PUT phases measured P95
510.892 ms disabled and 376.176 ms enabled, with unresolved phase-order/cache
effects and no concurrent signed event sender or parser. Seventeen offline
safety tests passed. A post-run Docker inventory found no owned containers,
volumes or networks, and the private credentials had been removed.

The full 100-GB preset has not run. This small synthetic binary result does
not accept AI parsing/indexing throughput, mixed document distributions, real
AD/Team Folder ACLs, twenty-user concurrency, sustained upload or event P95,
physical derived cleanup, or coordinated restore. Shared stacks and WeKnora
source patches were not changed by this pilot.

## 2026-10-08 frontend dependency security in the combined source patches

The pinned source patches now include the frontend dependency updates on top
of the interim history guards and dormant lineage foundation. Production npm
audit findings fell from 13 to zero; the full dependency audit fell from 14 to
zero, including the development-only shell-quote finding. KaTeX 0.18.2 is
shared by the direct, Markdown-extension and Mermaid consumers through a
targeted override; Mermaid remains at 11.16.1. A persistent regression rejects
inherited `trust` and verifies all three consumers resolve the same package.

Combined-source frontend suites, type checks, builds, audit results and the
limited synthetic browser rendering comparison are recorded in
[frontend dependency validation](weknora-frontend-dependency-security-2026-10-08.md).
These are source and frontend validation results. The frozen 7f images and
schema 131 runtime do not contain this source update. No new full image build,
shared migration, physical GC activation or complete V1 acceptance is claimed
by this dependency integration.

## 2026-10-08 complete isolated original-volume preset

The [full original-volume report](evidence/pilot-original-volume-100gb-2026-10-08.json)
records 10,000 new 10-MB synthetic originals: exactly 100,000,000,000 bytes.
Native regular files, PostgreSQL filecache and 50 complete manifest pages
agreed on counts and bytes, with 10,000 unique file IDs and four sample digest
matches. Two local department groups of ten staff users had 20 own-department
allows, 20 cross-department denials and two cross-binding machine denials.
The two upload workers completed the corpus in 2,520.502 seconds; a separate
UI build near the end makes corpus timings observational. The earlier
20-sample disabled/enabled comparison also retains phase-order/cache limits.
See the [harness record](../scripts/ops/PILOT-original-volume.md) for exact
image IDs, capacity samples and metric scope. All owned resources and private
credentials were removed, verified by a second Docker inventory.

This closes the isolated original count/storage preset. It does not accept
WeKnora parsing/indexing, event latency, real AD/Team Folder ACLs, sustained
upload impact, 20 concurrent users, physical derived cleanup or complete
recovery. Earlier entries stating that the preset was unexecuted describe
their earlier verification dates.

## 2026-10-08 frontend dependency candidate deployed

The shared UI now runs `sha256:3e5feb7e70f4fda72f270435a85a60656615689df14a02572e57e6996586d1ca`,
built with the normal pinned frontend Dockerfile from RAG77 plus source patch
`ea5b19f668308ba54330a380493e6bf771dd4e0889c317738ac87e9a1769bcea`.
Compared with the previously running 7f frontend, only package.json,
package-lock.json and the dependency security regression differ; application
page source is unchanged. The old UI remains under `pre-security-20261008`.
Static artifacts and an isolated Nginx frame-policy contract passed, including
200 with exact CSP for the allowed page and 403 for the denied page.

The fresh hidden browser rendered the login page and its local/AD selectors.
LAN HTTPS login/status and the local administrator's API authentication
returned 200 with CA validation. [Runtime evidence](
evidence/weknora-frontend-security-runtime-2026-10-08.json) binds these checks
to the actual image. The backend remains
`sha256:be9fa7b1514edbbe97e99253aaeb4f38f138c964a4317e414732711c63f5b46d`
(RAG77 + 7f), with schema131. This frontend deployment does not activate the
new Agent-history backend or schema132 lineage producers/readers.

## 2026-10-08 prebuilt local service digest references

The RAG local overlay now pins PostgreSQL, Redis, DocReader, OpenLDAP and the
LAN gateway to the exact digest artifacts previously running. DocReader uses
a digest-only reference; no floating latest tag remains for that service.
The source-built app/UI remain local candidate aliases, whose actual image
IDs and distinct source hashes are recorded separately. Full source release
locking remains a deployment gate for subsequent built candidates.

Before changing the references, both local stacks produced a matched private
cold checkpoint with 16 artifacts totaling 1,528,644,011 bytes. Its independent
SHA/mode verification passed; manifest SHA-256 is
`7911dff3dff8a8f8586c0c0facbbff91b01e0354a44691eda03559d1ac2a8248`.
The five prebuilt containers were recreated from the same immutable images,
and both stacks returned healthy. Nextcloud remained 34.0.4/app0.4.37 with
maintenance off and no DB upgrade. CA-verified LAN checks returned 200, local
WeKnora authentication returned 200, and authenticated Nextcloud DAV returned
207. WeKnora remains schema131/false with the same RAG77 backend and updated
security UI. [Pinning evidence](
evidence/weknora-prebuilt-runtime-pins-2026-10-08.json) records exact references
and IDs. The new checkpoint was captured and verified, not restored; it does
not establish external replay or complete application recovery.

## 2026-10-08 ranked raw search and response denial correction

The current pinned complete c6/RAG77 patches now accept only the two finite
reranking diagnostics (`base_score` and `model_score`) in addition to exact
persisted source metadata. Every persisted key, channel, knowledge base,
publication and durable read lease remains checked. A response latches its
first authorization or lease denial, so later success cannot release its
protected JSON. The original false-403 and transient-denial counterexamples
are covered by persistent regressions.

Both complete pinned trees passed the entire session package independently
with local model stubs. Clean application reconstructs the exact frozen tree
for each profile, and reverse application reconstructs its exact base. CI now
explicitly includes the raw-search regressions. See the [correction record](
nextcloud-raw-search-ranked-metadata-2026-10-08.md) for source hashes and scope.
These are source checks; the shared backend remains RAG77 + 7f/schema131.
No new image deployment, live event acceptance, full lineage rollout or
physical derived cleanup is implied.

## 2026-10-08 stream poll-error authorization correction

A real `ContinueStream` counterexample reproduced a protected buffered tail
being emitted after withdrawal during an event-store read failure. The error
branch now rechecks the message's current publication before releasing that
tail; denial, unavailable authorization and cancellation discard it, while a
still-authorized request retains its tail. The original controls and all three
negative cases were reused, red before the correction and green afterwards.
Both pinned complete source profiles passed their whole session package
(370 test/subtest results, zero fail/skip) and vet. Full patch reconstruction
and reverse checks passed. [Source evidence](
nextcloud-stream-poll-failure-authorization-2026-10-08.md) records the exact
hashes. Runtime and full lineage/GC acceptance remain unchanged.

## 2026-10-08 bootstrap job-registration ordering

The stream-fix commit `29bc245` had seven successful CI jobs and one failing
PR-triggered local smoke. The failing assertion expected exactly one installed
OutboxRetentionJob; its old message did not report the observed count. The
other complete run on that same commit passed, but does not replace the failure.
Pinned Nextcloud source registers jobs before final app activation and removes
an unresolved job class. Running cron concurrently with initial app enable is
a possible class-resolution race; that inference is recorded without claiming
the missing count was observed.

Bootstrap now stops cron and integration workers before enabling the app,
checks exactly one retention job after the route is ready, then starts the
workers. The HTTP assertion remains strict and now includes the observed
count. [Failure record](
evidence/nextcloud-bootstrap-job-registration-2026-10-08.json) preserves the
original job and log digest. A new full CI run must verify this ordering; the
shared local runtime was not bootstrapped by this source change.


## 2026-10-08 captured application recovery and RAG baseline

The [captured application restore drill](shared-application-restore-drill.md)
now passed actual Nextcloud, WeKnora and LDAP startup in owned closed resources.
All eight volume archives were compared before database startup; application
identity, bindings and source versions remained unchanged. Thirty containers,
ten volumes and one internal network were precisely removed. External
withdrawal/ACL ledger replay and reopening remain unaccepted. The imported
implementation passed 82 offline safety contracts; one optional Docker test
is skipped by default.

The [RAG build baseline](weknora-rag-adaptation.md) now pins b6ea8b56, including
the upstream runtime-package retry fix and explicit evaluation model selection.
The complete integration patch is byte-identical to the preceding 77c97 patch
and has exact-tree apply/reverse proof on the new baseline. Local focused
checks passed 149 tests/subtests with one optional PostgreSQL fixture skipped.
New baseline remote CI and an actual candidate image are separate checks.
The shared schema-131 backend and the private Phase 3 candidates remain separate.


## 2026-10-09 external publication journal candidate

App 0.4.38 adds an atomic metadata-only recovery journal and external signed
retention with database-prefix and witnessed-head fork detection. A pinned
external record can be attached before coordinated shutdown after matching
the live instance/stream/prefix. Actual isolated Nextcloud installation, admin
publication, simulated backup rollback and maintenance-only replay/retry passed;
bindings stayed stopped, withdrawn files stayed excluded and original DAV bytes
remained intact. The fixture was removed. [Implementation and boundary](publication-recovery-journal.md)
and [measured evidence](evidence/publication-recovery-journal-2026-10-09.json)
record the exact package and post-measurement validation change. Shared runtime
still uses app 0.4.37. WeKnora closure, authoritative full reconciliation and
reopening remain separate incomplete gates.

## 2026-10-09 reproducible WeKnora development candidates

Two separately pinned complete candidate patches are now submitted under
`integration/candidates/`, with an exact-tree verifier and their own CI matrix.
The [candidate description](weknora-development-candidates.md) separates the
measured source gates from outstanding full-app, physical-GC, capacity and
performance acceptance. FAQ/enrichment tests passed 317 cases per profile,
with no failures or skips, and both complete source trees compiled. The final
recovery/default-prompt gate passed another 17 cases per profile and full compile.
The shared schema131 stack and default build selections are unchanged.

The [live joint recovery evidence](evidence/coordinated-recovery-live-20261009.json)
records actual dual PostgreSQL rollback, exact external plan replay, closed
receipts and fresh signed source reconciliation using live Nextcloud and nested
synthetic LDAP. Its WeKnora parser/index rows are fixtures; global ingress
reopening and full external-index/application recovery remain unaccepted.

## 2026-10-09 selected-KB FAQ/tag HTTP lease update

The complete candidate patches now hold selected-KB read fences through FAQ/tag
input capture and actual response writes, and knowledge collection requests
refuse a missing durable lease store. The two candidate combinations passed
367 tests each with no failures/skips and complete Go compilation; owned PG
resources were removed. A malformed FAQ filter still returns 400 before lease
admission, correcting the first combination's one failed fixture per profile.

The preceding submitted candidate `324425d` passed both complete CI runs
(37827990151 and 37827979194): all 12 jobs green, including 337 candidate cases
and frontend builds per profile. The newer source is separately pinned in the
[candidate manifest](../integration/candidates/manifest.json). Cross-KB parent
read coverage and the actual local-object `RunDue` adapter remain pending; this
update does not enable physical derived GC or change the shared runtime.

## 2026-10-09 running Nextcloud source isolated from development

LAN verification observed `needsDbUpgrade=true`: the source checkout had
advanced to app0.4.38 through its direct bind while the database stayed0.4.37.
Earlier shared-runtime observations described their check time and did not
account for this later bind-source drift. The running app and all three workers
now use an exact separate0.4.37 snapshot, matching the database; no migration
was run. Maintenance is off, no DB upgrade is pending, and CA-verified DAV207
passed. The cold checkpoint now fingerprints/archives the actual source bind,
with all app processes required to agree. See [source isolation and evidence](
nextcloud-runtime-app-source.md). New candidate deployment remains pending.

## 2026-10-09 local capacity write-intent candidate

The isolated capacity148/SQLite67 module is included in both candidate patches.
Actual local partial files, registration/staging failures, two concurrent writers
and subprocess crashes are recovered through durable write/inode receipts and
the owner CLI. Unknown/legacy objects remain unavailable; orphan bytes stay
charged to the target KB. The root combination passed398 cases per profile and
full Go compilation, with four failure nodes caused by an old HTTP fixture not
loading schema67. Loading that migration resolved all eight tests in the affected
HTTP class on each profile; production code did not change for that repair.

The sourcepin commit0cb7738 PR CI passed all six jobs, but its push RAG candidate
failed the10,000-entry HTTP fixture with context cancellation around a heartbeat.
The fixture now uses production WAL/busy settings, and actual concurrent capture
and lease renewal plus the10,000-entry response passed on both profiles. The
original failure is retained; rollback-journal contention is an inference from
the observed timing, not yet an isolated reproduction of that exact CI failure.
The new matrix adds actual PG/SQLite capacity intents and owner CLI checks.
Actual objectGC fencing, full parent/read coverage and complete backend capacity
remain pending; the shared app is the fixed0.4.37 runtime snapshot.

## 2026-10-09 template, FAQ all-parent and actual object-GC combination

Both separately pinned candidates passed529 test/subtest cases, zero failures
and zero skips, plus complete Go compilation in owned PG/SQLite fixtures. The
code includes actual-admin/code/derived KB template writers and guarded reads,
original FAQ progress/results, metadata-only all-parent read plans, transaction-
bound single-connection authority checks, and actual RunDue local-object content
fences/provider ACK. The [candidate evidence](evidence/weknora-development-candidates-2026-10-09.json)
retains the original smaller-PG failure and duplicate fixture DDL failures, and
the final successful combination. All owned resources were removed; caches kept.

The ordinary build script still selects the default adaptation; the explicit
`WEKNORA_RAG_CANDIDATE=1` path verifies the candidate manifest/tree and builds
separate app/UI image tags without starting services. Candidate deployment and
full normal machine-ingestion acceptance remain pending. No production coverage
marker or global recovery ingress is enabled. Local derived/vector inventory,
remaining readers/history and the PRD performance requirements are still open.


## 2026-10-09 candidate CI and local derived follow-up

Submission `beb2646` passed all 12 push/PR jobs. Retained artifacts contain547
producer/recovery test and subtest cases per candidate with zero failures/skips,
complete Go compile and frontend checks. Later candidate sources add exact local
backend writer/retirement inventory, atomic native SQLite/PG row receipts, local
path replacement/crash fences and mandatory Wiki source policy. Current
verification results and original failed fixture runs are separately pinned in
[candidate evidence](evidence/weknora-development-candidates-2026-10-09.json).

PRD §7.4 allows90-day retention only for body-free metadata/audit. Original
JSON body purge, external backend inventory, machine first-ingestion, complete
saved-history readers, candidate app recovery and measured performance remain
open. Global physical coverage remains closed. Shared Nextcloud stays on its
separate0.4.37 source snapshot.


## 2026-10-09 guarded admin cleanup candidate

The selected-KB administrator panel now has expiry, bounded task paging,
category/count preview, readable deferrals, retry and immediate guarded
collection.32 actual PG/SQLite/operator/HTTP cases passed, including true local
unlink and late admin revocation; coverage and retention remain enforced.
Body purge and full external inventory remain incomplete. See [admin scope](
nextcloud-gc-admin.md). Submission8426485 passed all12 jobs, including566/35/11
producer/local-derived/native-vector cases per profile with no failures/skips.
The previous frontend typecheck selected only the empty root configuration;
new CI explicitly checks the app project, which passed in the new owned source.
Full personal history/machine pipeline modules and candidate application
build/restore/performance are still open; shared services remain pinned.


## 2026-10-09 original saved-message core candidate

151/70 message origins, material receipts, owner/body/membership counters and
metadata-only parent read sets now connect the actual message service, saved
HTTP reads and queued history-to-knowledge indexing. Current actor/share checks
use transaction-local repositories, and saved output holds source leases and
rechecks current authorization. Both integrated candidates passed285 selected
cases with0fail/skip across actual PG/SQLite/message/HTTP/FAQ/collection/local
GC paths. The owner's broader core compile passed154 packages; two old PG
negative skips in its earlier log are explicitly retained, and the root run
executed them. [Core paths and remaining consumers](source-lineage-saved-message-materials.md)
list Agent/model lifetime, Continue/cache, clone/steer/memory/artifact and partial
field writers still underway.152 is only a hook until its actual module freezes.
The candidate is not deployed or accepted as full V1.


## 2026-10-09 actual machine input integration and image probe

150/69 machine original upload/name controls and final Summary/Question→Auto
origins are integrated with same-transaction production DI. Two candidates
passed84 affected actualmachine/HTTPspan/SQLitemigration cases after concrete
fixture repairs; original309pass/60fail combinations and CI migration-tail
failures remain in the evidence. The [machine gate](knowledge-machine-original-input-validation-20261009.md)
includes queue/model/tenant/source/actor/body negatives and credentials excluded
from immutable JSON and ordinary summary logs. Durable completion→queue handoff
is the next work package, using the existing pending-op mechanism.

Normal AnyDoc core app/UI images were built and an owned nested LDAP/NC/WK
stack performed real signed sync/indexing. Personal knowledge access failed403
with an attached tag lacking display origin; this is a retained failed probe,
not a release acceptance. All owned resources were cleaned. The new machine
image is being built for the repaired personal positive. Original body152 and
complete Agent/Continue/partial-field lifetimes remain in independent worktrees.
