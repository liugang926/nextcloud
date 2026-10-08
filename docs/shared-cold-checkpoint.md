# Shared local pair: cold checkpoint only

`scripts/ops/shared-cold-checkpoint.py` captures a matched checkpoint of the
local `nextcloud-weknora-dev` and `weknora-ldap-local` Compose projects. It does
not restore data, start services, change images, or publish a deployment. It
requires the live containers to match the resolved Compose topology, mounts,
ports, environment, and networks. The Nextcloud Git checkout must be clean.

Run a read-only preflight from the **same Nextcloud checkout mounted by the
running containers**:

```sh
python3 scripts/ops/shared-cold-checkpoint.py
```

The explicit capture command needs a new absolute directory on a disk with
room for the eight volume archives, PostgreSQL dumps, and a 2 GiB reserve:

```sh
python3 scripts/ops/shared-cold-checkpoint.py \
  --apply \
  --confirm-pair nextcloud-weknora-dev/weknora-ldap-local \
  --evidence-dir /absolute/private/path/shared-cold-YYYYMMDD-HHMMSS
```

Before stopping anything, the helper checks the local Unix Docker context,
Compose ownership labels, exact running images and mounts, foreign volume or
network users, a clean Nextcloud Git commit, every runtime bind input, and approximate
archive capacity. It creates the destination with mode `0700` and each output
with mode `0600`. The capacity estimate uses read-only Docker helper containers;
it is approximate and may miss filesystem growth during capture.

It then stops both stacks' ingress, application, cron, event, document reader,
LDAP, and other non-database services. Neither database nor Redis may publish a
host port. It requires zero other PostgreSQL client sessions before and after
each `pg_dump -Fc` and cluster globals capture. Next it stops
both PostgreSQL and both Redis services. The cold archives include Nextcloud
PostgreSQL, Redis AOF, and HTML/data/config volumes, and WeKnora PostgreSQL,
Redis AOF, app data, document-reader temporary data, and LDAP data volumes.
The helper also archives the exact bind-mounted Nextcloud app code, Compose and
`.env` files, effective Compose JSON, WeKnora model environment, keys,
certificates, and every other bind-mounted runtime input. It records the
Nextcloud Git commit and every running container image ID.

Bind-mounted inputs are fingerprinted before and after capture. Each archive is
read back and SHA-256 checked before `manifest.json` is written
with `status: COMPLETE`. The `INCOMPLETE` file is removed only after this final
step. **Both stacks remain stopped**, including after success. Any failed or
interrupted attempt must be treated as incomplete, and the stacks should remain
stopped for operator review. The script never tries to restart or clean up an
uncertain state.

The checkpoint contains local passwords, TLS private keys, JWT/AES material,
and document contents. Restrict access to the entire directory. It records
image IDs but does not export image layers or external LLM, object-store, or
remote LDAP state. A complete manifest establishes a matched local copy; it
does not prove restoration or post-restore publication replay. The disposable
restore rehearsal remains documented in
[isolated-dual-service-restore.md](isolated-dual-service-restore.md).

Offline guard and ordering checks:

```sh
python3 scripts/ops/test_shared_cold_checkpoint.py
```
