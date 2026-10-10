# Nextcloud directory onboarding

The Files WeKnora sidebar accepts both files and folders. Within a registered publication rule, an explicit source delegate can associate an existing folder or create a new folder and associate it in the same form. The form offers automatic creation of a dedicated KB and authorized existing blank KB candidates. It uses the same session and CSRF protected backend for both choices. Source readability and writability do not grant configuration authority.

Register a rule under Administration → WeKnora publication → 目录自动关联规则. The container must use the verified local-home or Team Folders adapter. A rule specifies allowed existing Tenants with a processing-template KB and scoped credential ID for each, exact service origins, source owner, and explicit delegate UIDs. Candidate Tenants are filtered by the current verified actor before the browser receives them. Legacy single-Tenant rules remain supported. `direct_children` with `auto_enabled` discovers WebDAV-created, copied and moved-in directories. `explicit` permits an intentional association inside the registered scope. Roots and rule containers cannot overlap across owner views.

Allowed targets are unique by `(tenant_id, processing_template_id)`. One Tenant may offer several approved templates, such as optional and required graph processing. The rule's default must name an exact allowed pair. The Files form selects the Tenant and template independently; the immutable choice records both IDs. When several templates are allowed for a selected Tenant, a request without a template must resolve the exact configured default or fail as ambiguous. Candidate responses expose approved template names and graph modes, without model configuration or secrets.

Install a scoped WeKnora onboarding credential through the target's operator configuration, then place scoped secrets in Nextcloud's system configuration as `weknora_onboarding_credentials[credential_id] = ["tenant_id" => "7", "weknora_origin" => "https://weknora.example.com", "token" => "operator-installed-secret"]`. The rule refers only to the credential ID. Legacy single-Tenant rules can use `weknora_onboarding_token`. The target credential is limited to the exact instance, source base URL, existing Tenant, trusted directory, processing-template KB and KB quota. The browser never receives this credential or a pairing token. The existing `WEKNORA_EVENT_ALLOWED_ORIGINS` exact-origin egress allowlist still applies. Production origins require HTTPS; isolated local tests require the existing explicit HTTP development switch.

Example session-protected rule API body:

```json
{
  "expected_revision": 0,
  "rule": {
    "id": "research-documents",
    "owner_uid": "source-owner-uid",
    "container_file_id": 123,
    "tenant_id": "7",
    "processing_template_id": "processing-template-kb-id",
    "weknora_origin": "https://weknora.example.com",
    "base_url": "https://nextcloud.example.com",
    "discovery": "direct_children",
    "delegates": ["explicit-publisher-uid"],
    "automation_uid": "explicit-publisher-uid",
    "auto_enabled": true,
    "targets": [
      {"tenant_id":"7","processing_template_id":"processing-template-kb-id","credential_id":"research-space"},
      {"tenant_id":"9","processing_template_id":"another-template-kb-id","credential_id":"another-allowed-space"}
    ]
  }
}
```

`POST /apps/integration_weknora/api/v1/admin/directory-rules` is restricted to Nextcloud administrators. Editing a rule requires its current revision. Existing source/target boundaries cannot be reassigned through a rule edit; source migration uses the coordinated migration protocol.

Session APIs:

- `GET /api/v1/folders/{id}/status` resolves the actual reader and root audience before returning binding metadata. Descendants inherit their publication-root configuration.
- `GET /api/v1/folders/{id}/candidates` checks source delegation and the target's verified actor authority. The `for_creation=1` parameter selects a registered creation container.
- `POST /api/v1/folders/{id}/associate` accepts `operation_id`, `tenant_id`, `processing_template_id`, `mode`, `name`, and `knowledge_base_id` for existing-KB mode.
- `POST /api/v1/folders/{parentId}/create` accepts the same target choice plus `folder_name`. The intent commits before `newFolder`.
- `POST /api/v1/directory-operations/{operationId}/retry` continues the original operation and target.
- `POST /api/v1/folders/{id}/sync` accepts `sync_enabled` and `expected_revision`. Pausing synchronization preserves current source authorization and ETag checks.
- `POST /api/v1/folders/{id}/target-migration` starts an existing root's target/template change with the same target choice and a durable migration UUID. `GET` and `POST /api/v1/folders/{id}/target-migrations/{migrationUUID}` read its status and retry the same migration. Template changes use this handoff to a new dedicated KB; they do not overwrite the current target IDs.

Native directory events only write a persistent discovery queue. They do not run LDAP or remote requests during an upload or mkdir. Run the included worker as the installation's web user with an explicit path: `NEXTCLOUD_OCC_PATH=/var/www/html/occ sh scripts/ops/directory-discovery-worker.sh`. It executes `php occ integration_weknora:discover-directories` every five seconds; Nextcloud's usual five-minute cron alone does not meet the ten-second event-to-task objective. `DirectoryPublicationJob` performs retry and permission reconciliation; its compensation scan has a persistent cursor so directories beyond the first batch are eventually processed. Discovery and the UI converge on one durable physical-root intent. A persisted explicit choice takes precedence over the background default.

Pairing prepare stores the one-time machine credential in an encrypted handoff outbox in the same database transaction. Response loss and unknown target ACK retain the same operation. The event connection is independent and recoverable from the authenticated target operation. Source files remain intact when target eligibility, reservations or transport fail.

The source grant snapshot retains separate owner, direct user-share, LDAP group-share and supported Team Folder base relations. Stable user/group GUIDs are verified live; names and email addresses are not mappings. Unknown share types, descendant shares and advanced ACL require separately supported permission adapters. An incomplete or unverifiable snapshot is never submitted as an empty audience.

Folder summaries use signed, user- and folder-bound cursors and a stable generation over current visible IDs, ETags, permissions, relevant source epochs, publication-decision revisions and current processing heads. Each page revalidates identity, audience and counted files. Tree, source, permission or status changes invalidate earlier page totals. The sidebar accumulates only matching generations and discards old totals on invalidation. Summary reads use local status metadata and do not make per-file remote requests.

Disposable runtime checks live under `apps/integration_weknora/tests/directory_publication_runtime.php`, `directory_discovery_runtime.php` and `folder_summary_runtime.php`, `directory_targets_runtime.php` and `folder_summary_performance_runtime.php`. They require `WEKNORA_PRD_TEST_RUNTIME=1` and the task-owned Nextcloud/PostgreSQL installation; do not run them against shared or production instances.

Metadata performance on the disposable NC34/PostgreSQL16 runtime (synthetic filecache entries, no 100 GB upload):

| Visible metadata entries | First page seconds | First page SQL queries | Next page seconds | Next page SQL queries | PHP peak |
| --- | --- | --- | --- | --- | --- |
| 205 | 0.057 | 135 | 0.068 | 135 | 34 MiB |
| 1,000 | 0.084 | 147 | 0.082 | 147 | 42 MiB |
| 10,000 | 0.539 | 227 | 0.599 | 227 | 128 MiB |

The 10,000-entry fixture declares about 104.8 GB of source sizes in filecache metadata. It verifies summary query/memory scaling, not physical source upload throughput, parsing, vector/index storage, graph processing, LDAPS pressure, or full 100 GB end-to-end performance. These require separate actual-service acceptance.

The current source version also includes the durable `source_content_revision`. Nextcloud local in-place writes can retain an ETag when inode, timestamp and byte size are unchanged. The before-write event advances the independent revision and closes the write fence; only the successful completion clears it. File/source authorization, manifest snapshots, publication checks, status projections and folder generation checks all compare this revision. Legacy callers without the field can only read revision 0 and fail closed after a source write. A failed write keeps derived knowledge blocked while the employee still sees the original's metadata as unverified.

A bound root's “更换知识库” action uses the persistent `/folders/{id}/target-migration` workflow. It selects only the caller's verified target spaces and blank KBs, preserves the chosen migration operation on transport retry, and pauses the old target until the new source admission and inventory checks complete. It never overwrites the bound target through the ordinary association API.
