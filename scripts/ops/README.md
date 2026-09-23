# Local operations probes

## Local Nextcloud source pairing

Install Nextcloud app 0.4.18 and deploy the WeKnora patch before this probe.
Keep the selected Nextcloud binding active with its original root. As a
WeKnora administrator, create an **empty, dedicated** knowledge base in the
same tenant; record its ID and the canonical positive decimal tenant ID. A
knowledge base that already contains a data source or knowledge document is rejected.
The WeKnora API derives the tenant from the authenticated administrator and
checks it against the Nextcloud intent; the CLI cannot choose a different
tenant through a request field. A legacy synthetic Nextcloud data source or
knowledge base created before source pairing cannot be adopted in place. Use
a newly created empty dedicated knowledge base and a binding that no existing
Nextcloud data source targets; do not
delete or reuse a knowledge base with permanent Nextcloud provenance.

Set `WEKNORA_TEST_ADMIN_EMAIL` and `WEKNORA_TEST_ADMIN_PASSWORD` for the local
WeKnora administrator. The CLI reads the local Nextcloud administrator
credentials from this repository's `.env`. Run from this repository:

```sh
python3 scripts/ops/local-source-pairing.py pair \
  --binding YOUR_UNUSED_BINDING_ID \
  --tenant-id YOUR_CANONICAL_TENANT_ID \
  --knowledge-base-id YOUR_EMPTY_DEDICATED_KB_ID \
  --nextcloud-machine-base-url http://nextcloud
```

The CLI prints an `operation_id` to stderr before sending requests. Save that
UUID for recovery. It calls Nextcloud's administrator
`POST /admin/bindings/{id}/source-pairing` with the operation ID, tenant ID,
and knowledge-base ID. HTTP 201 returns the prepared pairing and its machine
token **once**; an exact retry returns HTTP 200 without a token. The CLI
passes that token directly to WeKnora's administrator
`POST /api/v1/datasource/nextcloud-source-pairings` with
`knowledge_base_id`, `base_url`, `binding_id`, `operation_id`, `instance_id`,
`publication_epoch`, `key_id`, and `token`. It never prints the token.
WeKnora creates a paused pending data source and calls Nextcloud's signed
`POST /bindings/{id}/source-pairing/commit` using the dedicated operation key.
Only the exact pending source, target, operation, and key may commit. A stop,
root move, or publication-epoch change prevents a pending commit. HTTP 201
from WeKnora means active; HTTP 202 means still pending and unavailable for
sync or retrieval.

For an interrupted or pending operation, inspect or retry **the same** UUID:

```sh
python3 scripts/ops/local-source-pairing.py status --binding YOUR_UNUSED_BINDING_ID --operation-id YOUR_OPERATION_UUID
python3 scripts/ops/local-source-pairing.py retry --binding YOUR_UNUSED_BINDING_ID --operation-id YOUR_OPERATION_UUID
```

These use WeKnora's `GET /api/v1/datasource/nextcloud-source-pairings/{operation_id}`
and `POST /api/v1/datasource/nextcloud-source-pairings/{operation_id}/retry`.
The CLI reports both source and target states, the data-source ID, and a bounded
WeKnora error code when present, without secrets or remote error bodies. Its
success requires `active` on both sides. To close a **pending** operation on
both sides, run:

```sh
python3 scripts/ops/local-source-pairing.py abort --binding dev-published --operation-id YOUR_OPERATION_UUID
```

WeKnora signs the abort with its encrypted pending credential. Once Nextcloud
returns the exact aborted ACK, WeKnora removes the empty paused source and
keeps a credential-free operation tombstone. HTTP 202 means the outcome is
uncertain; retry the same UUID. HTTP 409 requires operator inspection. If the
one-time token never reached WeKnora, the CLI closes the Nextcloud-only
pending intent with administrator `DELETE /admin/bindings/{id}/source-pairing`.
An active pair cannot be aborted. A replacement uses a new operation UUID.

### Isolated pending-abort smoke

`local-source-pairing-abort-smoke.py` exercises a lost one-time key, a pending
WeKnora source abort, wrong-operation rejection, idempotent abort, and an
active Nextcloud pairing's abort rejection. Run it only against **two
disposable Compose projects**, with explicit loopback origins and the
isolated Nextcloud `.env` file. It rejects the shared development project
names. The WeKnora app must be attached to the isolated Nextcloud network,
where the isolated Nextcloud container's unique name is resolvable. The
script verifies their shared Docker network and passes that container name
to the relay, avoiding the ambiguous `nextcloud` alias on stacks attached to
multiple networks. Before starting that
WeKnora app, set `WEKNORA_NEXTCLOUD_DEV_HTTP=1` and approve its exact
`http://127.0.0.1:18089` relay origin in
`WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS`. The relay uses a preinstalled
`python:3.12-alpine` image and runs in the app container's network namespace;
it listens only on loopback, rejects one commit for the script's unique
binding and UUID, then forwards requests to the verified isolated Nextcloud
container. It prints no token.

```sh
python3 scripts/ops/test-local-source-pairing-relay.py
python3 scripts/ops/local-source-pairing-abort-smoke.py \
  --nextcloud-origin http://127.0.0.1:YOUR_ISOLATED_NC_PORT \
  --weknora-origin http://127.0.0.1:YOUR_ISOLATED_WK_PORT \
  --nextcloud-env-file /path/to/isolated-nextcloud/.env \
  --nextcloud-compose-project YOUR_ISOLATED_NC_PROJECT \
  --weknora-compose-project YOUR_ISOLATED_WK_PROJECT \
  --relay-port 18089
```

If the isolated WeKnora app service is named something other than `app`, pass
`--weknora-app-service ITS_COMPOSE_SERVICE_NAME`.

The smoke creates a unique folder, binding, and empty KB. On normal exit it
removes its relay and only those owned fixtures. If abort cannot be confirmed,
it retains the binding and KB for recovery and prints their IDs and operation
UUIDs without secrets. No existing binding or KB is selected for deletion.

### Rotate an active source credential

Save the original pair UUID, then run:

```sh
python3 scripts/ops/local-source-rotation.py rotate \
  --binding dev-published \
  --pair-operation-id YOUR_ACTIVE_PAIR_UUID
```

The CLI prints a new rotation UUID before sending requests. It moves the
one-time `rot_` credential from Nextcloud to WeKnora without printing it.
WeKnora first commits the new key remotely while the old key still works,
then atomically switches its encrypted source config, then requests remote
finalization. Finalization immediately revokes the old key. If any response
is lost, inspect and retry the same operation:

```sh
python3 scripts/ops/local-source-rotation.py status --binding dev-published --pair-operation-id YOUR_ACTIVE_PAIR_UUID --operation-id YOUR_ROTATION_UUID
python3 scripts/ops/local-source-rotation.py retry --binding dev-published --pair-operation-id YOUR_ACTIVE_PAIR_UUID --operation-id YOUR_ROTATION_UUID
```

The old key expires at most 24 hours after remote commit even when
finalization has not completed. Retry uses WeKnora's encrypted new credential
and repairs a pending or locally switched operation. A committed rotation
cannot be aborted. A pending rotation may be aborted with the same CLI and
`abort`; this asks Nextcloud to revoke the new key while retaining the old
one. If the token was lost before WeKnora stored it, the CLI aborts the
Nextcloud-only pending operation. Start a new UUID afterward. Rotation
changes the source config hash, so re-pair any optional event-inbox
connection pinned to the old source config.

Source pairing is separate from the event-delivery connection. It establishes
the intended binding, tenant, and dedicated knowledge base, but does not prove
that events were applied, documents indexed, employees mapped to AD identities,
or retrieval authorized. The event sender now verifies a separate applied
watermark before pruning connected hints; this still does not prove each
document is indexed or satisfy the AD acceptance gate.

## Local Nextcloud to WeKnora delivery probe

With both local stacks healthy, Nextcloud app 0.4.18 installed, and a new
synthetic data source actively source-paired as above, set
`WEKNORA_TEST_ADMIN_EMAIL` and `WEKNORA_TEST_ADMIN_PASSWORD` to the local
WeKnora administrator credentials. The probe requires that neither side has
an active event connection. For the default `dev-published` binding and local
ports, run:

```sh
python3 scripts/ops/local-event-pipeline-smoke.py --data-source-id YOUR_SYNTHETIC_NEXTCLOUD_DATASOURCE_UUID
```

To also wait for WeKnora to accept a full-source sync into its task queue,
using the patched dispatch worker, add `--expect-dispatch`:

```sh
python3 scripts/ops/local-event-pipeline-smoke.py --data-source-id YOUR_SYNTHETIC_NEXTCLOUD_DATASOURCE_UUID --expect-dispatch
```

The probe refuses an already active WeKnora or Nextcloud event connection.
It pairs the synthetic data source's event connection, saves the one-time event
credential in Nextcloud,
creates a temporary WebDAV file, executes the Nextcloud delivery job, and
compares the durable receipt watermarks on both sides. It removes the file and
revokes both test connections on exit. An existing retained outbox can require
several job executions to catch up. A matching watermark proves signed
delivery and durable inbox receipt, **not** dispatch, parsing, publication, or
an application acknowledgement. An old unpaired development data source is
denied by the patched WeKnora event receiver and cannot run this probe.

With `--expect-dispatch`, the probe polls the WeKnora connection status for up
to 120 seconds before cleaning up its temporary connection. It requires the
`dispatched_through_event_id` checkpoint to include the temporary file event,
which advances only after queue acceptance. A timeout reports the received and
dispatched IDs, dispatch state, and last error code. Queue acceptance still
does **not** prove that the source sync, parsing, or indexing succeeded.

For an isolated binding and a WeKnora stack published on another loopback
port, point the script at the existing binding root and container receiver:

```sh
python3 scripts/ops/local-event-pipeline-smoke.py \
  --data-source-id YOUR_PAIRED_DATASOURCE_UUID \
  --binding YOUR_SYNTHETIC_BINDING_ID \
  --dav-folder-url http://127.0.0.1:18082/remote.php/dav/files/devadmin/YOUR_SYNTHETIC_BINDING_ID \
  --weknora-base-url http://127.0.0.1:18086 \
  --receiver-origin http://wkprobe-app:8080 \
  --expect-applied
```

The Nextcloud development stack must allow the exact receiver origin in
`WEKNORA_EVENT_ALLOWED_ORIGINS` and enable `WEKNORA_EVENT_DEV_HTTP=1`.
`--expect-applied` waits for both WeKnora's applied watermark and Nextcloud's
separately verified signed applied watermark to reach at least the probe event
ID. It also delivers the temporary file's cleanup delete and waits for both
applied watermarks to cover that delete before revoking only the connections it
created. It does not assume either watermark begins at zero. A timeout reports
checkpoint IDs and Nextcloud's bounded applied error code. `--compose-directory`
and `--env-file` may point at the running Nextcloud checkout when the script
runs from a separate worktree.

## Local event connection probe

After building and starting the patched local WeKnora app, set
`WEKNORA_TEST_ADMIN_EMAIL` and `WEKNORA_TEST_ADMIN_PASSWORD` to the synthetic
local administrator credentials and run:

```sh
python3 scripts/ops/local-event-connection-smoke.py --data-source-id YOUR_SYNTHETIC_NEXTCLOUD_DATASOURCE_UUID
```

The probe pairs only that source, sends synthetic reconcile hints, tests
durable receipt, nonce replay, idempotent retry, rotation and revocation, and
revokes the created connection on exit. It refuses to replace an already active
connection. HTTP 202 is a receipt, not an applied or published event. Use a
synthetic data source only.

## Isolated backup and restore drill

Run `./scripts/ops/isolated-restore-drill.sh` from this repository to rehearse a **synthetic data-level** backup and restore. The script creates new, uniquely named Docker volumes and two PostgreSQL containers on Docker's `none` network. It never accepts a Compose project, existing volume name, database address, or production credentials. It verifies labels before removing only the volumes and containers it created. Backup files and comparison results remain in a new private temporary directory printed at the end. Pass a path that does not exist to choose the output directory.

The drill writes representative synthetic Nextcloud configuration, file and plugin-state files; WeKnora original, index-checkpoint and key *fixtures*; and small Nextcloud and WeKnora PostgreSQL tables. It takes separate `pg_dump` archives and volume tar archives, restores them into newly created volumes and a second database container, then compares database rows and SHA-256 file manifests. It also records archive checksums and elapsed time. It does not read or modify the running `nextcloud-weknora-dev_*` or `weknora-ldap-local_*` volumes.

This validates the backup tools, archive transport, isolated restore plumbing and simple data integrity checks. The WeKnora fixture uses plain PostgreSQL tables, so it does **not** validate ParadeDB/vector extension restore, application schema migrations, an application boot from the restored volumes, external object storage, LDAP identity, actual encryption keys, or the PRD's four-hour RTO. Redis queues are not restored in this drill. All data and keys are synthetic.

For an eventual coordinated environment recovery, pause publication and synchronization, preserve a cross-service checkpoint, and back up Nextcloud database/config/data/plugin state together with WeKnora database/originals/required indexes/keys. Restore into a **new** environment with retrieval and synchronization closed. Reapply withdrawal and deletion records, reconcile the full source manifest, and confirm instance UUID, bindings, source versions, current permissions and index state before allowing retrieval. An older database dump can precede a withdrawal; restoring bytes alone must never reopen that content. Record actual RPO and RTO only after this application-level exercise succeeds. The current script does not implement or certify that release gate.
