# Nextcloud × WeKnora local development

This repository contains an independent Nextcloud deployment, the `integration_weknora` app, and a fixed-baseline patch for the adjacent WeKnora service. It implements part of the [development plan](docs/development-plan.md) in a local development environment; it is not a V1 enterprise release. Nextcloud 34.0.4, PostgreSQL 16 and Redis 7 run with dedicated volumes. The Nextcloud stack starts on its own; the optional WeKnora override joins its app to this project's Docker network for integration tests.

## Start

From this repository:

```sh
./scripts/dev-up.sh
./scripts/bootstrap.sh
./scripts/smoke.sh
./scripts/smoke-manifest.py
```

`dev-up.sh` creates a private `.env` with random local credentials on its first run. The Nextcloud web UI is at `http://localhost:18082`; the admin username and password are in `.env`. `bootstrap.sh` installs the app, creates `Published/department-notes.md` through WebDAV, and saves a `dev-published` binding. Re-running it refreshes the sample file and binding.

`smoke.sh` checks signed service authentication, the sample manifest, and content bytes. `smoke-manifest.py` temporarily uploads 206 small test files, checks pagination, detects a change between pages, verifies conditional reads, and removes the test folder. The app's HTTP smoke scripts in `apps/integration_weknora/tests/` cover administrator publication, identity mapping and source authorization, the change journal, root-folder events, runtime binding scope, outbox retention, replay rejection, key rotation, employee status and administrator diagnostics.

The app registers a **WeKnora publication** section in Nextcloud's administrator settings. It can create bounded folder bindings, stop and resume an entire binding's publication, manage per-file withdrawal, and show source-side diagnostics. Stop blocks new source authorization and machine manifest/content reads while retaining the original files, binding identity, credentials, and individual withdrawals. The Files sidebar shows a readable file's source scope, withdrawal or stopped state; knowledge readiness remains unverified until a trusted WeKnora status feed is added. The read API exposes ordered file-change hints and a fresh per-user source authorization decision, including the live source ETag. AD identity mappings are explicitly attested by a Nextcloud administrator. A daily background job prunes expired change hints after at least 30 days and advances the durable cursor floor; an expired cursor requires a complete reconciliation.

For WeKnora integration tests, start the adjacent `weknora-ldap-local` project, build the Nextcloud connector and authorization patch with `./scripts/build-weknora.sh`, and run `./scripts/use-weknora-integration.sh` after the Nextcloud stack is up. The build script archives the fixed WeKnora commit recorded in the script and applies `integration/weknora.patch` in a disposable directory. It does not consume uncommitted changes in the adjacent WeKnora checkout. The Compose overlays replace only the local WeKnora app image, keep its existing database and other services, and explicitly permit the fixed `http://nextcloud` endpoint for this isolated Docker test. Other deployments require HTTPS and a server-approved origin in `WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS`.

The WeKnora connector uses:

- `settings.base_url`: `http://nextcloud` from the WeKnora development container;
- `credentials.token`: `WEKNORA_SERVICE_TOKEN` from this repository's `.env`;
- `credentials.key_id`: `default`, issued for the `dev-published` binding by `bootstrap.sh`;
- `resource_ids`: `["dev-published"]`.

The local cross-system check created a synthetic embedding model, a dedicated knowledge base, and a Nextcloud data source in the existing WeKnora test stack. A sync imported `department-notes.md`, completed parsing, and marked its resource as Nextcloud-sourced. A separate synthetic file was uploaded, overwritten with different bytes and deleted; the candidate changed, only one version remained visible after each publish, and two deletion scans produced a tombstone with zero visible candidates. An unlinked local administrator received HTTP 403 for the imported knowledge and knowledge-base list. The [manual version probe](docs/local-version-smoke.md) reproduces the update and delete checks. An authorized AD user, real chat answer, production permission semantics, and failure recovery still need end-to-end validation.

The service API contract is in [docs/openapi.yaml](docs/openapi.yaml), the [machine signing specification](docs/machine-request-signing.md), the [event receiver and pairing contract](docs/nextcloud-event-receiver.md), the [source-pairing contract](docs/nextcloud-source-pairing.md), and the [event sender guide](docs/nextcloud-event-sender.md). [Local operations](scripts/ops/README.md) show how to pair a new empty knowledge base. Machine requests use a Bearer token plus canonical HMAC headers, a timestamp and a database-backed nonce; local tests cover replay, tampering, rotation and cross-binding denial. Each server-stored token hash is scoped to one binding; ordinary keys can be issued or revoked by an administrator, while a source-pairing key follows its pairing lifecycle. An independent user proof for source authorization and production TLS remain before a shared environment. The [app package guide](docs/app-package.md) describes the reproducible runtime archive, and the [isolated restore drill](scripts/ops/README.md) covers synthetic backup verification.

## Inspect and stop

```sh
docker compose ps
docker compose logs --tail=100 nextcloud
docker compose exec -T -u www-data nextcloud php occ status
docker compose stop
```

Do not use `down -v` unless you intend to discard the local Nextcloud database and files. The existing WeKnora database and volumes are separate and are not managed by this Compose project.

## Current boundary

The local implementation includes change hints with periodic full reconciliation and two-scan deletion confirmation. The WeKnora patch applies a live, fail-closed source and ETag guard to tested search, chat, direct file, history, resource, embed, MCP, and derived-content paths. A minimal durable version row stages an invisible candidate and publishes it after parsing, while keeping older rows for later recovery policy. The patch has a signed durable event inbox and a leased dispatcher that queues full source reconciliation; Nextcloud signs and retries scoped delivery. Nextcloud 0.4.14 and the WeKnora patch also provide a durable, headless two-phase source-pairing workflow for a new empty dedicated knowledge base. [Local operations](scripts/ops/README.md) include operator CLIs for initial pairing, active key rotation, retry, and pending-rotation abort. The earlier [cross-system probe](scripts/ops/README.md) verified a matching durable receipt watermark and queue acceptance on a legacy synthetic source; the current patch requires an active source pair before repeating it. Receipt and dispatch are distinct from application: the applied checkpoint remains zero. Binding-wide Stop publication blocks new source reads but does not prove WeKnora index cleanup. Complete publication acknowledgement, pending initial-pair abort, full revision history and garbage collection, an enterprise AD permission matrix, load/fault testing, and actual coordinated backup recovery remain open. The connector now streams one downloaded file at a time after validating the complete metadata manifest, but still buffers the manifest and one file body, has no mid-scan resume, and has not passed the 10,000-file / 100-GB pilot. The 30-second dispatcher poll and standard Nextcloud cron do not meet the PRD's 10-second event-to-task target. Keep this stack on synthetic local data until the [V1 status and remaining work](docs/v1-status.md) are addressed.
