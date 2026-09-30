# Failed Nextcloud candidate retry

The WeKnora patch keeps a source file's current `nextcloud_source_versions`
row in `staging` when parsing fails. Its old published generation remains
retained but hidden. A failed row never becomes visible merely because an
earlier candidate finishes late.

The app dispatcher scans current failed staging rows every five seconds by
default, including rows whose source event was already acknowledged. It
persists a retry job for each tenant, knowledge base, data source, and external
file ID. The scan filters active sources and pairings, pages beyond malformed
rows, and continues other files if one row is invalid. The first retry waits
one minute plus jitter after parse failure; later attempts use capped
exponential backoff (1, 2, 4, …, 128 minutes) with 0–20% stable per-file,
per-candidate jitter. The automatic window ends 24 hours after the **first staging of the same ETag**. A replacement
candidate with that ETag retains the original timestamp and retry count.
After the window, the job moves to `manual` with
`candidate_retry_exhausted`. A stale running sync lasting 150 minutes also
requires manual review (`sync_stale_manual_review`). These are static codes;
parser output and credentials are not copied into the retry job.

The queue task carries the exact failed candidate, source tuple, ETag,
instance, binding, source configuration SHA-256, pairing operation/epoch,
sync log ID, and a database lease token. At task start and before source reads, WeKnora checks the current failed generation, active
source pair, source configuration fingerprint, and lease. A revoked, replaced,
or expired task cannot start or renew. A key rotation revokes the old claim;
Stage locks and rechecks the claimed failed generation, lease, current source
configuration, and pairing before replacing it, even if the fresh source fetch
has a newer ETag. A newer version committed by an ordinary sync makes the old
retry claim invalid. A lease cannot extend the 24-hour window; an expired job moves to manual review even when its next backoff time
is still in the future. The connector fetches a complete authoritative
manifest and re-downloads **that file** even when its ETag equals the stored
cursor; other unchanged files are skipped. After validating the complete
manifest, a retry task skips every other file before content GET, including
newly changed neighbors whose content endpoint fails. It also suppresses
unrelated deletion callbacks and leaves the shared
source cursor unchanged so the next ordinary sync still sees them. An ordinary
full sync or changed-file hint skips a failed file with the same ETag before
storing bytes or enqueuing a parser. Stage checks the exact leased retry again
in its transaction. The usual content API and Stage checks still compare the
live source. File-hash deduplication only matches the
current source-version candidate with the desired ETag, so a failed generation
or an older identical body cannot be reused. Stage atomically retires the old
generation before the new one becomes the current candidate. Publication
requires completed parsing and fresh source checks.

The administrator-only routes are:

```text
GET  /api/v1/datasource/nextcloud-source-pairings/{operation_id}/candidates/{file_id}/retry
POST /api/v1/datasource/nextcloud-source-pairings/{operation_id}/candidates/{file_id}/retry
```

`GET` returns the current failed candidate ID, ETag, retry state, attempts,
next attempt, first staging time, and static reason code. `POST` accepts
`{"source_etag":"...","candidate_id":"..."}`. It compare-and-swaps these
selectors against the current failed candidate, checks the exact active pair,
and restarts the retry window. A fresh source fetch and a **new** knowledge ID
are still required. The route requires an interactive tenant administrator
with edit access to the paired knowledge base; API keys cannot invoke it.

A transient fetch or ingest failure is recorded on the retry sync log and
leaves the paired data source active. After backoff, the same failed candidate
can be claimed again with a new lease. The endpoint does not manually publish
a failed candidate. A source that is deleted, moved outside the binding, paused, or no longer paired must be fixed
at the source before retry. A later ETag creates a new version and resets its
own retry window. The signed file-status response remains `failed` while the
current candidate has failed; the admin retry route gives the reason code.

Focused tests cover an event-free failed-candidate scan, durable lease and
candidate fencing, scan isolation beyond 64 invalid rows, a 24-hour cutoff
and administrator restart, unchanged-ETag content refetch, pre-create skip for
ordinary syncs, exact-file retry scope and unchanged shared cursor, transient
fetch failure recovery, key-rotation revocation, a distinct second generation,
and late completion of the old generation. Full LAN validation still requires an isolated source with
an induced parser failure and a healthy subsequent parse.
