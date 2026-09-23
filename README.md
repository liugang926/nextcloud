# Nextcloud × WeKnora local development

This repository contains an independent Nextcloud deployment and the `integration_weknora` app. It is a local implementation slice of the [development plan](docs/development-plan.md). Nextcloud 34.0.4, PostgreSQL 16 and Redis 7 run with dedicated volumes. The Nextcloud stack starts on its own; the optional WeKnora override joins its app to this project's Docker network for integration tests.

## Start

From this repository:

```sh
./scripts/dev-up.sh
./scripts/bootstrap.sh
./scripts/smoke.sh
./scripts/smoke-manifest.py
```

`dev-up.sh` creates a private `.env` with random local credentials on its first run. The Nextcloud web UI is at `http://localhost:18082`; the admin username and password are in `.env`. `bootstrap.sh` installs the app, creates `Published/department-notes.md` through WebDAV, and saves a `dev-published` binding. Re-running it refreshes the sample file and binding.

`smoke.sh` checks service authentication, the sample manifest, and content bytes. `smoke-manifest.py` temporarily uploads 206 small test files, checks pagination, detects a change between pages, verifies conditional reads, and removes the test folder.

The app also registers a **WeKnora publication** section in Nextcloud's administrator settings. It can create bounded folder bindings and manage per-file withdrawal. The read API exposes ordered file-change hints and a fresh per-user source authorization decision, with explicit administrator-attested AD identity mappings. Run `python3 apps/integration_weknora/tests/authorization_http_smoke.py --file-id 77` and `python3 apps/integration_weknora/tests/changes_http_smoke.py` against the local test stack for their focused checks. These scripts create temporary test data and clean it up.

For WeKnora integration tests, start the adjacent `weknora-ldap-local` project, build its Nextcloud connector with `scripts/build-weknora.sh`, and run `scripts/use-weknora-integration.sh` after the Nextcloud stack is up.

The WeKnora connector uses:

- `settings.base_url`: `http://nextcloud` from the WeKnora development container;
- `credentials.token`: `WEKNORA_SERVICE_TOKEN` from this repository's `.env`;
- `resource_ids`: `["dev-published"]`.

The service API contract is in [docs/openapi.yaml](docs/openapi.yaml). The app stores the SHA-256 digest of the service token in Nextcloud app config and only reads files under the configured binding root. The token is deliberately restricted to this local test deployment; configure HTTPS and the planned request signing, replay protection and key rotation before any shared environment.

## Inspect and stop

```sh
docker compose ps
docker compose logs --tail=100 nextcloud
docker compose exec -T -u www-data nextcloud php occ status
docker compose stop
```

Do not use `down -v` unless you intend to discard the local Nextcloud database and files. The existing WeKnora database and volumes are separate and are not managed by this Compose project.

## Current boundary

This local slice proves app installation, a bound folder's short-lived paginated manifest and content API, persistent manual withdrawal exclusion, a file-event hint journal, source-side user authorization, and the connector protocol with two-scan deletion confirmation. The journal has no WeKnora consumer or automatic retention yet. WeKnora still lacks safe version publication and full retrieval and chat authorization. Do not use the connector for enterprise documents until those controls are implemented and tested. A full knowledge ingestion and Q&A check also needs a test embedding/chat model configured in the local WeKnora instance. See [the V1 status and authorization gaps](docs/v1-status.md).
