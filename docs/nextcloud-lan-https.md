# Optional Nextcloud LAN HTTPS gateway

The `lan-https` Compose profile serves this development stack at
`https://10.106.105.128:18482` while its existing HTTP listeners remain in
place. Nginx terminates TLS and forwards every path and method, including
WebDAV, to the same Nextcloud container. It binds only the configured private
LAN address. This profile is excluded from ordinary `docker compose up` and
from fresh CI stacks that do not have local certificates.

The default certificate and key are
`../weknora-ldap-local/certs/weknora-lan-signed.crt` and
`../weknora-ldap-local/certs/weknora-lan.key`, relative to this repository.
The certificate covers `10.106.105.128`; its trust root is
`../weknora-ldap-local/certs/weknora-lan-ca.crt` (not `certs/ca.crt`). Install
that root on each LAN test device before browser testing. Keep the private key
on the Docker host. Set `NEXTCLOUD_HTTPS_CERT_FILE` and
`NEXTCLOUD_HTTPS_KEY_FILE` to other absolute paths if the files live elsewhere;
the replacement certificate must include the chosen `NEXTCLOUD_LAN_HOST` in
its SAN. `NEXTCLOUD_HTTPS_PORT` defaults to `18482`.

## Start and configure

First run `scripts/allow-lan-access.sh 10.106.105.128` if LAN access has not
already been enabled. It records `NEXTCLOUD_LAN_HOST` and adds that IP to
`trusted_domains`. Then, from this repository, use the same Compose file list
for all HTTPS gateway operations:

```bash
dc=(docker compose --env-file .env \
  -f compose.yaml -f integration/nextcloud.lan.yaml \
  -f integration/nextcloud.https.yaml --profile lan-https)
"${dc[@]}" config --quiet
"${dc[@]}" up -d --no-deps nextcloud-https
gateway_id="$("${dc[@]}" ps -q nextcloud-https)"
gateway_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$gateway_id")"
printf 'Nextcloud HTTPS proxy IP: %s\n' "$gateway_ip"
```

The gateway has one Docker network. Nextcloud must trust **only this exact
container IP** to use its `X-Forwarded-Proto: https` and forwarded Host;
trusting the whole Docker subnet would also trust other containers. On the
provided stack, `trusted_proxies` is initially empty:

```bash
"${dc[@]}" exec -T -u www-data nextcloud \
  php occ config:system:set trusted_proxies 0 --value="$gateway_ip"
"${dc[@]}" exec -T -u www-data nextcloud \
  php occ config:system:set overwrite.cli.url \
  --value="https://10.106.105.128:18482"
```

If `trusted_proxies` already has entries, append this IP at the next free
numeric index instead of replacing them. If the gateway is recreated and its
IP changes, replace its old entry with the new exact IP before accepting LAN
traffic. The gateway passes the original `Host` including port, resets
caller-supplied forwarding headers, and sends `X-Forwarded-Proto: https`.
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

## Verify

Use the WeKnora LAN CA certificate, not the LDAP CA:

```bash
ca=../weknora-ldap-local/certs/weknora-lan-ca.crt
curl --noproxy '*' --cacert "$ca" -I \
  https://10.106.105.128:18482/
curl --noproxy '*' --cacert "$ca" -I \
  https://10.106.105.128:18482/login
curl --noproxy '*' --cacert "$ca" -I \
  https://10.106.105.128:18482/.well-known/carddav
curl --noproxy '*' --cacert "$ca" -i -X PROPFIND -H 'Depth: 0' \
  https://10.106.105.128:18482/remote.php/dav/files/devadmin/
curl --noproxy '*' -I http://10.106.105.128:18082/login
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
`https://10.106.105.128:18482`, then open the HTTPS Files UI and a newly
indexed citation in a trusted-CA browser. Its scripts, API requests, Files
link, and citation should remain HTTPS with no mixed-content warning.
