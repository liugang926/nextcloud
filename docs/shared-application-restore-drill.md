# Isolated captured application and LDAP restore

`scripts/ops/shared-application-restore-drill.py` extends the complete
[cold-volume rehearsal](shared-cold-restore-drill.md) with actual Nextcloud,
WeKnora and OpenLDAP startup. It creates only new random owned resources and
never accepts an existing target or publishes a host port. An external
manifest SHA-256 pin is mandatory. Without `--apply`, it checks the sixteen
source artifacts and prints a read-only plan.

Evidence cannot be placed inside the checkpoint, any original runtime input,
the WeKnora source/installation tree or the Nextcloud application bind root.
An output inside a checkout must be in Git-ignored `dist`; the parent path
must already exist without symbolic or unnormalized ancestors. These checks
run before `mkdir` and resource creation.

```sh
python3 scripts/ops/shared-application-restore-drill.py \
  --checkpoint-dir /absolute/private/checkpoint \
  --nextcloud-dir /absolute/original/nextcloud \
  --weknora-dir /absolute/original/weknora-ldap-local \
  --expected-manifest-sha256 <externally-recorded-64-hex-digest> \
  --evidence-dir /absolute/new/private/evidence --apply
```

The existing verifier authenticates every source artifact and captured Git
file. All eight cold volumes are restored and compared, including every
member, SHA-256, UID/GID, mode and link, before any database starts. The
application and runtime-input archives are copied through a non-symlink file
descriptor, reauthenticated and extracted into new private bind roots.
Extraction creates regular files and directories first, then verified links;
it never follows archive links. Runtime input directories/files use host modes
0700/0600. PHP application code retains executable bits and is readable within
its dedicated mount. Source archives/configuration are never rewritten.

Applications run their captured immutable image IDs on one new `--internal`
network. Every start, exec, mutation and cleanup checks the nonce, exact IDs,
labels, image, environment fingerprint, command, capability set, resource
limits, all mounts, volume creation identity and network peers. Foreign
containers, including stopped ones, cannot use the owned volumes/network.
Anonymous image volumes are refused. Application root filesystems and
Nextcloud HTML, WeKnora originals, restored docreader temporary files and
original LDAP data/configuration are read-only. Nextcloud requires a writable
data root even for console status. A tenth owned volume supplies that root;
all original flat files are copied with exact SHA-256/UID/GID/mode and every
original file and subdirectory is then covered by a read-only subpath mount
from the original restored HTML volume. The active originals remain read-only.
The backing copies must still match after startup, and its only additional
entries may be empty Docker mount-point directories; the private inventory
records all of them. Unexpected new files or changed original bytes/metadata
refuse acceptance. The actual Engine file-subpath path was exercised with
non-root UID/GID and restricted modes before the full rehearsal.

Apache and application transient state use separate tmpfs. OpenLDAP requires a writable MDB directory even for
a bind-only rehearsal. A ninth owned disposable execution volume is therefore
restored from the same verified LDAP archive and fully compared before startup.
The original `slapd.d` is submounted read-only; PID files use tmpfs. OpenLDAP
starts directly without initialization scripts. After startup and the bind,
every execution-volume member, database/configuration byte, UID/GID, mode and
link must still match; only `data/lock.mdb` mutex bytes/size may differ. The
original LDAP volume remains read-only. The captured slapd file capability
requires `NET_BIND_SERVICE` in its bounding set. Each application has
at most 1 CPU/1 GiB; PostgreSQL and Redis have
1 CPU/512 MiB.

A separate runtime overlay maps database/cache addresses to the new resources,
sets Nextcloud maintenance and its documented `config_is_read_only` mode,
disables WeKnora automatic migration and
sync recovery, removes model credentials and HTTP proxies, and disables Docker
sandbox access. PostgreSQL defaults to read-only transactions. Redis's default
ACL permits reads and denies writes/EVAL, checked before and after application
startup. This prevents queued work from claiming tasks. The captured WeKnora
binary still registers its built-in worker/scheduler goroutines; the evidence
does **not** claim they were absent. Its file cleanup paths are also blocked by
the read-only original mounts. Ordinary Nextcloud cron/event workers,
docreader, frontend and public ingress containers are never created.

Acceptance requires installed Nextcloud with maintenance mode and compatible
schema, actual integration version read from its restored database with the
console running as the restored file owner, internal Nextcloud HTTP status, WeKnora `/health` and its
read-only auth configuration entry, and an actual bind against the restored
LDAP database. Fixed metadata inventories of integration identity/bindings,
publication state, WeKnora data sources and source versions must remain equal
before and after application startup. Only hashes/counts/versions enter the
report. Document read, search, chat, synchronization and model request entries
are not invoked. JWT/AES secrets are restored intact, but read-only entry
checks do not prove a successful application user login or AES decryption of
every saved connector.

Private bounded container logs are preserved before exact cleanup, on success
and failure. Daemon startup/exec failures and rejected readiness replies also
have private logs, including failures before a process can write Docker logs.
Removed owned endpoints may briefly remain in Engine network inspection;
cleanup waits only for recorded removed IDs that are absent from every live
and stopped container listing. Unknown or live peers refuse cleanup. Failed ownership proof retains the closed resources and records
their exact inventory; it never prunes Docker state. A failure is not reported
as successful application restoration.

This is an isolated fenced runtime rehearsal. It does not replace the shared
pair or implement shared destructive recovery. No authoritative external
withdrawal/ACL/deletion journal is supplied by the checkpoint. Therefore
`external_replay_verified`, `ingress_reopen_permitted` and
`full_recovery_acceptance` remain false even after runtime acceptance. Real AD
and production restoration remain separate acceptance gates.

Offline safety contracts:

```sh
python3 scripts/ops/test_shared_application_restore_drill.py
python3 scripts/ops/test_shared_cold_restore_drill.py
python3 scripts/ops/test_shared_cold_restore_plan.py
```

## Actual acceptance on 2026-10-08

[Public acceptance evidence](evidence/shared-application-restore-drill-full-2026-10-08.json)
records the complete fresh try7 fixture from runtime code
`84251ecb881162c7a1ab5f63870f0a3ec346f8bf`. The external manifest pin was
`3ad12fbd4edbefb33b3986ab375c0d85deb6bd7a4518d74e16f79f068f9c7bae`.
All sixteen source hashes and the captured Git tree were reverified after the
successful run. The eight original archives compared 39,508 members and
1,525,088,462 payload bytes before database start.

Nextcloud 34.0.4 reported installed, maintenance on and no database upgrade;
its console read integration version 0.4.37 from the restored database, and
its internal HTTP maintenance status passed. WeKnora `/health` and read-only
auth configuration passed against PostgreSQL schema 131, clean. The actual
restored LDAP administrator bind passed and all LDAP database/configuration
bytes and metadata remained equal, excluding only LMDB mutex bytes/size.
Both metadata identity/binding/source-version inventories stayed unchanged.

Nextcloud's six copied root files retained exact bytes/UID/GID/mode/link
metadata after startup. All three original subdirectories and all original
root files were mounted read-only. Its backing cache contained three empty
Docker mount directories and no new temporary files at validation. The
original LDAP volume stayed read-only while its execution copy supplied the
required writable MDB directory. All 30 containers (23 helpers and seven
runtimes), ten volumes and the internal network were removed; the exact nonce
inventory was empty. Captured image IDs and all overlay boundaries are in the
public report. Private configuration, credentials and log bodies are omitted.

Six preceding incomplete attempts and their available logs remain private.
They established the required file capability/PID cache, Engine bind/subpath
representations, console file owner, read-only config option and writable data
root. The WeKnora process logged expected blocked EVAL/write attempts while
the read-only entry checks passed; ordinary worker functionality was not
accepted. External replay and reopening remain prohibited.

An independent read-only review found an evidence-path source-write gap after
the successful run. The output guard and its offline refusal test close that
gap before any write; the actual ignored `dist` evidence path was valid and
the runtime result is unaffected. Current offline gates pass 19 application,
17 cold-drill, 32 plan and 14 checkpoint tests; one optional Docker test is
skipped by default.
