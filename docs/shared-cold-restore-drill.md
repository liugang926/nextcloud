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
UID/GID, modes, raw symlink targets and hardlink equivalence groups.
The permitted sticky permission bit is included in the comparison; setuid and
setgid entries continue to be refused before restoration. Different
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

Before every resource creation, helper/database start and cleanup, the script rechecks all exact resource
labels/IDs, image IDs, command, user, capabilities, isolation flags, mounts,
volume creation identity and network peers. It also checks stopped foreign
containers for any use of the new volumes or network. Starts require the
recorded exact ID and helper/database purpose, rejecting arbitrary target IDs
before sending a Docker start. Cleanup uses only
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
proves that limited real volume path. The subsequent
[full-checkpoint evidence](evidence/shared-cold-restore-drill-full-2026-10-08.json)
records a separate actual restoration of the finalized shared checkpoint into
new isolated volumes, including fenced PostgreSQL/Redis startup. It does not
satisfy the application or external-replay gates.

## Full checkpoint rehearsal on 2026-10-08

Frozen code `d5d0207` includes the independent sticky-bit and ownership/start
reviews. Its 17 offline drill tests and 32 restore-plan tests passed. The real
checkpoint's external manifest pin was
`3ad12fbd4edbefb33b3986ab375c0d85deb6bd7a4518d74e16f79f068f9c7bae`.
All sixteen source artifacts, captured Git app code and required runtime inputs
passed the source verifier. All source hashes were checked again after the
completed drill.

All eight cold-volume archives were restored into new random owned volumes.
The complete inventory compared 39,508 members and 1,525,088,462 payload bytes,
including ownership, permissions/sticky bit, symlinks and hardlink groups.
The proof records each original archive SHA-256 and matching restored-inventory
SHA-256. Byte equality was established before database startup.

| Closed restored service | Observed version and state |
| --- | --- |
| Nextcloud PostgreSQL | 16.13; app0.4.37; users1, files3,268, publication states5, decisions121 |
| WeKnora PostgreSQL | 17.9; migration131, clean; tenants3, KBs3, knowledges29, chunks263 |
| Nextcloud Redis | 7.4.8; db0 keys66 |
| WeKnora Redis | 7.0.15; db0 keys22 |

The run used the captured immutable image IDs, 17 helpers on network-none,
four PostgreSQL/Redis containers listening on Unix sockets on one internal
network, eight new volumes and no host ports. Exact ownership checks preceded
starts and cleanup. All 21 containers, eight volumes and the internal network
were removed; the owned-resource inventory was empty afterwards. The shared
running projects were untouched.

The app-code archive's 106 Git files and 1,551 allowed captured build-extra
entries were source checked. App code and runtime-input archives were not
extracted into application bind roots. No application, LDAP, cron, worker or
ingress process started. This evidence proves the physical cold-volume and
closed database portion of an isolated rehearsal. Actual application restore,
shared destructive apply, external withdrawal replay and revocation acceptance
before reopening remain open.

The subsequent [captured application and LDAP extension](shared-application-restore-drill.md)
now records actual fenced Nextcloud/WeKnora/OpenLDAP startup from the same
externally pinned checkpoint, including private code/input extraction, exact
original-data protection and disposable startup caches. Its successful
read-only runtime acceptance leaves shared destructive apply, authoritative
external replay and reopening unperformed.
