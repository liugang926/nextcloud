# Disposable LDAPS and Nextcloud source acceptance receipt

Date: 2026-10-10. Only the task-owned Nextcloud34/PostgreSQL16 and Samba AD containers were used. Their `codex.task` label is `auto-space-prd-20261010`; neither service publishes a host port. Shared Nextcloud/WeKnora deployments and production settings were not changed.

The reusable fixture is `scripts/ops/fixtures/ldaps/`: `Dockerfile`, `provision.py`, `start.sh`, `start-disposable-ad.sh`, and `configure-nc.py`. These preserve the actual tested Samba configuration. The domain is `PRD.EXAMPLE.TEST`, the disposable hostname is `prd-ad`, and LDAPS runs on 636 with a generated two-day CA and a SAN certificate for the owned hostname. No CA private keys or domain secrets are committed. Test passwords in these recipes are synthetic and only apply to the disposable domain.

The configuration helper requires `WEKNORA_PRD_TEST_RUNTIME=1`, verifies both container ownership labels and the absence of published host ports, copies only the public CA certificate into the owned Nextcloud trust store, and sets `turnOffCertCheck=0`, cache TTL 0, timeout 5, canonical AD objectGUID identities, nested groups and the trusted directory ID `prd-samba-ad`. The server remains certificate-verifying; `ldapTLS=0` means no additional StartTLS on an already encrypted `ldaps://` connection.

To reproduce from an already installed disposable Nextcloud runtime, build the fixture image only when local disk capacity permits, then run the guarded launcher. The launcher reuses an existing local image and refuses to overwrite an existing AD domain. It never downloads an image or opens a host port automatically.

```sh
# Optional image build; skip when the tested local image already exists.
docker build -t weknora-auto-space-ad-test:20261010 scripts/ops/fixtures/ldaps
WEKNORA_PRD_TEST_RUNTIME=1 sh scripts/ops/fixtures/ldaps/start-disposable-ad.sh
# Install/enable the bundled user_ldap app and create its s01 configuration once
# in this owned installation, then configure trusted LDAPS:
WEKNORA_PRD_TEST_RUNTIME=1 python3 scripts/ops/fixtures/ldaps/configure-nc.py
```

Create synthetic users `prd-alice`, `prd-bob`, and `prd-sourceowner`, plus security groups `PrdEngineering`, `PrdPlatform`, `PrdSourceRead`, `PrdSourceNested`, and `PrdSourcePrimary` with `samba-tool` inside the labelled AD container. The tested passwords were `SyntheticPrdAlice2026!`, `SyntheticPrdBob2026!`, `SyntheticPrdOwner2026!`, and disposable administrator password `SyntheticPrdAdmin2026!`. `user_ldap` discovers the new users through a real directory search; source user/group relations subsequently use verified GUIDs, not these names.

Runtime PHP scripts explicitly load the installed `user_ldap` app before resolving LDAP owners. A bare CLI script that only loads the custom app does not establish all authentication backends and can incorrectly treat an existing LDAP source owner as unavailable.

```sh
# Setup creates an original under the real LDAP publisher and a real NC group share.
docker exec weknora-auto-space-ad-20261010 samba-tool group addmembers PrdSourceRead prd-alice
docker exec -u www-data -e WEKNORA_PRD_TEST_RUNTIME=1 -e WEKNORA_PRD_LDAP_SOURCE_MODE=setup \
  nextcloud-auto-space-prd-test-20261010 php /var/www/html/custom_apps/integration_weknora/tests/ldap_source_guard_runtime.php

# Nested membership: remove direct membership, then make the nested group a member.
docker exec weknora-auto-space-ad-20261010 samba-tool group removemembers PrdSourceRead prd-alice
docker exec weknora-auto-space-ad-20261010 samba-tool group addmembers PrdSourceNested prd-alice
docker exec weknora-auto-space-ad-20261010 samba-tool group addmembers PrdSourceRead PrdSourceNested
docker exec -u www-data -e WEKNORA_PRD_TEST_RUNTIME=1 -e WEKNORA_PRD_LDAP_SOURCE_MODE=nested \
  nextcloud-auto-space-prd-test-20261010 php /var/www/html/custom_apps/integration_weknora/tests/ldap_source_guard_runtime.php

# Primary group: the runtime changes this fixture's NC share to the primary group itself.
docker exec weknora-auto-space-ad-20261010 samba-tool group removemembers PrdSourceNested prd-alice
docker exec weknora-auto-space-ad-20261010 samba-tool group removemembers PrdSourceRead PrdSourceNested
docker exec weknora-auto-space-ad-20261010 samba-tool group addmembers PrdSourcePrimary prd-alice
docker exec weknora-auto-space-ad-20261010 samba-tool user setprimarygroup prd-alice PrdSourcePrimary
docker exec -u www-data -e WEKNORA_PRD_TEST_RUNTIME=1 -e WEKNORA_PRD_LDAP_SOURCE_MODE=primary \
  nextcloud-auto-space-prd-test-20261010 php /var/www/html/custom_apps/integration_weknora/tests/ldap_source_guard_runtime.php

# Restore the original primary group and verify immediate source/metadata denial.
docker exec weknora-auto-space-ad-20261010 samba-tool user setprimarygroup prd-alice 'Domain Users'
docker exec weknora-auto-space-ad-20261010 samba-tool group removemembers PrdSourcePrimary prd-alice
docker exec -u www-data -e WEKNORA_PRD_TEST_RUNTIME=1 -e WEKNORA_PRD_LDAP_SOURCE_MODE=revoked \
  nextcloud-auto-space-prd-test-20261010 php /var/www/html/custom_apps/integration_weknora/tests/ldap_source_guard_runtime.php
```

| Actual scenario | Result | Evidence |
| --- | --- | --- |
| AD GUID, account state and security-group SID/tokenGroups over trusted LDAPS | Passed | `ldap_membership_runtime.php`; direct/nested/primary, unrelated user and GUID mismatch |
| Disabled account, untrusted certificate, unavailable directory | Passed adapter checks | `ldap_membership_runtime.php` modes; no cached GUID fallback |
| Real NC group share, direct member | Passed four source entrances | `SourceAuthorizationService::authorizeRoot` and `authorize`, `EmployeeFileStatusService`, `FolderSummaryService` |
| Real NC group share, nested member | Passed four source entrances | Same runtime with current directory membership |
| Real NC share to the primary group itself | Passed four source entrances | Actual NC primary-group mount READ and live AD tokenGroups |
| Unrelated LDAP user | Denied across all four | Bob denied source authorization and received neither file metadata nor folder counts |
| Member removed, then actual share deleted | Denied across all four | No stale source authorization or success counts; original bytes and ETag preserved |
| Positive independent source revision with a legacy request specifying 0 | Denied | Latest `ldap_source_guard_runtime.php` passes the real revision and separately rejects the old request |
| Primary group nested under another group with only the ancestor group shared | NC mount lacked READ, access correctly denied | The adapter does not override Nextcloud's effective permission decision |
| Shared mount write/rename/delete | Passed | `shared_alias_events_runtime.php`; owner canonical hints, stable file IDs, content revision, manifest invalidation and compensation queue |
| Removing a received share mount | Original retained; no false deletion tombstone | Same runtime; owner source survives and only reconciliation is emitted |
| Real equal-size in-place write in the same second | Repeated ETag observed; independent revision advanced | `source_content_revision_runtime.php`, first attempt: revision 2 → 3; no ETag/mtime manipulation |
| Failed write after its before-event | Old derived content stays fenced; original metadata remains visible and unverified | Durable `writing` state; no completion inferred |
| Stage/graph replica health | Passed admin diagnostics | `stage_health_runtime.php`; stored 13/stale 13 in the observed run, fixed-code alerts, no user-visible-current claim |

The same-ETag observation uses actual `FileNode.putContent` plus the storage updater to refresh CLI filecache metadata. It does not fabricate ETags, source timestamps or content revisions. It demonstrates the in-place storage path; no claim is made that every WebDAV upload uses the same inode behavior. The independent content revision fence also covers those other write paths without relying on ETag uniqueness.

The source-layer matrix does not replace WeKnora KB-grant, query, Agent/history, graph backend, physical GC, rollback, browser, or full 100 GB I/O acceptance. Those have separate fixtures and receipts. The 10,000-entry summary benchmark is a synthetic filecache metadata test, not a real 100 GB upload/parse test.

A second independent paired-root matrix used `prd-root-alice`, `prd-root-bob`, and `PrdRootRead`/`PrdRootNested`/`PrdRootPrimary`. This kept the original `prd-alice` processing fixture's valid share intact during parallel WeKnora tests. The runtime accepts `WEKNORA_PRD_LDAP_READER_UID`, `WEKNORA_PRD_LDAP_DENIED_UID`, `WEKNORA_PRD_LDAP_SOURCE_GROUP`, `WEKNORA_PRD_LDAP_PRIMARY_GROUP`, and `WEKNORA_PRD_LDAP_FIXTURE_FILE`; set `WEKNORA_PRD_ROOT_AUTH_EXPORT=0` to avoid overwriting the active component fixture. Each direct/nested/primary/removed-member/deleted-share mode also verified the signed API's underlying root authorizer. Both file and root authorization require a current real LDAP identity and current Nextcloud READ; root authorization additionally checks the active pair and a uniform whole-tree audience twice.

The component fixture's scoped one-time token and key are stored only in the owned runtime's `/tmp/weknora-prd-root-auth-fixture.json` and a task-local mode-0600 copy. They are not emitted in receipts or committed. The processing agent used that fixture to test real HTTP components.
