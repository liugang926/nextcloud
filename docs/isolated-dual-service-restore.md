# Isolated actual dual-service restore drill

`scripts/ops/isolated-dual-service-restore.py` creates a **new** loopback-only
`nc-synldap-*` Compose project using the pinned Nextcloud, PostgreSQL, Redis,
OpenLDAP and local patched WeKnora images. It does not accept an existing
Compose project, volume, database or HTTP origin. The project and every named
volume are checked against the generated fixture marker, the exact generated
Compose-file digest and Compose project/volume labels before its own volumes
can be replaced. A generated project name already present in Docker is
rejected before first startup. It never touches the shared
`nextcloud-weknora-dev` or `weknora-ldap-local` stacks.

```sh
python3 scripts/ops/isolated-dual-service-restore.py \
  --weknora-image weknora-ldap-app:nextcloud-rag
```

The command prints the private evidence directory. To choose one, pass a path
that does not exist with `--evidence-dir`. The directory has mode `0700`; SQL
dumps, volume archives and JSON files have mode `0600`. The archives include
synthetic administrator credentials and application encryption keys. Keep them
private and delete them when the evidence is no longer needed. On success the
owned Compose project and its scratch credentials are removed. On failure it
remains in place for inspection; `failure.json` records its exact scratch path.
Only use `synthetic-ldap-fixture.py destroy --scratch <that path>` after checking
the failure and its ownership marker.

## Sequence and assertions

1. The existing synthetic fixture creates an actual paired source, a dedicated
   knowledge base, an indexed file with a ready chunk and embedding, and two
   OpenLDAP users. Alice's old JWT must read the document and find it in
   search; Bob's negative matrix also runs.
2. Both application containers stop before a coordinated checkpoint. The
   script produces logical dumps of the **real** Nextcloud and WeKnora
   PostgreSQL databases, then stops both database containers and archives
   their complete physical data volumes. Physical volumes preserve the
   installation's database roles and grants, which a single-database dump
   omits. It also archives the actual Nextcloud HTML/config/data and WeKnora
   local original volumes. SHA-256 digests, PostgreSQL volume owners, source
   identity and checkpoint duration are recorded in `checkpoint.json`. The
   fixture's vector index uses PostgreSQL (`RETRIEVE_DRIVER=postgres`), so its
   indexed rows are included. The logical dumps are separately format-checked
   and retained as evidence; the cold volumes are the restoration input.
3. The services resume, the synthetic source file is deleted, and two
   successful complete WeKnora scans must leave a tombstone with zero visible
   knowledge candidates. `replay-journal.json` records this **post-checkpoint**
   deletion fact outside the old backup. The journal is read back from disk
   and checked against the checkpoint before its binding/file IDs drive replay.
4. After a second label check, only this disposable Compose project is
   destroyed and recreated. Both PostgreSQL data volumes and the file volumes
   are restored **before either database starts**; PostgreSQL UID, GID and
   permissions must match the checkpoint. The application containers remain
   stopped. Both databases must start, and the restored database must show the
   old published knowledge, proving this was a stale backup.
5. Only Nextcloud starts. The script stops its binding, replays the exact file
   withdrawal, deletes the stale restored file, and resumes the binding. Before
   replay it verifies the restored instance UUID, exact pair operation and
   source IDs, binding root ID, DAV file ID and source ETag against the
   checkpoint. Its
   signed source authorization must deny that file. WeKnora has not yet
   started, so its stale vector cannot be served during this step.
6. WeKnora starts only after the source denial. The **same old JWT** must still
   pass `/api/v1/auth/me` for Alice, while direct knowledge, chunk/preview and
   search checks deny the withdrawn file before reconciliation.
   Two complete scans must restore the tombstone and zero visible candidates;
   the old JWT remains valid but denied for that file. `result.json` records
   the observed recovery duration and these stage results without document
   text or credentials. Successful cleanup is followed by an exact check that
   the owned project has no remaining containers or volumes.

This is a same-pinned-image local physical-volume recovery safety drill, not a
portable PostgreSQL migration method or an RPO or RTO commitment. The replay
journal is a test driver outside the backup, not the PRD's durable cross-system
publication ledger. The drill does not restore or verify external Qdrant,
Milvus, graph, Wiki, cloud object, email alert or production LDAP backends, and
does not prove physical derived-index cleanup. Any failed replay or assertion
stops before reporting success; the fixture publishes only loopback ports.
