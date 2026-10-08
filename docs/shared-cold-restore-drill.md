# Isolated rehearsal of a matched cold checkpoint

`scripts/ops/shared-cold-restore-drill.py` restores a verified checkpoint into
new random Docker volumes. It accepts no target project, existing volume or
resource-name argument. Its default invocation verifies the source and prints
a read-only plan. An external manifest SHA-256 pin is mandatory.

```sh
python3 scripts/ops/shared-cold-restore-drill.py \
  --checkpoint-dir /absolute/private/checkpoint \
  --nextcloud-dir /absolute/original/nextcloud \
  --weknora-dir /absolute/original/weknora-ldap-local \
  --expected-manifest-sha256 <externally-recorded-64-hex-digest>
```

Add `--apply` to create new resources. `--evidence-dir` must identify a new
absolute directory with a real existing parent. Evidence uses directory mode
0700 and file mode 0600. `--volumes-only` restores and verifies all eight
archives without starting PostgreSQL or Redis; the report marks database
validation as unperformed. Run the full checkpoint after other substantial
Docker builds or capacity tests finish.

The source validator is the existing
[restore-plan verifier](shared-cold-restore-plan.md): all sixteen private
artifacts, externally pinned manifest, exact capture topology, tar member/link
safety, required runtime inputs, and the captured Git app tree are checked.
Every volume tar is then copied into private staging through a non-symlink
file descriptor, authenticated against its saved hash, and checked again.

All volume names carry a fresh 96-bit token. Each destination must be absent
before creation and be an ordinary local volume with exact labels and recorded
creation identity. Extraction uses the captured immutable Nextcloud
PostgreSQL image ID with its entrypoint replaced by `tar`, no network, no
host ports and a read-only container filesystem. Its sole Dockerfile VOLUME
must exactly match the helper mount; unknown image volumes are refused before
container creation, preventing anonymous volumes. Linux-unrepresentable
owners, symlink modes or inconsistent hardlink metadata are refused.

BusyBox tar does not restore symlink owners. A second helper from the same
image applies `chown -h` to each already validated archived symlink, using
quoted paths and never following the link. The complete restored volume is
then rearchived through a read-only mount. Every normalized member must match;
extra or missing files are errors. The comparison includes file SHA-256/size,
UID/GID, modes, raw symlink targets and hardlink equivalence groups. Different
tar choices of the first hardlinked member are allowed only when the same
inode-sharing group and bytes are preserved. Unsupported member types never
pass by omission.

The default apply next starts exactly two PostgreSQL and two Redis processes
using captured immutable image IDs, on one new internal network with no
published ports. They listen only on private Unix sockets. PostgreSQL uses the
archive's data-directory owner, read-only transactions, disabled archival,
external recovery commands, replication connections, WAL senders, preload
workers and autovacuum. Standby/recovery signal archives are refused. Redis
uses its captured data owner and AOF, with TCP disabled. Only fixed schema
queries, version and row/key counts enter the report; credentials, identities,
source names, keys and document bodies do not. Startup can modify the cloned
WAL/AOF state, so archive byte equality is recorded **before** database start.

Before every helper start and cleanup, the script rechecks all exact resource
labels/IDs, image IDs, command, user, capabilities, isolation flags, mounts,
volume creation identity and network peers. It also checks stopped foreign
containers for any use of the new volumes or network. Cleanup uses only
recorded IDs/names. If ownership cannot be proved, it retains closed resources
and their attempted names in private failure evidence; it never prunes Docker
state.

The application-code and runtime-input archives are **source verified only**.
They are not extracted into application bind roots, and no Nextcloud app,
WeKnora app, LDAP, cron, worker or ingress process starts. The shared pair is
untouched. This rehearsal does not implement shared destructive recovery,
restore full application behavior, replay later external withdrawals or
permit reopening ingress.

## Verification

```sh
python3 scripts/ops/test_shared_cold_restore_drill.py
```

The offline suite covers default no-write behavior, manifest/payload and staging
tamper, exact membership, bytes/UID/GID/mode/link changes, unknown member types,
unrepresentable metadata, safe quoting, image-default anonymous volumes,
identity/image/mount/port/capability/peer drift and refusal before cleanup or
start. CI runs these tests without Docker writes.

The opt-in tiny real-Docker test creates a synthetic complete sixteen-artifact
checkpoint and restores all eight 1-byte volume archives with non-root UID/GID,
restricted directory/file modes, symlinks and hardlinks. It uses only the
captured immutable helper image and verifies complete cleanup:

```sh
SHARED_COLD_DRILL_DOCKER_TEST=1 \
SHARED_COLD_DRILL_DOCKER_EVIDENCE=/absolute/new/private/evidence \
python3 scripts/ops/test_shared_cold_restore_drill.py TinyDockerRehearsal -v
```

[Recorded tiny-volume evidence](evidence/shared-cold-restore-drill-small-2026-10-08.json)
proves that limited real volume path. The full 1.5GB shared checkpoint and its
PostgreSQL/Redis runtime checks remain pending; neither tiny data nor the
source verifier substitutes for them.
