# Disposable pilot load and queue latency probe

`pilot-load.py` creates synthetic files in a new binding, pairs a new dedicated
WeKnora knowledge base, performs one full source sync, and times independent
WebDAV upserts until WeKnora reports a durable queue-accepted event watermark.
It waits for the previous event's applied watermark before each next sample.
The fixture remains in its **disposable** stacks for inspection; the script
revokes its temporary event connection, and the operator removes both stacks
and their volumes after recording results.

The default is eight 1 KiB files and 20 events. The explicit
`--pilot-10k-100gb` flag selects 10,000 files of 10,000,000 bytes each (100 GB
decimal). Other custom loads above 100 files or 100 MiB require
`--allow-large-pilot`. Plan for substantially more than the payload size in
Docker disk space because the two applications and indexes keep copies.

## Prepare two fresh stacks

Use a **separate Nextcloud Git worktree** at the revision being tested. The
initializer refuses a main checkout, an existing `.env`, or an existing
state directory. Build a WeKnora image from the fixed baseline plus the
current `integration/weknora.patch`, including
`Transport.DisableCompression=true` in the Nextcloud connector. Record that
image's digest. The old `nextcloud-isolated-current` image predates the fix
and fails against Apache's gzip-suffixed ETag.

Choose unused loopback ports and unique `pilot-*` project names. The following
uses example paths and ports; the WeKnora config file is the ordinary
`config/config.yaml` from the patched checkout.

```sh
NC=/private/tmp/pilot-nc-worktree
STATE=/private/tmp/pilot-load-state
NC_PROJECT=pilot-example-nc
WK_PROJECT=pilot-example-wk
python3 "$NC/scripts/ops/pilot-disposable-init.py" \
  --nextcloud-worktree "$NC" --state-dir "$STATE" \
  --nextcloud-project "$NC_PROJECT" --weknora-project "$WK_PROJECT" \
  --nextcloud-port 18195 --weknora-port 18196 \
  --weknora-image weknora-pilot:current \
  --weknora-config-file /path/to/patched-WeKnora/config/config.yaml

docker compose -p "$NC_PROJECT" --env-file "$NC/.env" \
  -f "$NC/compose.yaml" up -d --wait
COMPOSE_PROJECT_NAME="$NC_PROJECT" bash "$NC/scripts/bootstrap.sh"
docker compose -p "$WK_PROJECT" --env-file "$STATE/.env" \
  -f "$NC/scripts/ops/pilot-weknora.compose.yaml" up -d --wait
```

The initializer writes mode-0600 random credentials outside tracked files.
The WeKnora Compose template exposes only its app on loopback, joins only the
two pilot networks, uses `pilot-models.yaml` against Nextcloud's synthetic
embedding service, and approves only `http://nextcloud` for source reads and
`mock-embedding` for model calls. The regular Nextcloud Compose file already
permits the test receiver `http://app:8080` on its own Docker network.

Register the first WeKnora user in this empty isolated database without
placing the password in a command argument or log:

```sh
python3 - "$STATE/admin.env" <<'PY'
import json, sys, urllib.request
values = dict(line.split('=', 1) for line in open(sys.argv[1]).read().splitlines() if '=' in line)
body = json.dumps({'username': 'pilotadmin',
                   'email': values['WEKNORA_TEST_ADMIN_EMAIL'],
                   'password': values['WEKNORA_TEST_ADMIN_PASSWORD']}).encode()
request = urllib.request.Request('http://127.0.0.1:18196/api/v1/auth/register',
                                 data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=30) as response:
    print('isolated registration HTTP', response.status)
PY
```

## Run and interpret

```sh
python3 "$NC/scripts/ops/test-pilot-load.py"
python3 "$NC/scripts/ops/pilot-load.py" \
  --nextcloud-origin http://127.0.0.1:18195 \
  --weknora-origin http://127.0.0.1:18196 \
  --nextcloud-env-file "$NC/.env" \
  --weknora-admin-env-file "$STATE/admin.env" \
  --nextcloud-compose-project "$NC_PROJECT" \
  --weknora-compose-project "$WK_PROJECT" \
  --nextcloud-compose-directory "$NC" \
  --embedding-model-id builtin-pilot-mock \
  --file-count 2 --file-bytes 256 --event-samples 3 \
  --report "$STATE/reduced-report.json"
```

Omit the three reduced-load flags for the default 20-event run. Use
`--pilot-10k-100gb` only on a deliberately sized disposable host; increase
`--sync-timeout`, `--event-timeout` and `--upload-timeout` as appropriate. The report path must be
new. It contains image IDs, fixture IDs and metrics but no credentials or
document bodies.

`event_to_durable_job.p95_ms` is nearest-rank P95 of host monotonic intervals
from just before a WebDAV PUT to the first observed WeKnora
`dispatched_through_event_id` that covers that file's outbox hint. It is an
**upper bound** that includes PUT time and 0.5-second status polling. A
dispatch watermark means a durable sync job was accepted, not that parsing or
publication finished. Applied checkpoints between samples prevent one queued
job from being counted as several independent samples.

`bulk_sync.logical_payload_bytes_per_second` divides known fixture bytes by
the successful `sync_logs` duration. This is a logical ingestion rate; it is
not measured network throughput or end-to-end indexing throughput. The
`VmRSS` baseline and sampled peak describe the entire WeKnora process, not
connector-only heap. Initial sync must create every file and all candidates
must publish before latency sampling begins.

## Reduced disposable evidence, 2026-09-24

Using Nextcloud image
`sha256:a5ace30c695afe48c2c406e940ee7886a81e13fa382e57cd68b2416d1a66914c`
and patched WeKnora pilot image
`sha256:c4f63fd9af774d6555265a2fd7672459d3c4a7c7e9542f1beab41220b447a465`,
the final 2 × 256-byte fixture created two source items in a successful
0.335-second sync (1,528.5 logical bytes/s). Five RSS samples observed a
343,703,552-byte process peak. Three independently applied event IDs
28/29/30 had observed queue latencies 6,073.6 / 5,069.7 / 5,266.1 ms;
nearest-rank P95 was 6,073.6 ms. The output was saved outside the repo in
`reduced-report-final.json` before deleting the disposable stacks.

This tiny sequential run does not establish the PRD's sustained 10-second
P95, 10,000-file/100-GB capacity, connector-only memory ceiling, or restore
and failure behavior. Run a properly sized pilot with representative file
types and concurrency before accepting those targets.

After inspecting a fixture, remove **only** the two explicitly named pilot
projects and their owned volumes, then delete the private env files:

```sh
docker compose -p "$WK_PROJECT" --env-file "$STATE/.env" \
  -f "$NC/scripts/ops/pilot-weknora.compose.yaml" down -v --remove-orphans
docker compose -p "$NC_PROJECT" --env-file "$NC/.env" \
  -f "$NC/compose.yaml" down -v --remove-orphans
rm -f "$NC/.env"
rm -rf "$STATE"
```
