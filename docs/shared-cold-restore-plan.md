# Shared local pair: verified restore plan

`scripts/ops/shared-cold-restore-plan.py` is the read-only companion to
[the matched cold checkpoint](shared-cold-checkpoint.md). It validates a
checkpoint and the stopped local restore target, then prints ordered recovery
steps. It **does not perform restoration or start services**. A successful
exit verifies the stated preconditions only; `restore_verified` and
`apply_supported` remain `false`.

Use the same absolute Nextcloud and WeKnora directories recorded by the
checkpoint. Both projects must already be stopped for the default target
verification. Provide a manifest digest recorded separately at capture time
when available; hashing the manifest beside its own archives establishes
internal consistency, not an independent authenticity proof.

```sh
python3 scripts/ops/shared-cold-restore-plan.py \
  --checkpoint-dir /absolute/private/path/shared-cold-YYYYMMDD-HHMMSS \
  --nextcloud-dir /absolute/path/to/nextcloud \
  --weknora-dir /absolute/path/to/weknora-ldap-local \
  --expected-manifest-sha256 <externally-recorded-64-hex-digest>
```

Omit `--expected-manifest-sha256` only for a source consistency review. The
actual digest is returned in the plan. The future destructive restore must
confirm both this exact digest and the pair
`nextcloud-weknora-dev/weknora-ldap-local` before writing anything.

To inspect only the saved source, without calling Docker:

```sh
python3 scripts/ops/shared-cold-restore-plan.py \
  --checkpoint-dir /absolute/private/path/shared-cold-YYYYMMDD-HHMMSS \
  --nextcloud-dir /absolute/path/to/nextcloud \
  --weknora-dir /absolute/path/to/weknora-ldap-local \
  --offline
```

Offline mode still requires the saved bind roots to exist at their original
absolute locations and the captured Nextcloud Git commit to be available. It
leaves the live stopped-target and image-availability gate open. Neither mode
extracts archives or modifies a file, container, volume, network, image or
service. Default mode uses local Unix Docker `inspect`, `ps`, `volume ls` and
Compose `config` reads; it does not create helper containers. Store captured
plan output privately: the container IDs and config fingerprints identify the
local recovery state, though configuration contents and secrets are not
printed.

## What is verified

The checkpoint directory must be owned by the invoking user with mode `0700`.
The manifest and all sixteen expected artifacts must be ordinary, nonempty,
non-hardlinked files owned by that user with mode `0600`. Symlink components,
missing or extra files, duplicate JSON keys, and `INCOMPLETE` or failure markers
are refused. The manifest must describe a completed matched cold pair with
all fifteen services stopped. Every SHA-256 is checked before and after the
longer verification.

Each tar payload is read without extraction. Absolute or parent-traversing
member paths, duplicate normalized names, special files, sparse or privileged
entries, cyclic or missing hardlinks, and members beneath link/file ancestors
are refused. Link chains cannot leave the archive, including a `..` path that
traverses another symlink. Each runtime input archive root is checked
separately so a link cannot pivot into a different input. All required bind,
Compose, `.env`, model and token inputs must be present; unknown archived
input roots are refused.

All Git-tracked application files must match the captured commit byte for
byte, including executable flags and symlink contents. The captured app
archive may additionally contain `node_modules` and Python `__pycache__/*.pyc`
from the original bind directory. These captured build extras remain protected
by the archive digest and link checks, are counted separately, and require
operator review before any restore. Unknown untracked application code is
refused. Git consistency does not independently authenticate ignored build
extras or database/document contents.

The live target must contain exactly the fifteen stopped Compose services and
eight owned local volumes. It rejects extra services or volumes, non-local
volume drivers/options, foreign network ownership, and any other container
using a target volume or network, including stopped containers. The current
containers must match their current resolved Compose mounts, ports,
environment and image references; their creation-time Compose config-hash
labels must equal the hashes calculated from that current configuration. Only
the documented Docker Desktop stopped representation of an additional
same-container-port address becoming an empty loopback binding is accepted.
Unrelated or nonempty port changes are refused. The checkpoint must include
the original running port bindings and creation-time service hashes;
checkpoints predating those fields are refused by this verifier. The restore
mounts must equal the captured mounts. Current service IDs and images may differ after an upgrade. Both the
captured immutable image IDs and current recovery image IDs must still be
available locally. Captured mutable image tags are never the restore image
identity.

The returned current inventory records the stopped container/image IDs,
resolved configuration SHA-256 values and runtime-input fingerprint. This
inventory is distinct from the checkpoint being restored. It is not a backup:
all current volumes and inputs still need a verified recovery copy before
replacement.

## Recovery order and remaining gates

The plan keeps ingress and ordinary applications closed while performing the
following operator-controlled sequence:

1. Recheck the stopped target and resource ownership immediately before writes.
2. Capture and verify a **new pre-restore recovery copy** of all eight current
   volumes, current inputs/app code, and current image inventory.
3. Stage and replace the captured bind inputs/app code safely.
4. Restore the contents and owners/modes of **all eight cold volumes as one
   matched pair**. Overlay extraction is insufficient. Do not combine logical
   database dumps with the restored cold PostgreSQL volumes.
5. Pin every service to its captured immutable image ID using private resolved
   configuration, disable restart policies, and retain closed ingress.
6. Verify restored bytes; validate databases/Redis behind an isolated fence
   before admitting application traffic.
7. Replay the authoritative external withdrawal, ACL-change and deletion
   record from a recorded conservative cursor at or before both write
   shutdowns through recovery time. Use only fenced
   operator reconciliation access; public ingress, cron and ordinary workers
   remain closed.
8. Prove both sides agree on source pairing/publication versions and that
   revoked sources are denied in search, chat, history and downloads. Record
   acceptance with no replay left pending.
9. Reopen applications/workers deliberately, then ingress last.

A stale matched backup cannot reveal withdrawals that happened later.
**Without an authoritative external replay source, ingress must remain
closed.** Neither this verifier nor the capture manifest provides that source.
The manifest creation timestamp marks capture completion; it does not prove
a replay lower bound or ledger cursor. The plan leaves
`external_replay_lower_bound_proven` as `false`. This companion has no destructive `--apply` mode and cannot attest to actual
restoration, byte recovery, runtime recovery or post-checkpoint replay. The
shared destructive implementation and rehearsal gate remains open. Existing
[disposable restore drills](isolated-dual-service-restore.md) are separate
evidence and do not prove restoration of the shared pair.

Offline tamper, ownership and ordering tests:

```sh
python3 scripts/ops/test_shared_cold_restore_plan.py
python3 scripts/ops/test_shared_cold_checkpoint.py
```
