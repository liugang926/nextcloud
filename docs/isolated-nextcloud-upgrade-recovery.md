# Isolated Nextcloud app upgrade-failure recovery drill

Run from the repository root with local Docker and the full Git history:

```sh
python3 scripts/ops/isolated-upgrade-failure-recovery.py
```

The script generates a unique `nc-upgrade-recovery-*` Compose project, fresh
administrator and database passwords, a loopback-only HTTP port, and private
PostgreSQL, Redis and Nextcloud HTML volumes. It ignores the shared `.env` and
does not accept an existing project, volume, credential file or remote URL.
Before starting, it checks the rendered Compose topology, pinned images,
loopback listener and private credentials, then rejects any pre-existing
resources with the generated project name. It checks project ownership and
exact Compose labels before deleting any of its own resources.
The archive helper uses a pinned `python:3.12-alpine` digest; the script pulls
that exact digest if the image is not already cached locally.

## What the drill checks

1. Install the fixed Git `0.4.6` integration in a fresh Nextcloud 34.0.4
   stack. Upload a random-byte file through WebDAV. Record its file ID,
   original bytes, Nextcloud instance ID, binding configuration and a real
   integration publication-state row.
2. Stop Nextcloud, PostgreSQL and Redis. Archive the cold PostgreSQL data
   volume and the complete Nextcloud HTML volume, including config and user
   data, as one matched checkpoint. Record SHA-256 for both archives and the
   PostgreSQL volume owner. Redis has no synthetic queued work in this drill.
3. Restart the isolated stack and enter Nextcloud maintenance mode before
   replacing the temporary bind-mounted app copy. Its final migration gets a
   deliberate exception. `occ upgrade` must fail at that exception after
   reaching the late migration, while maintenance mode stays on and the prior
   binding configuration and publication-state row remain in the database.
   The half-upgraded container remains loopback-only and is stopped before
   the restore.
4. Change the isolated database probe value and source-file bytes after the
   failed upgrade. Tear down only the marker-owned disposable volumes, put
   the fixed `0.4.6` code back, recreate clean volumes, verify both archive
   hashes, clear any files that Docker prefilled into the two owned restore
   volumes, and restore the database and HTML archives before starting any
   application container.
5. Verify the original Nextcloud instance ID, installed and enabled `0.4.6`
   app, binding config, publication-state row, core WebDAV listing, source
   file ID and exact source bytes. The post-checkpoint mutations must be gone.
   Finally, remove the owned containers, network and volumes and verify they
   are absent.

The command prints a JSON result with the injected failure marker, version,
archive hashes and restored file hash. Generated credentials and file content
are never printed; the private archives are removed when the temporary
directory closes. A failed assertion triggers cleanup only while the project
ownership marker and Docker labels still match; otherwise the script refuses
to delete uncertain resources.

This is a local rollback **exercise** for a small synthetic Nextcloud install.
It restores a cold, matched physical database and HTML checkpoint with the
same pinned images. It does not certify automatic rollback, portable
PostgreSQL restoration, a WeKnora or LDAP recovery, queued-event replay,
external storage, production migration compatibility, or an RPO/RTO.
Production rollback must stop integration and retrieval, use a coordinated
cross-service checkpoint, reconcile post-checkpoint withdrawals and source
permissions, and prove the restored state safe before reopening access.
