# Local operations probes

## Local Nextcloud to WeKnora delivery probe

With both local stacks healthy, Nextcloud app 0.4.11 installed, and the
synthetic `dev-published` data source configured, set
`WEKNORA_TEST_ADMIN_EMAIL` and `WEKNORA_TEST_ADMIN_PASSWORD` to the local
WeKnora administrator credentials and run:

```sh
python3 scripts/ops/local-event-pipeline-smoke.py --data-source-id YOUR_SYNTHETIC_NEXTCLOUD_DATASOURCE_UUID
```

The probe refuses an already active WeKnora or Nextcloud event connection.
It pairs the synthetic data source, saves the one-time credential in Nextcloud,
creates a temporary WebDAV file, executes the Nextcloud delivery job, and
compares the durable receipt watermarks on both sides. It removes the file and
revokes both test connections on exit. An existing retained outbox can require
several job executions to catch up. A matching watermark proves signed
delivery and durable inbox receipt, **not** dispatch, parsing, publication, or
an application acknowledgement.

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
