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

`smoke.sh` checks service authentication, the sample manifest, and content bytes. `smoke-manifest.py` temporarily uploads 206 small test files, checks pagination, detects a change between pages, verifies conditional reads, and removes the test folder. The app's HTTP smoke scripts in `apps/integration_weknora/tests/` cover administrator publication, identity mapping and source authorization, the change journal, root-folder events, runtime binding scope, and outbox retention.

The app registers a **WeKnora publication** section in Nextcloud's administrator settings. It can create bounded folder bindings and manage per-file withdrawal. The read API exposes ordered file-change hints and a fresh per-user source authorization decision, including the live source ETag. AD identity mappings are explicitly attested by a Nextcloud administrator. A daily background job prunes expired change hints after at least 30 days and advances the durable cursor floor; an expired cursor requires a complete reconciliation.

For WeKnora integration tests, start the adjacent `weknora-ldap-local` project, build the Nextcloud connector and authorization patch with `./scripts/build-weknora.sh`, and run `./scripts/use-weknora-integration.sh` after the Nextcloud stack is up. The build script archives the fixed WeKnora commit recorded in the script and applies `integration/weknora.patch` in a disposable directory. It does not consume uncommitted changes in the adjacent WeKnora checkout. The override replaces the local WeKnora app image and keeps its existing database and other services.

The WeKnora connector uses:

- `settings.base_url`: `http://nextcloud` from the WeKnora development container;
- `credentials.token`: `WEKNORA_SERVICE_TOKEN` from this repository's `.env`;
- `resource_ids`: `["dev-published"]`.

The local end-to-end check created a synthetic embedding model, a dedicated knowledge base, and a Nextcloud datasource in the existing WeKnora test stack. A sync imported `department-notes.md`, completed parsing, and marked its resource as Nextcloud-sourced. An unlinked local administrator received HTTP 403 for the imported knowledge and knowledge-base list. This exercises ingestion and a deny case with test data; an authorized AD user, real chat answer, production permission semantics, and failure recovery still need end-to-end validation.

The service API contract is in [docs/openapi.yaml](docs/openapi.yaml). The app stores the SHA-256 digest of the service token in Nextcloud app config and only reads files under the configured binding root. The token is for this local test deployment. HTTPS, scoped signed requests, replay protection, and key rotation remain requirements before a shared environment.

## Inspect and stop

```sh
docker compose ps
docker compose logs --tail=100 nextcloud
docker compose exec -T -u www-data nextcloud php occ status
docker compose stop
```

Do not use `down -v` unless you intend to discard the local Nextcloud database and files. The existing WeKnora database and volumes are separate and are not managed by this Compose project.

## Current boundary

The local implementation now includes a change-hint consumer with periodic full reconciliation and two-scan deletion confirmation. The WeKnora patch applies a live, fail-closed source and ETag guard to the tested search, chat, direct file, history, resource, embed, MCP, and derived-content paths. The local synthetic import and unlinked-user denial are verified. Durable published revisions and atomic version switching, complete event delivery and acknowledgement, paired binding policy, an enterprise AD permission matrix, load/fault testing, and backup recovery remain open. Keep this stack on synthetic local data until the [V1 status and remaining work](docs/v1-status.md) are addressed.
