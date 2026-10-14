# External publication recovery journal

App 0.4.38 adds a metadata-only journal for publication recovery. New binding,
file-hint, explicit file decision and binding publication changes append to
`weknora_recovery_log` in the same business transaction. A locked head allocates
contiguous sequences and an event-prefix SHA-256 chain. Failed business/journal
writes roll back together. Migration 22 starts a new stream without inventing
historical coverage; older backups have no proven external replay boundary.

## Retain outside the backup

Use a separate private directory outside the Nextcloud/WeKnora checkouts,
restored volumes and checkpoint directories. Create a 32-byte key with mode
0600; its parent and journal parent must be owned directories with mode 0700.
Keep this key and journal independently from the coordinated application backup.
Run `watch` as a supervised external worker for five-second collection;
`collect` performs one bounded pass:

```sh
python3 scripts/ops/publication-recovery-journal.py watch \
  --journal /absolute/private-external/journal.jsonl \
  --key-file /absolute/private-external/journal.key \
  --container-id <exact-64-hex-nextcloud-container-id> \
  --compose-project nextcloud-weknora-dev
```

The collector inspects the exact running Compose container before and after
`occ integration_weknora:export-recovery-ledger`. It retains pages with an
external hash chain and HMAC, fsyncs records before advancing, and refuses
sequence gaps, wrong instance/stream, rollback, altered database prefixes and
replacement of previously witnessed page-head hashes. Interrupted partial
records require operator recovery from retained journal copies; they are not
silently truncated. Journal retention must cover the complete backup window.
Database history is not pruned by this implementation.

Before coordinated shutdown, obtain an externally retained terminal record pin.
Pass all three options to `shared-cold-checkpoint.py`:

```sh
--recovery-journal /absolute/private-external/journal.jsonl \
--recovery-key-file /absolute/private-external/journal.key \
--recovery-record-sha256 <externally-recorded-record-sha256>
```

The checkpoint validates that the pin is outside captured inputs, matches the
live instance/stream and database prefix, and is at or before its live head
before either application stops. It rechecks the retained pin before writing
`external_recovery_anchor` into the manifest. The manifest itself still needs
its independent SHA-256 pin. Omitted journal options preserve old capture
behavior and leave external replay unproven.

## Closed replay

Verify the independently retained recovery-through record. Generate an
authenticated plan from the pinned checkpoint record through that record:

```sh
python3 scripts/ops/publication-recovery-journal.py replay-plan \
  --journal /absolute/private-external/journal.jsonl \
  --key-file /absolute/private-external/journal.key \
  --checkpoint-record-sha256 <checkpoint-pin> \
  --through-record-sha256 <independent-recovery-pin> \
  --signed-plan-output /absolute/private-operator/plan.json
```

Keep both stacks' public ingress, ordinary cron and workers stopped. Restore
and verify the coordinated application checkpoint, leave Nextcloud maintenance
on, and run as the config owner:

```sh
php custom_apps/integration_weknora/appinfo/recovery-console.php \
  --plan=/absolute/private-operator/plan.json \
  --key-file=/absolute/private-operator/journal.key
```

Nextcloud's ordinary `occ` suppresses third-party commands during maintenance.
The dedicated CLI checks the config owner and maintenance, loads only the
closure command, verifies the HMAC/instance/stream/prefix range, stops all
restored active bindings and reapplies file withdrawals/deletions. A persisted
plan receipt binds retries; already withdrawn files do not add duplicate audit
rows. Upsert, eligible and resume journal events never authorize reopening.
Original Nextcloud files remain untouched.

File storage, LDAP and advanced ACL events are not all atomic with this journal.
Consequently current authoritative identity/ACL checks and complete manifest
reconciliation remain required. The independent WeKnora source-version closure
and replay receipt are also required before reopening that service. This module
does not mark full cross-service recovery accepted or start ingress.

## Verified scope

The packaged 0.4.38 isolated Nextcloud test exercised real admin binding creation,
DAV creation, transaction journal export, external signed retention, a simulated
backup-era DB rollback, maintenance replay, identical retry, stopped publication,
retained withdrawal and intact original DAV bytes. Fresh installation,
disable/re-enable and restart checks passed, and the disposable stack was removed.
The package and source evidence record the exact tested revision; later changes
require their own checks. Python tests cover signature/path/sequence/fork/head
commitment failures; the real PHP/PDO contract covers atomic rollback and pagination.


The first submitted CI's failure-recovery fixture refused its stale hard-coded
migration 21 pin. The repair moves the deliberate failure to migration 22 and
asserts all three recovery tables before restoration. An actual isolated
0.4.6→0.4.38 failed upgrade returned exit 5 at the injected marker, then cold
restoration recovered the exact instance, file ID/bytes, plugin state/config
and administrator route; all owned resources were removed. [Evidence](evidence/upgrade-recovery-migration22-2026-10-09.json)
retains the original failed job and the precise local repair scope.
