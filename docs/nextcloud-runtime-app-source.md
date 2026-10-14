# Pin the running Nextcloud app source

The development Compose file mounts `apps/integration_weknora` by default.
Changing `appinfo/info.xml` in that checkout immediately changes the code seen
by Web, cron and both event workers, even before an explicit app upgrade. A
version increase can therefore leave the running code ahead of the database.

For a shared test environment, set `NEXTCLOUD_APP_SOURCE_DIR` in its private
`.env` to a separate snapshot under `dist/runtime-apps/`. Use the exact source
commit matching the database's installed app version. The setting applies to
all four app processes. Keep the selected snapshot unchanged during development;
new source versions are applied after their package/upgrade and matched backup
gates. Fresh isolated environments can use the default development mount.

The local shared environment now uses the exact app directory from commit
`1c8850c9fbf7fd5ad97698b6dd0c6ffc07567ef8`, version `0.4.37`. Its source archive
SHA-256 is `59e3955e54749da6972aef435fb21a8fdb71a83be418fa871e0d3ac74ad7cc2d`.
The private runtime directory and per-file hashes are retained in `dist` and
excluded from Git. The development source remains `0.4.38`.

This corrected a directly observed mismatch: the database reported `0.4.37`
while the development bind exposed `0.4.38`, causing `needsDbUpgrade=true`.
Web and workers were recreated with the matched snapshot. No migration was
run; maintenance is off, the database requires no upgrade, and CA-verified
authenticated DAV returned 207. PostgreSQL, Redis and the WeKnora runtime were
preserved. [Runtime evidence](evidence/nextcloud-runtime-app-source-2026-10-09.json)
records the actual mounted source and container identities.

The cold-checkpoint helper now resolves the actual app source from Compose and
requires all four processes to agree. Only the default checkout or a dedicated
runtime snapshot under the Nextcloud project is allowed. It fingerprints and
archives that source under the canonical `apps/integration_weknora` artifact
name. Backup destinations and external replay anchors must remain outside the
actual source, including a pinned snapshot. This prevents a checkpoint from
silently combining the installed database with newer checkout code.
