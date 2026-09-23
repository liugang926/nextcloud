# Local operations probes

## Local Nextcloud source pairing

Install Nextcloud app 0.4.13 and deploy the WeKnora patch before this probe.
Keep the selected Nextcloud binding active with its original root. As a
WeKnora administrator, create an **empty, dedicated** knowledge base in the
same tenant; record its ID and the canonical positive decimal tenant ID. A
knowledge base that already contains a data source or knowledge document is rejected.
The WeKnora API derives the tenant from the authenticated administrator and
checks it against the Nextcloud intent; the CLI cannot choose a different
tenant through a request field. A legacy synthetic Nextcloud data source or
knowledge base created before source pairing cannot be adopted in place. Use
a newly created empty dedicated knowledge base and a new operation; do not
delete or reuse a knowledge base with permanent Nextcloud provenance.

Set `WEKNORA_TEST_ADMIN_EMAIL` and `WEKNORA_TEST_ADMIN_PASSWORD` for the local
WeKnora administrator. The CLI reads the local Nextcloud administrator
credentials from this repository's `.env`. Run from this repository:

```sh
python3 scripts/ops/local-source-pairing.py pair \
  --binding dev-published \
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
python3 scripts/ops/local-source-pairing.py status --binding dev-published --operation-id YOUR_OPERATION_UUID
python3 scripts/ops/local-source-pairing.py retry --binding dev-published --operation-id YOUR_OPERATION_UUID
```

These use WeKnora's `GET /api/v1/datasource/nextcloud-source-pairings/{operation_id}`
and `POST /api/v1/datasource/nextcloud-source-pairings/{operation_id}/retry`.
The CLI reports both source and target states plus the data-source ID, without
secrets. Its success requires `active` on both sides. If the one-time token was
lost before WeKnora stored the operation, a retry cannot recover it. Inspect
both sides and any in-flight request before aborting the **pending** Nextcloud
intent with administrator `DELETE /admin/bindings/{id}/source-pairing` and a
JSON `operation_id`, then start a new operation. An active pair cannot be
aborted this way. WeKnora currently has no pending source-pair abort or
source-key rotation endpoint; a pending WeKnora record needs repair and retry.

Source pairing is separate from the event-delivery connection. It establishes
the intended binding, tenant, and dedicated knowledge base, but does not prove
that events were applied, documents indexed, employees mapped to AD identities,
or retrieval authorized. There is no trusted applied-event acknowledgement or
AD acceptance gate yet.

## Local Nextcloud to WeKnora delivery probe

With both local stacks healthy, Nextcloud app 0.4.13 installed, and a new
synthetic `dev-published` data source actively source-paired as above, set
`WEKNORA_TEST_ADMIN_EMAIL` and `WEKNORA_TEST_ADMIN_PASSWORD` to the local
WeKnora administrator credentials and run:

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
which advances only after queue acceptance, and requires
`applied_through_event_id` to remain `0`. A timeout reports the received and
dispatched IDs, dispatch state, and last error code. Queue acceptance still
does **not** prove that the source sync, parsing, or indexing succeeded.

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
