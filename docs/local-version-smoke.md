# Local source-version smoke

This manual probe uses only synthetic Markdown in the local `Published` folder.
It requires the Nextcloud and patched WeKnora Docker stacks, an active
Nextcloud data source bound only to `dev-published`, and a WeKnora test account
that can manually sync that data source. Supply the account through environment
variables; do not put its password in this repository:

```sh
export WEKNORA_TEST_ADMIN_EMAIL='your-local-test-admin@example.test'
export WEKNORA_TEST_ADMIN_PASSWORD='your-local-test-password'
python3 scripts/local-version-smoke.py --data-source-id YOUR_DATA_SOURCE_UUID
```

The script reads the local Nextcloud `.env` without printing its credentials.
It creates a unique `version-smoke-*.md` file through WebDAV, syncs it, overwrites
the same file, and syncs again. Bounded PostgreSQL polling checks that each
version has exactly one completed, enabled candidate with the desired ETag and
that the candidate ID changes. It deletes the source file, runs two complete
syncs for deletion confirmation, and checks for a durable tombstone with no
visible candidates. A `finally` block attempts file removal and reconciliation
if an earlier step fails. Each sync and parse phase has a 180-second default
limit; use `--timeout-seconds` to change it (10–600 seconds).

This is a local database-state probe. It does not exercise an authorized AD
user, generated answer, fault recovery, cross-system atomicity, or physical
garbage collection. It is intentionally separate from CI and from the running
application's package verification.
