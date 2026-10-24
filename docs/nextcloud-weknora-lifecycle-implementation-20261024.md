# Nextcloud × WeKnora Lifecycle PRD Implementation Plan

Status: implemented on `feat/nextcloud-weknora-lifecycle-20261024`

Target: `integration_weknora` 0.10.0 on Nextcloud 34

## Delivery slices

1. **Administrator boundary**
   - Render only WeKnora address, one-time connection key, discovery,
     workspace visibility and connection status.
   - Remove legacy rule, binding, pairing, event-credential, withdrawal and
     diagnostics forms from the ordinary settings template.
   - Stop preparing legacy operation URLs in `Settings\Admin`.
   - Keep advanced operations in existing backend routes and expose a
     read-only OCC diagnostics entry.

2. **User status and activity**
   - Persist inherited workspace and knowledge-base display names on source
     pairing and backfill existing rows during migration.
   - Add signed, paginated folder and file activity endpoints.
   - Show version mismatch, stage completion, freshness, failure explanation,
     knowledge withdrawal and physical cleanup state in the Files sidebar.
   - Mark state returned by an unavailable refresh as `unverified` while
     retaining the last verified result and timestamp.

3. **Lifecycle fencing**
   - Persist a deletion tombstone by binding, file and exact source revision.
   - Reject delayed status for a deleted source generation.
   - Treat a later completed source revision as a new upload/restore
     generation.
   - Accept authenticated deletion-only status to advance cleanup through
     `pending`, `blocked`, `running`, `partial` and `verified` without
     reopening access.

4. **Credentials, origins and scale**
   - Encrypt workspace-scoped onboarding credentials with Nextcloud `ICrypto`
     and migrate legacy plaintext entries.
   - Replace stale credentials on reconnect and clear the local credential set
     on disconnect.
   - Require exact operator-approved connection and event origins.
   - Validate a Web URL supplied by the connection contract and prefer it over
     the legacy OCC value.
   - Cache the first folder-summary page briefly after revalidating the
     reader, root version, permission mask and all binding publication epochs.

## P0 acceptance mapping

| PRD item | Implementation evidence |
| --- | --- |
| ADM-01..03 | Connection-only template, script boundary test, live DOM smoke |
| ADM-04..05 | One-time key lifecycle test, `ScopedCredentialStore`, legacy migration test |
| ADM-06..07 | Separate visibility/reconnect-impact and disconnect text; existing sources remain independent |
| DIR-01..07 | Name projections, exact visible counts, status labels, last sync/parse/index and activity pages |
| DIR-08 | Tombstone activity with withdrawal and cleanup state |
| FILE-01..08 | Inherited names, current/published ETags, stage times, version mismatch, sanitized failures, activity, ready-only ask link |
| V01..V08 | Existing source revision, write fence, attempt/fence and CAS tests plus tombstone generation test |
| D01..D15 | Existing connector reconciliation plus local deletion fence and deletion-only cleanup acceptance |
| R01..R06 | Existing migration/restore gates; stale status and newer source generation regression tests |

## Verification commands

```sh
npm ci --prefix apps/integration_weknora
npm run build --prefix apps/integration_weknora
node apps/integration_weknora/tests/admin_layout_contract.js
node apps/integration_weknora/tests/admin_space_summary_contract.js
node apps/integration_weknora/tests/multisource_ui_contract.js
python3 apps/integration_weknora/tests/activity_http_smoke.py
docker compose exec -T -u www-data -e WEKNORA_PRD_TEST_RUNTIME=1 nextcloud \
  php /var/www/html/custom_apps/integration_weknora/tests/lifecycle_activity_runtime.php
```

Production rollout uses the repository's existing signed release workflow:
merge to `main`, wait for the exact-commit `local-smoke` and `direct-upgrade`
checks, then let `nextcloud-production-update.timer` verify and activate the
published release on `10.106.1.128`. The previous release directory and
pre-update database/config checkpoints remain the rollback boundary.
