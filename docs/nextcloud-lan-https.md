# Optional Nextcloud LAN HTTPS gateway

The `lan-https` Compose profile serves this development stack at
`https://10.106.105.121:18482` while its existing HTTP listeners remain in
place. Nginx terminates TLS and forwards every path and method, including
WebDAV, to the same Nextcloud container. It binds only the configured private
LAN address. This profile is excluded from ordinary `docker compose up` and
from fresh CI stacks that do not have local certificates.

The default certificate and key are
`../weknora-ldap-local/certs/weknora-lan-signed.crt` and
`../weknora-ldap-local/certs/weknora-lan.key`, relative to this repository.
The certificate covers `10.106.105.121`; its trust root is
`../weknora-ldap-local/certs/weknora-lan-ca.crt` (not `certs/ca.crt`). Install
that root on each LAN test device before browser testing. Keep the private key
on the Docker host. Set `NEXTCLOUD_HTTPS_CERT_FILE` and
`NEXTCLOUD_HTTPS_KEY_FILE` to other absolute paths if the files live elsewhere;
the replacement certificate must include the chosen `NEXTCLOUD_LAN_HOST` in
its SAN. `NEXTCLOUD_HTTPS_PORT` defaults to `18482`. Pass `--ca-file` to the
helper if the replacement certificate has a different trust root.

This address is the host's current private DHCP lease, verified on
2026-10-08. Substitute the actual host address after a lease change.

## Start and configure

First run `scripts/allow-lan-access.sh 10.106.105.121` if LAN access has not
already been enabled. It records `NEXTCLOUD_LAN_HOST` and adds that IP to
`trusted_domains`. Then run the operator helper from this repository:

```bash
python3 scripts/enable-lan-https.py --check-only
python3 scripts/enable-lan-https.py
```

The first command validates the private bind address, Compose configuration,
certificate SAN, trust chain, and matching key without contacting Docker. The
second starts only `nextcloud-https`, reads its single Docker network IP,
preserves other `trusted_proxies` entries, and writes the gateway's **exact
container IP**. It also sets `overwrite.cli.url` to the HTTPS origin and
checks CA-verified HTTPS login HTML, spoofed forwarding headers, and the
parallel HTTP login HTML. It records its managed IP under ignored
`dist/nextcloud-https-gateway.json`, so a repeat run after container
recreation removes the stale IP while preserving unrelated proxies. Keep this
state file with the development stack. Trusting the whole Docker subnet would
also trust other containers. The helper reads the three required Nextcloud
settings with `config:system:get`; `config:list` redacts `trusted_proxies`.

The gateway passes the original `Host` including port, resets caller-supplied
forwarding headers, and sends `X-Forwarded-Proto: https`. The Nextcloud Apache
image enables `mod_remoteip` and may trust the Docker subnet for `X-Real-IP`.
If Nginx forwards the client address in that header, Apache rewrites PHP's
`REMOTE_ADDR` to the client IP and Nextcloud no longer recognizes the gateway's
exact `trusted_proxies` entry. The gateway explicitly strips `X-Real-IP`; it
keeps the connection's gateway IP as `REMOTE_ADDR` and resets
`X-Forwarded-For` to the actual client address. Do not add a broad Docker
subnet to `trusted_proxies` to work around this.

Do not set a global `overwriteprotocol` or `overwritehost`: they would also
change URLs for the existing direct HTTP listener. Nextcloud's
`overwrite.cli.url` **does** need the HTTPS browser origin because the
integration app uses it for human file citations. Its machine source URL and
WeKnora connection settings are separate. Previously indexed citations keep
their old URL until those files are reindexed. Nextcloud's Files-to-WeKnora
link also needs `weknora_web_url` to be an HTTPS LAN origin.

For a different LAN address or HTTPS port, substitute it in the commands and
ensure it remains in `trusted_domains`. The gateway does not redirect the old
HTTP endpoint or emit HSTS; HSTS would make a browser try HTTPS on port
`18082`, where only HTTP is served.

## When the host's LAN address changes

Start Docker Desktop and confirm the new private IPv4 address is assigned to
the host. From this checkout, first run the read-only preflight, then apply
the coordinated change:

```bash
python3 scripts/rotate-lan-ip.py --old-ip OLD_LAN_IP --new-ip NEW_LAN_IP
python3 scripts/rotate-lan-ip.py --old-ip OLD_LAN_IP --new-ip NEW_LAN_IP --apply
```

The helper requires the saved old IP to match both stacks, checks the local
Docker daemon, exact Compose overlays and running WeKnora image/configuration,
and verifies the existing local CA and leaf certificate keys. It signs a new
leaf certificate with the existing test CA, backs up the affected files and
Nextcloud settings with mode `0600` under ignored
`dist/lan-ip-rotation-backups/`, then updates both gateways and browser URLs.
It recreates the WeKnora frontend after the app so its Nginx upstream resolves
the new app container. No CA or server private key is replaced. If a step
fails after the backup, the helper reports its directory; use its manifest
to restore the files and Nextcloud settings, then recreate the affected
containers. It does not attempt an automatic runtime rollback. The preflight
deliberately refuses a rotation already completed by hand.

## Verify

Use the WeKnora LAN CA certificate, not the LDAP CA:

```bash
ca=../weknora-ldap-local/certs/weknora-lan-ca.crt
curl --noproxy '*' --cacert "$ca" -I \
  https://10.106.105.121:18482/
curl --noproxy '*' --cacert "$ca" -I \
  https://10.106.105.121:18482/login
curl --noproxy '*' --cacert "$ca" -I \
  https://10.106.105.121:18482/.well-known/carddav
curl --noproxy '*' --cacert "$ca" -i -X PROPFIND -H 'Depth: 0' \
  https://10.106.105.121:18482/remote.php/dav/files/devadmin/
curl --noproxy '*' -I http://10.106.105.121:18082/login
dc=(docker compose --env-file .env \
  -f compose.yaml -f integration/nextcloud.lan.yaml \
  -f integration/nextcloud.https.yaml --profile lan-https)
"${dc[@]}" exec -T -u www-data nextcloud \
  php occ config:system:get trusted_proxies --output=json
"${dc[@]}" exec -T -u www-data nextcloud \
  php occ config:system:get overwrite.cli.url
"${dc[@]}" exec -T -u www-data nextcloud \
  php occ config:app:get integration_weknora weknora_web_url
```

Expect HTTPS `/login` to return 200 or an HTTPS redirect, CardDAV discovery to
redirect with `Location: /remote.php/dav/`, and anonymous DAV to return 401.
With a private test-account netrc file, repeat the DAV `PROPFIND`
using `curl --netrc-file <file>` and expect 207. The old HTTP `/login` must
still return 200. Check any `Location` header stays on
`https://10.106.105.121:18482`. Check the HTTPS `/login` HTML contains only
`https://10.106.105.121:18482` for its own canonical and icon URLs, while
the HTTP `/login` HTML still uses `http://10.106.105.121:18082`. A request
with spoofed `X-Real-IP` and `X-Forwarded-Proto` headers must still produce
HTTPS origin URLs at the gateway. Then open the HTTPS Files UI and a newly
indexed citation in a trusted-CA browser. Its scripts, API requests, Files
link, and citation should remain HTTPS with no mixed-content warning.
