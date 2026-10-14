# Nextcloud app package

From the repository root, build the installable app archive:

```sh
python3 scripts/package-nextcloud-app.py
```

The script installs the pinned frontend dependencies in a temporary directory,
builds the Files sidebar, and requires its output to match the checked-in
`js/weknora-sidebar.js`. It then creates
`dist/integration_weknora-<version>.tar.gz` with fixed timestamps, ownership,
permissions, and file ordering. The version comes from `appinfo/info.xml`.
The archive includes only the app's runtime PHP, XML, CSS, and JavaScript files
under `appinfo/`, `lib/`, `templates/`, `css/`, and `js/`. Tests, source files,
Node dependencies, lockfiles, and local configuration are excluded.

Before reporting success, the script opens the archive, checks every packaged
file against the working tree, and extracts it into a temporary directory to
verify the `integration_weknora/appinfo/info.xml` install layout. It prints the
archive path and SHA-256 checksum. To verify reproducibility, run it twice and
compare the printed checksums. You can inspect the manifest with:

```sh
tar -tzf dist/integration_weknora-<version>.tar.gz
```

Install the archive into a **separate Nextcloud installation** with a writable
`custom_apps` directory:

```sh
tar -xzf dist/integration_weknora-<version>.tar.gz -C /path/to/nextcloud/custom_apps
sudo -u www-data php /path/to/nextcloud/occ app:enable integration_weknora
sudo -u www-data php /path/to/nextcloud/occ upgrade
```

Set ownership of the extracted directory for that installation's web user if
needed. The development Compose stack mounts the source app directory read-only
into Nextcloud; unpacking this archive there would conflict with that mount.
For an existing installation, use its normal maintenance and backup procedure
when replacing an older app version.
