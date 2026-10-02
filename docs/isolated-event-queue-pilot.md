# Isolated event queue pilot

On 2026-10-01, a disposable, loopback-only Nextcloud and WeKnora pair ran
`scripts/ops/pilot-load.py` with two 256-byte synthetic files and three
`post-accept` event samples. Nextcloud used app 0.4.32. WeKnora used the
`3ad3b31` RAG candidate with patch SHA-256
`251f416a0262fedeea74ef2962664e005122d950670e24792341f7cef5f77611`
and image ID
`sha256:2ec7e6b31463e6764130973cc1d636d2f65c83268376943cbae59713b7156f9e`.
The complete noncredential [JSON report](evidence/event-queue-pilot-2026-10-01.json)
has SHA-256 `868e4e35717a6f1d7f1ff061371e2cb4643c78885d6b92fac523149a2ffec1ee`.

| Measurement | Result |
| --- | ---: |
| Initial two-file source sync | 0.753 seconds; 2 files created |
| Event-to-durable-job upper bounds | 10,080.9 ms; 5,402.3 ms; 74,838.0 ms |
| Three-sample nearest-rank P95 | 74,838.0 ms |

Each event sample starts immediately before a WebDAV PUT and ends when the
WeKnora administrator status first reports a matching dispatched watermark.
The upper bound includes WebDAV and status polling. A preceding event was
already accepted into the queue, so each measured event exercises same-source
serialization. Between samples, the script waits for complete applied proof.

The third sample's status changed from `received=7, dispatched=6, applied=5`
at 20:35:44 to `publication_unproven` retry at 20:35:48; it dispatched event
7 at 20:36:53. Its scan finished successfully at 20:36:54.539 and its source
version published at 20:36:54.759. The applied watermark reached 7 at
20:37:58. Earlier successful scans also took about 65 seconds to advance
their applied watermark. This timing is consistent with the tested
candidate's one-minute proof checks, including its retry after publication
had not yet appeared when the scan completed. It does not establish that
this was the only source of event latency.

The run used a mock embedding model and a tiny local fixture. These numbers
are neither the PRD's 10,000-file/100-GB load acceptance nor a sustained
10-second P95 result. WeKnora's applied watermark is distinct from
Nextcloud's later signed status poll, which currently has a 30-second
per-connection interval. The pilot's owned containers, volumes and network
were removed after inspection; the shared LAN stacks were untouched.

## Five-second proof-poll candidate

A second disposable run used a Go backend built from RAG source `3ad3b31`
with patch SHA-256
`2c7da9519fc38d98b37a71660d8bb873d02c00af3d286984394dc70d32b6909c`.
For this event-only check, the new binary was layered on the preceding
runtime image without rebuilding AnyDoc or the UI; its image ID was
`sha256:a4b57893884cde2aee333fca7914e138331ae2eca83ec1471547a99ce6510bc0`.
The noncredential [JSON report](evidence/event-queue-pilot-2026-10-01-fastpoll.json)
has SHA-256 `4ae872412fab74c7d28d3c466aae8a96c0ad39538447d5650a65b5f6c6fb514a`.

The same reduced `post-accept` pattern measured 10,199.0, 80,049.6, and
4,944.2 ms; nearest-rank P95 was 80,049.6 ms. The event immediately before
the slow sample completed its source scan at 21:32:17.475. The next WebDAV
write arrived at 21:32:18.348 and changed the same file while the earlier
candidate was still parsing. Its final source publication check returned
HTTP 409 for the superseded ETag. The dispatcher kept the previous event
unapplied, then entered `publication_unproven` retry after 15 seconds and
waited one minute before dispatching the newer receipt. This preserves
authorization but still misses the proposed queue-latency target in this
small run. The second fixture and its private credentials were destroyed.

## Signed version/path proof candidate, 2026-10-02

A third disposable pair used Nextcloud app 0.4.33 and the newer RAG source
`3ad3b31` with final RAG patch SHA-256
`7b0b15ab9c9f93c6c707a13fa39cfc71d050b0b2095d132501584fad908601f4`.
The Go backend binary and SQL migrations were layered onto the earlier
AnyDoc runtime image for this event-only test. Its image ID was
`sha256:06252aef292fa8bdbeed988cdd52af64f56b5463e9da6b8f97b6d5889005b201`;
the original AnyDoc/UI were not rebuilt. The complete noncredential
[JSON report](evidence/event-queue-pilot-2026-10-02-coalesced.json) has
SHA-256 `790eb5a07c2749eb9117f299cf21fca4830d50d22c099b91b53ba5576e064416`.
The PostgreSQL event inbox contained seven signed hints, all with ETag and
binding-relative path.

| Measurement | Result |
| --- | ---: |
| Initial two-file source sync | 0.306 seconds; 2 files created |
| Event-to-durable-job upper bounds | 5,463.7 ms; 4,938.7 ms; 4,909.8 ms |
| Three-sample nearest-rank durable-job P95 | 5,463.7 ms |
| Event-to-applied-proof upper bounds | 15,306.3 ms; 14,820.0 ms; 15,270.9 ms |
| Three-sample nearest-rank applied-proof P95 | 15,306.3 ms |

The same `post-accept` pattern wrote each measured file version immediately
after a priming event's durable acceptance. The dispatcher can use the exact
signed version/path and latest completed scan to retire a superseded
candidate after its publication result. This test did not exercise every
possible timing of an older parser. The dispatcher still waits while an
earlier parser is running, and a bounded overlapping parser budget is not
implemented. This small mock-model run therefore does not establish the
PRD's sustained 10-second P95, 10,000-file/100-GB capacity, or enterprise
AD/Team Folder permission acceptance. The shared LAN WeKnora service was
not changed by this pilot.

## Pending-parser overlap fix after the pilot, 2026-10-02

Both pinned WeKnora patches now permit the dispatcher to admit a newer signed,
same-file upsert while the old version is still parsing. Admission requires an
exact changed ETag and importable relative path, a completed old source scan,
and at most one unfinished parser generation or active build lease for that
file. A further version waits for an older parser and its lease to finish.
The old candidate's write fence closes when the newer version stages, and the
applied watermark advances only after the newer version has its own source
cursor and publication proof.

Focused SQLite race tests verified a paused V1 parser, signed V2 receipt,
prompt V2 queue admission after the one-minute polling window, denial of a
late V1 publication, no ACK during V2 parsing, and ACK after V2 publication.
A second test verified that a third version waits while V1 and V2 remain
unfinished, even if V1 is marked failed while its build lease is still live.
The fixed and RAG source trees passed their Nextcloud repository, service and
handler Go tests. No new runtime image or latency pilot was run for this fix;
the measured results above belong to the preceding patch revision.

Subsequently, the full pinned RAG77 app and UI images were built with the
overlap patch. An owned, loopback-only synthetic LDAP fixture exercised signed
same-file V1/V2 events with the V1 parser held in progress: V2 dispatched
without an early applied ACK, then became the only retrievable candidate after
its own parse and publication proof. The
[redacted evidence](evidence/rag77-parser-overlap-2026-10-02.json) is one
functional observation, separate from the three-sample timing pilot above.
The new images were then installed in the shared LAN stack. This does not add a
new latency sample or prove stale derived-row physical collection.

## Full-image post-accept retry, 2026-10-02

A separate owned, loopback-only fixture attempted ten 256-byte synthetic files
and ten `post-accept` latency samples using the full RAG77 overlap image. The
initial bulk sync succeeded. The first same-file pair wrote priming event #1,
waited for its dispatched watermark, then wrote measured event #2. The inbox
contained both events and reached dispatched #2 without an applied ACK. The
first event's content GET subsequently returned HTTP 412 against its old ETag
after the second write, and the harness observed a blocked dispatcher. Its
automatic receiver revocation overwrote the original `last_error_code`, so the
exact block condition was not preserved. The run produced **no valid P95** and
does not extend the earlier timing result. All 11 owned containers, seven
volumes, network, worktree and temporary credentials were removed. The pilot
script now captures a bounded receiver state/error category before revocation
on failure, for a future isolated investigation.

A later minimal rerun with two 256-byte files and two `post-accept` pairs did
not reproduce the 412: inbox events #1–#5 all reached applied, both receiver
watermarks ended at #5, and all five sync logs succeeded. Its owned fixture
was removed. This does not explain the first failure or establish a P95.
