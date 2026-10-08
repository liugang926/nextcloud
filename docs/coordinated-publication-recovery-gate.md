# Coordinated publication recovery evidence gate

The cold checkpoint can now retain `weknora_recovery_inventory` alongside the
external Nextcloud journal anchor. The inventory binds all original operation,
tenant, KB, datasource, instance, binding, origin, configuration fingerprint,
publication epoch, key and pairing-state tuples, its independently retained
SHA-256, the source-state digest, and the exact valid replay lower bound.

Capture WeKnora's inventory with the schema145 maintenance CLI while ordinary
ingress and writers are stopped. Retain it in a private external directory. Add
these flags to the existing cold capture command, together with all three
external journal flags:

```sh
--weknora-recovery-inventory /absolute/private-external/weknora-inventory.json \
--weknora-recovery-inventory-sha256 <independently-retained-inventory-pin>
```

The helper validates the inventory's exact external journal anchor and checks
the live original WeKnora pairing registry before and after stopping writers.
It requires both schema145 recovery tables; an older schema131 shared stack
cannot claim this contract. Inventory and journal paths must remain outside all
captured inputs and checkpoint directories. At completion the inventory is
read and pinned again. The restore-side WeKnora Apply operation additionally
requires the full restored source-state digest to match the captured inventory.

After real restore, apply the same authenticated external plan to Nextcloud in
maintenance and to WeKnora as the database owner. Keep the ordinary services and
workers stopped. Nextcloud's maintenance receipt now contains concrete current
bindings, pairing tuples, withdrawals and exact persisted plan/boundary receipt.
`--inspect-state` re-reads that authority without replaying. A matching receipt
whose withdrawn file revived is refused.

WeKnora's CLI `--mode=snapshot` takes `--plan-sha256`, `--inventory` and its
independent `--inventory-sha256`. It re-reads every original source and scope,
checks exact tuple/config identity and all retired immutable generations, source
status, KB epoch and zero unknown GC coverage. Closed sources must actually be
paused, tombstoned and free of running workers. For reconciled scopes it obtains
fresh signed pair ACKs around two complete identical live Nextcloud manifests,
requires the persisted reconciliation's exact manifest, and resolves every
currently published generation through the actual lineage repository. The
output includes actual indexed chunk IDs from the relational catalog. It
contains no credential and creates no caller principal.

The WeKnora CLI reserves the exclusive private output before opening the DB.
An existing file, directory or link fails before Apply/Reconcile can mutate
state. Use a new output path for every fresh observation; identical stable
snapshots must agree on the entire source/scope body.

The owner-only verifier checks the independently pinned checkpoint and both
current snapshot artifacts:

```sh
python3 scripts/ops/coordinated-recovery-gate.py --phase=closed \
  --manifest=/absolute/private/checkpoint/manifest.json \
  --manifest-sha256=<independent-manifest-pin> \
  --plan=/absolute/private/plan.json --key-file=/absolute/private/journal.key \
  --nextcloud-state=/absolute/private/nc-closed.json \
  --nextcloud-state-sha256=<fresh-nc-observation-pin> \
  --weknora-state=/absolute/private/wk-closed.json \
  --weknora-state-sha256=<fresh-wk-observation-pin> \
  --output=/absolute/private/joint-closed-receipt.json
```

It rejects absent journal/inventory pins, incomplete checkpoint markers,
changed backup artifacts, mismatched plans/boundaries, partial closure or changed
original tuples. `--phase=sources` additionally requires every original source's
actual reconciliation, newer active NC binding epoch, matching complete stable
manifest and complete new published/indexed generations. A withdrawn file in
that manifest or a retired immutable ID in the new catalog is refused.

Both phases always return `ingress_reopen_permitted: false` and never start a
service. Full reopening still needs the authoritative current personal directory
and ACL gate and actual external index/backend reconciliation. A relational
`indexed` flag cannot establish external vector inventory. Continue applying
the ordinary current SourceGuard to every personal read. Obtain the snapshots
again at the final maintenance boundary; a retained old success is insufficient.

The isolated joint probe uses live Nextcloud and an AD-shaped LDAP with real
objectGUID reads and nested group sharing, stock PostgreSQL, actual maintenance
CLI Capture/Apply/Reconcile/Snapshot, actual repository/source guards and
`pg_dump`/`pg_restore` on both databases. Its WeKnora parser/index outputs are
controlled fixture rows and are explicitly scoped to that boundary; it does
not accept a complete deployed shared-stack restore.
