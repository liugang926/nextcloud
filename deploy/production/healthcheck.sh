#!/usr/bin/env bash
set -euo pipefail

task_root=${NEXTCLOUD_PRODUCTION_ROOT:-/srv/nextcloud-production}
task_release=${NEXTCLOUD_RELEASE_DIR:-$task_root/current}
task_env=${NEXTCLOUD_IMAGES_ENV:-$task_root/secrets/images.env}
task_compose=(docker compose --env-file "$task_env" -p nextcloud-production -f "$task_release/deploy/production/compose.yaml")
task_occ=("${task_compose[@]}" exec -T -u www-data nextcloud php /var/www/html/occ)

"${task_compose[@]}" config --quiet
"${task_occ[@]}" status --output=json | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("installed") is True, "Nextcloud is not installed"
assert s.get("maintenance") is False, "Nextcloud is in maintenance mode"
assert s.get("needsDbUpgrade") is False, "Nextcloud database upgrade is pending"
print("Nextcloud installed; maintenance off; database current")
'
"${task_occ[@]}" app:list --output=json | python3 -c '
import json,sys
apps=json.load(sys.stdin).get("enabled",{})
assert apps.get("integration_weknora"), "integration_weknora is disabled"
assert apps.get("user_ldap"), "user_ldap is disabled"
print("WeKnora plugin " + apps["integration_weknora"] + "; LDAP enabled")
if "richdocuments" in apps:
    assert apps["richdocuments"].startswith("11."), "Expected NC34-compatible richdocuments 11.x"
    print("Nextcloud Office " + apps["richdocuments"])
else:
    print("Nextcloud Office app is not installed yet")
'
"${task_compose[@]}" ps --all --format json | python3 -c '
import json,sys
raw=sys.stdin.read().strip()
rows=json.loads(raw) if raw.startswith("[") else [json.loads(s) for s in raw.splitlines() if s]
expected={"db","redis","nextcloud","office","proxy","cron","event-worker","event-status-worker","directory-discovery-worker","stage-status-worker"}
if sys.argv[1]=="1": expected.remove("proxy")
if sys.argv[2]=="1": expected-={"cron","event-worker","event-status-worker","directory-discovery-worker","stage-status-worker"}
seen={r["Service"]:r for r in rows}
assert expected <= seen.keys(), "Missing production services: " + str(expected-seen.keys())
for name in sorted(expected):
    r=seen[name]
    assert r.get("State")=="running", name + " is not running"
    assert r.get("Health", "") in ("", "healthy"), name + " is not healthy"
print(str(len(expected)) + " required production services are running")
' "${NEXTCLOUD_SKIP_PROXY:-0}" "${NEXTCLOUD_SKIP_WORKERS:-0}"
task_domain=$("${task_compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["nextcloud"]["environment"]["OVERWRITEHOST"])')
curl --fail --silent --show-error --max-time 30 -H "Host: $task_domain" \
    http://127.0.0.1:19980/hosting/discovery | python3 -c '
import sys,xml.etree.ElementTree as ET
root=ET.parse(sys.stdin).getroot()
actions=root.findall(".//action")
assert root.tag=="wopi-discovery" and actions, "Invalid Office discovery response"
assert all(a.attrib.get("urlsrc", "").startswith("https://" + sys.argv[1] + "/") for a in actions), "Office discovery advertises a wrong origin"
print("Office discovery available at the configured HTTPS origin")
' "$task_domain"
if [[ ${NEXTCLOUD_SKIP_PROXY:-0} != 1 ]]; then
curl --fail --silent --show-error --max-time 30 -H "Host: $task_domain" \
    http://10.106.1.128:18082/status.php | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("installed") is True and s.get("maintenance") is False and s.get("needsDbUpgrade") is False, "Proxy returned an unhealthy Nextcloud status"
print("HTTP backend routes the configured public host to Nextcloud")
'
curl --fail --silent --show-error --max-time 30 -H "Host: $task_domain" \
    http://10.106.1.128:18082/hosting/discovery | python3 -c '
import sys,xml.etree.ElementTree as ET
r=ET.parse(sys.stdin).getroot()
assert r.tag=="wopi-discovery" and r.findall(".//action"), "HTTP backend did not route Office discovery"
print("HTTP backend routes Office discovery")
'
fi
printf '%s\n' 'Production health checks passed'
