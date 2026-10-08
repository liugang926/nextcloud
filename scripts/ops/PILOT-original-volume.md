# Isolated original-volume pilot

`pilot-original-volume.py` measures the PRD §9 original-file assumption in a
fresh Nextcloud stack. It provisions 20 **local synthetic staff** users, two groups
of ten users and two normal folders owned by the isolated publisher/admin,
each shared read-only with its department group. The current app intentionally
rejects multiple binding owners until a cross-owner mount model is verified.
This fixture preserves that restriction. It creates one plugin publication
binding per department, maps the local users to synthetic GUIDs, and checks
all 20 users' own-department allowance and other-department denial. The two
binding machine credentials must also reject access to their sibling binding.
The user authorization queries use one manifest file per department for each
principal, consistent with this fixture's uniform folder share; they do not
check every original against distinct per-file ACLs.
This fixture uses the app's explicit local-identity opt-in. It does not claim
real AD or Team Folder ACL acceptance.

The default corpus is 20 binary files of 1,024 bytes each. The explicit full
preset is **10,000 files × 10,000,000 bytes = 100,000,000,000 bytes** (decimal
100 GB). Files alternate between departments, so each department has exactly
5,000 files and 50,000,000,000 logical bytes in the full preset. Synthetic
data is generated deterministically in at most 64-KiB chunks and sent with
an exact HTTP Content-Length. No corpus copy is first written on the host,
and file bodies are never accumulated in the Python process.

Only the common publisher uploads. Twenty provisioned staff users do not mean
twenty concurrent uploads or question sessions. The binary fixture measures
original storage and metadata, and is not a representative mixed document
corpus for AI parsing or indexing. WeKnora is never started or contacted.

## Run a small check

Run from the repository checkout. Its pinned Nextcloud, PostgreSQL and Redis
images must already exist locally; the script uses `--pull never`. Use a new
state directory whose parent exists. Credentials and Compose configuration
are written with mode `0600` under a directory with mode `0700`.

```sh
python3 scripts/ops/test-pilot-original-volume.py
python3 scripts/ops/pilot-original-volume.py run \
  --state-dir /absolute/private/new-small-pilot \
  --file-count 20 --file-bytes 1024 --workers 2 \
  --upload-impact-samples 20
```

The optional upload comparison sends matched synthetic PUTs first with the
plugin disabled and then enabled, using the same file size and worker count.
It reports each phase's nearest-rank request P95 and their relative change.
The unbound comparison folder is removed, including its trash, before the
corpus begins. No signed event sender or WeKnora parser is active during this
comparison. Cache/order effects remain, and a short two-phase observation
does not establish sustained upload P95 or the performance of every file type.

## Run the original-volume preset

After the small check, and when no expensive image build competes for the
Docker VM's memory or disk, run:

```sh
python3 scripts/ops/pilot-original-volume.py run \
  --state-dir /absolute/private/new-100gb-pilot \
  --pilot-10k-100gb --workers 2 --upload-impact-samples 20
```

The full run has not been executed as part of adding this harness. A smaller
passing corpus must not be recorded as 100-GB acceptance. Custom corpora above
100 files or 100 MiB require `--allow-large-pilot`; this never bypasses capacity
checks. The script accepts at most four simultaneous uploads and uses container
memory limits of 2 GiB for Nextcloud, 768 MiB for PostgreSQL and 256 MiB for Redis.

Before creating Docker resources, and before every upload batch, available
host space must cover all remaining logical originals plus 20% overhead and
an 8-GiB reserve. A separate Docker Desktop data filesystem is checked when
present. The Docker data volume filesystem is checked after startup and before
every batch with the same requirement. Failure stops the pilot instead of
filling storage. Full-run headroom is at least 128,589,934,592 free bytes.
With 20 comparison uploads per phase at the full preset's 10-MB size, the
comparison preflight also includes 400 MB of temporary objects and requires
129,069,934,592 free bytes before those phases.

## Evidence and cleanup

`report.json` contains source revision, actual runtime image IDs/app versions,
upload timings and bytes, capacity samples, exact native regular-file counts
and sizes, allocated filesystem bytes, SHA-256 matches for four originals,
PostgreSQL filecache count/sum/min/max, and complete plugin manifest counts,
byte totals, unique IDs and page timings for both departments. It includes the
40 user authorization decisions and two rejected cross-binding machine reads.
No password, machine token, document body or private Docker environment enters
the report. A failure records a bounded stage and the cleanup outcome.

By default the stack is removed after success or failure and the report remains.
`--retain-on-success` keeps a successfully verified fixture for inspection:

```sh
python3 scripts/ops/pilot-original-volume.py cleanup \
  --state-dir /absolute/private/retained-pilot
```

Creation rejects any occupied project prefix. Cleanup verifies each resource's
unique owner token, Compose project/service/volume labels, pinned image, exact
mounts, loopback port and isolated network peers before removal. Docker Desktop's
exact `/host_mnt` representation of the owned app path is accepted on macOS.
Cleanup removes only those verified containers, volumes and network, then its
credential and Compose files. It never prunes images, build caches, other stacks,
model files or volumes discovered merely by a broad name search. If ownership
cannot be proved, it preserves resources and reports the refusal for review.

This pilot leaves AI parse/index throughput, event-to-job and event-to-ready
latency, real AD, complex permissions, sustained concurrency, physical derived
cleanup and coordinated recovery as separate acceptance work.

## Small runtime verification, 2026-10-08

The [redacted 402-file result](../../docs/evidence/pilot-original-volume-small-2026-10-08.json)
has SHA-256 `df912a1a9485e589a5cb9176866305194435ad0a5d612f4b40a6bfd752c71d52`.
It used Nextcloud 34.0.4 and app 0.4.37, with 20 local staff users and the
common publisher. Exactly 402 new WebDAV files of 1,024 bytes each produced
411,648 logical original bytes. Native files, PostgreSQL filecache and two
manifest pages per department agreed on count and bytes, with 402 unique
file IDs and four original digest matches. All 20 same-department decisions
allowed access; all 20 other-department decisions and both wrong-binding
machine reads were denied. The 402 PUTs took 79.803 seconds using two workers.

The separate 20-PUT phases measured nearest-rank P95 of 510.892 ms with the
app disabled and 376.176 ms with it enabled. The phase order, cache and
concurrent local workloads prevent interpreting this as a general speedup
or sustained performance acceptance. Only 1-KiB synthetic originals were
measured. The 100-GB preset remains unexecuted. Seventeen offline safety
tests also pass, including bounded generation, exact Content-Length transfer,
system-proxy bypass, ownership failures, cleanup, interrupt handling and
safe generated password prefixes. After the run, a separate Docker inventory
confirmed zero owned containers, volumes and networks; private credentials
were absent. Shared development stacks were not modified.
