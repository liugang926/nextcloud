#!/usr/bin/env bash
# Idempotent application bootstrap. Never resets users, source files or passwords.
set -euo pipefail
umask 077

task_root=${NEXTCLOUD_PRODUCTION_ROOT:-/srv/nextcloud-production}
task_release=${NEXTCLOUD_RELEASE_DIR:-$task_root/current}
task_env=${NEXTCLOUD_IMAGES_ENV:-$task_root/secrets/images.env}
task_data=/data/nextcloud-production
[[ $(id -u) == 0 ]] || { printf 'Run production bootstrap as root\n' >&2; exit 1; }
[[ -f "$task_env" && -d "$task_release/apps/integration_weknora" ]] || { printf 'Missing images.env or release application\n' >&2; exit 1; }
task_compose=(docker compose --env-file "$task_env" -p nextcloud-production -f "$task_release/deploy/production/compose.yaml")
task_occ=("${task_compose[@]}" exec -T -u www-data nextcloud php /var/www/html/occ)
"${task_compose[@]}" config --quiet
"${task_compose[@]}" config --format json | python3 -c '
import json,re,sys,urllib.parse
j=json.load(sys.stdin); env=j["services"]["nextcloud"]["environment"]
domain=env["OVERWRITEHOST"]
assert re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?",domain) and ".." not in domain, "Invalid production domain"
for origin in filter(None,env.get("WEKNORA_EVENT_ALLOWED_ORIGINS", "").split(",")):
    p=urllib.parse.urlsplit(origin)
    assert p.scheme=="https" and p.hostname and p.hostname==p.hostname.lower() and not p.username and not p.password and not p.path and not p.query and not p.fragment, "WeKnora origins must be canonical HTTPS origins"
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?",p.hostname) and ".." not in p.hostname, "Invalid WeKnora hostname"
    import ipaddress
    try: ipaddress.ip_address(p.hostname)
    except ValueError: pass
    else: raise AssertionError("WeKnora origin must use a TLS hostname")
    assert origin=="https://" + p.hostname + ((":"+str(p.port)) if p.port is not None else ""), "Noncanonical WeKnora origin"
for service in j["services"].values():
    env=service.get("environment",{})
    for key in ("WEKNORA_EVENT_DEV_HTTP","WEKNORA_LOCAL_COMPOSE","WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY"):
        assert env.get(key,"0")=="0", "Development bypass is enabled"
print("Production origins and disabled development bypasses verified")
'

# File-backed Compose secrets preserve host ownership/mode. The parent directory
# stays private to root; the selected mounts must be readable after DB drops UID.
install -d -m 0700 "$task_root/secrets" "$task_data/backups"
for task_secret in nextcloud_db_password nextcloud_admin_password; do
    [[ -s "$task_root/secrets/$task_secret" ]] || { printf 'Missing secret file: %s\n' "$task_secret" >&2; exit 1; }
    chmod 0444 "$task_root/secrets/$task_secret"
done
# Query the selected image itself; distribution UIDs differ, notably PostgreSQL
# alpine versus Debian. These short, network-isolated probes mount no user data.
task_image_ids=$("${task_compose[@]}" config --format json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]; print("\n".join(s[n]["image"] for n in ["nextcloud","db","redis","office"]))')
task_nc_image=$(printf '%s\n' "$task_image_ids" | sed -n '1p')
task_db_image=$(printf '%s\n' "$task_image_ids" | sed -n '2p')
task_redis_image=$(printf '%s\n' "$task_image_ids" | sed -n '3p')
task_office_image=$(printf '%s\n' "$task_image_ids" | sed -n '4p')
task_probe=(docker run --rm --pull=never --network=none --cpus=0.25 --memory=64m)
task_nc_owner=$("${task_probe[@]}" --entrypoint sh "$task_nc_image" -c 'printf "%s:%s" "$(id -u www-data)" "$(id -g www-data)"')
task_db_owner=$("${task_probe[@]}" --entrypoint sh "$task_db_image" -c 'printf "%s:%s" "$(id -u postgres)" "$(id -g postgres)"')
task_redis_owner=$("${task_probe[@]}" --entrypoint sh "$task_redis_image" -c 'printf "%s:%s" "$(id -u redis)" "$(id -g redis)"')
# CODE 26.04 has no stat or shell. Read the directory's numeric ownership from
# Docker's archive of a stopped, network-isolated container instead.
task_office_owner=$(python3 - "$task_release/deploy/production/verify-api.py" "$task_office_image" <<'PY'
import runpy,sys
print(runpy.run_path(sys.argv[1])["office_data_owner"](sys.argv[2]))
PY
)
for task_owner in "$task_nc_owner" "$task_db_owner" "$task_redis_owner" "$task_office_owner"; do
    [[ "$task_owner" =~ ^[0-9]+:[0-9]+$ ]] || { printf 'Cannot determine an image data owner\n' >&2; exit 1; }
done
# Chown newly created or empty roots only. Existing application trees are retained.
for task_dir in html data postgres redis office; do
    mkdir -p "$task_data/$task_dir"
    if [[ -z $(find "$task_data/$task_dir" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
        case "$task_dir" in
            html|data) chown "$task_nc_owner" "$task_data/$task_dir" ;;
            postgres) chown "$task_db_owner" "$task_data/$task_dir" ;;
            redis) chown "$task_redis_owner" "$task_data/$task_dir" ;;
            office) chown "$task_office_owner" "$task_data/$task_dir" ;;
        esac
        chmod 0750 "$task_data/$task_dir"
    fi
done
[[ -d /sys/fs/cgroup/nextcloud-production.slice ]] || systemctl start nextcloud-production.slice

# Keep plugin jobs stopped until installation and migrations have completed.
"${task_compose[@]}" stop proxy cron event-worker event-status-worker directory-discovery-worker stage-status-worker
"${task_compose[@]}" up -d db redis nextcloud office
task_installed=false
for ((task_attempt=0; task_attempt<150; task_attempt++)); do
    if "${task_occ[@]}" status --output=json 2>/dev/null | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("installed") is not True)' 2>/dev/null; then
        task_installed=true
        break
    fi
    sleep 2
done
[[ "$task_installed" == true ]] || { printf 'Nextcloud did not install within five minutes\n' >&2; exit 1; }

if "${task_occ[@]}" status --output=json | python3 -c 'import json,sys; sys.exit(not json.load(sys.stdin).get("needsDbUpgrade",False))'; then
    # The release updater is responsible for taking a backup before this step.
    "${task_occ[@]}" upgrade --no-interaction
fi
"${task_occ[@]}" app:enable integration_weknora
"${task_occ[@]}" app:enable user_ldap
"${task_occ[@]}" background:cron
"${task_occ[@]}" config:system:set default_phone_region --value=CN >/dev/null
"${task_occ[@]}" config:system:set allow_local_remote_servers --type=boolean --value=true >/dev/null
"${task_occ[@]}" config:system:set log_type --value=file >/dev/null
"${task_occ[@]}" config:system:set logfile --value=/var/www/data/nextcloud.log >/dev/null
"${task_occ[@]}" config:system:set log_rotate_size --type=integer --value=104857600 >/dev/null

task_office_installed=$("${task_occ[@]}" app:list --output=json | python3 -c 'import json,sys; a=json.load(sys.stdin); print("1" if "richdocuments" in a.get("enabled",{}) or "richdocuments" in a.get("disabled",{}) else "0")')
if [[ "$task_office_installed" == 0 && ${INSTALL_RICHDOCUMENTS:-1} == 1 ]]; then
    task_office_archive="$task_release/deploy/production/richdocuments-11.1.2.tar.gz"
    [[ -f "$task_office_archive" ]] || { printf 'Missing pinned richdocuments 11.1.2 archive\n' >&2; exit 1; }
    python3 - "$task_office_archive" "$task_data/html/custom_apps" <<'PY'
import hashlib,os,pathlib,shutil,sys,tarfile,tempfile,xml.etree.ElementTree as ET
archive=pathlib.Path(sys.argv[1]); destination=pathlib.Path(sys.argv[2])
assert hashlib.sha256(archive.read_bytes()).hexdigest()=="96766a94cf417a716e47ed89c6ff2411c54954caa916afcdddc4d9668dffb557", "Office archive digest mismatch"
destination.mkdir(parents=True,exist_ok=True)
assert not (destination/"richdocuments").exists(), "Refusing to overwrite an existing Office application directory"
with tarfile.open(archive,"r:gz") as package, tempfile.TemporaryDirectory(prefix=".office-install-",dir=destination) as stage:
    members=package.getmembers()
    assert members and sum(m.size for m in members)<1024**3, "Unexpected Office archive size"
    for member in members:
        path=pathlib.PurePosixPath(member.name)
        assert not path.is_absolute() and ".." not in path.parts and path.parts[0]=="richdocuments", "Unsafe Office archive path"
        assert member.isfile() or member.isdir(), "Office archive links and special files are forbidden"
        target=pathlib.Path(stage).joinpath(*path.parts)
        if member.isdir(): target.mkdir(parents=True,exist_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            with package.extractfile(member) as source, target.open("xb") as output: shutil.copyfileobj(source,output)
            target.chmod(0o644)
    app=pathlib.Path(stage)/"richdocuments"
    info=ET.parse(app/"appinfo/info.xml").getroot()
    assert info.findtext("id")=="richdocuments" and info.findtext("version")=="11.1.2", "Unexpected Office application identity/version"
    dependency=info.find("dependencies/nextcloud")
    assert dependency is not None and dependency.get("min-version")=="34" and dependency.get("max-version")=="34", "Office application is not pinned to Nextcloud 34"
    for directory,subdirs,files in os.walk(app):
        os.chown(directory,33,33); os.chmod(directory,0o755)
        for name in files: os.chown(pathlib.Path(directory)/name,33,33)
    app.rename(destination/"richdocuments")
print("Installed the pinned Nextcloud Office 11.1.2 application files")
PY
    task_office_installed=1
fi
if [[ "$task_office_installed" == 1 ]]; then
    "${task_occ[@]}" app:enable richdocuments
    "${task_occ[@]}" app:list --output=json | python3 -c 'import json,sys; v=json.load(sys.stdin)["enabled"]["richdocuments"]; assert v.startswith("11."), "NC34 requires richdocuments 11.x; received " + v; print("Office app version: " + v)'
    task_domain=$("${task_compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["nextcloud"]["environment"]["OVERWRITEHOST"])')
    "${task_occ[@]}" config:app:set richdocuments wopi_url --value=http://office:9980 >/dev/null
    "${task_occ[@]}" config:app:set richdocuments public_wopi_url --value="https://$task_domain" >/dev/null
    "${task_occ[@]}" config:app:set richdocuments wopi_callback_url --value=http://nextcloud >/dev/null
    "${task_occ[@]}" config:app:set richdocuments wopi_allowlist --value=172.30.55.30 >/dev/null
else
    printf '%s\n' 'Office app installation deferred; run bootstrap with INSTALL_RICHDOCUMENTS=1 after the pinned archive is available'
fi

"${task_occ[@]}" maintenance:mode --off
"${task_compose[@]}" restart nextcloud
if [[ ${NEXTCLOUD_SKIP_WORKERS:-0} == 1 ]]; then
    [[ ${NEXTCLOUD_SKIP_PROXY:-0} == 1 ]] || { printf 'NEXTCLOUD_SKIP_WORKERS=1 requires NEXTCLOUD_SKIP_PROXY=1\n' >&2; exit 1; }
    "${task_compose[@]}" up -d --wait --wait-timeout 600 db redis nextcloud office
elif [[ ${NEXTCLOUD_SKIP_PROXY:-0} == 1 ]]; then
    "${task_compose[@]}" up -d --wait --wait-timeout 600 db redis nextcloud office cron event-worker event-status-worker directory-discovery-worker stage-status-worker
else
    "${task_compose[@]}" up -d --wait --wait-timeout 600
fi
"$task_release/deploy/production/healthcheck.sh"
printf '%s\n' 'Bootstrap complete. Configure and verify LDAP and HTTPS WeKnora pairing through the administrator interface.'
