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

## Current disposable Samba AD fixture

The current source identity verifier requires real AD `objectClass=user`, binary
`objectGUID`, account state and effective group membership. The coordinated
recovery runner now provisions an isolated Samba AD instance instead of relying
on OpenLDAP `adTestUser` mappings. It keeps identity bypass disabled, trusts only
the generated fixture CA, and reads Alice/Bob GUIDs from that actual directory.
Its nested Engineering/Platform group share is still exercised through normal
Nextcloud Files access before pairing.

Build the dedicated test image once; this does not replace the existing AD image:

```sh
docker build -t weknora-auto-space-ad-test:recovery-20261012 scripts/ops/fixtures/ldaps
python3 scripts/ops/isolated-coordinated-recovery-smoke.py \
  --weknora-source /path/to/current/WeKnora \
  --evidence /path/to/new-recovery-result.json
```

`--ad-image` selects another already-built local image. The runner resolves it to
an immutable image ID. Each fresh AD container refuses to overwrite an existing
`sam.ldb`; only its distribution-provided `smb.conf` is removed before provisioning.
No AD host ports are published. The two fixture networks use non-overlapping
`/28` subnets from the benchmark range, selected against current Docker networks;
Docker also rejects an allocation collision. Existing networks are never pruned.

This remains CLI/repository recovery evidence with controlled relational indexed
rows, not full-app/vector/model recovery acceptance. A failed preflight, process
or gate is a failed run; it cannot generate a successful result receipt. Partial
startup logs are retained privately with fixture passwords redacted before the
exact nonce-labelled temporary resources are removed.

### 回滚后的日志连续性

新的外部签名计划包含检查点之后的 `journal_events` 元数据。NC 在维护模式中验证实例、stream、恢复库前缀、逐事件哈希及终点，原子补回丢失的日志后才执行关闭操作；不会借此恢复正文或重新授权。旧版未含事件的计划在数据库确实丢失日志时拒绝执行，需要从保留的外部 journal 重新生成计划。

关闭操作也产生日志，原恢复计划的尾部因此不再是最新值。维护期间使用 `recovery-console.php --plan=<原计划> --key-file=<密钥> --export-after=<外部游标>` 导出新增页，交由外部 collector 逐页校验并追加到原 journal；保留新的独立终点 pin，再生成新的签名尾部计划。执行 `--reconcile-controls` 时传入新尾部计划，而 `--control-plan-sha256` 仍指向原关闭计划；`--control-inventory-sha256` 必须来自当前独立检查。不得用新尾部计划重新执行关闭步骤代替控制对账。

控制对账要求实际 LDAP 管理员，因此专用维护入口仅在显式 `--reconcile-controls` 时加载 `user_ldap`。所有来源继续 stopped，普通显式 resume 及 WK 新代次对账仍须独立完成。
