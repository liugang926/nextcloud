#!/usr/bin/env python3
"""Rehearse a verified cold checkpoint in new, closed Docker resources.

Default: verify the sixteen source artifacts and print a plan without writes.
--apply restores all eight volume archives to random new volumes. Only the
captured PostgreSQL/Redis image IDs may start, with no host ports and Unix
sockets only on an internal network. Apps, LDAP, workers and ingress never run.
This does not apply recovery to shared resources or replay external revocations.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import stat
import sys
import time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cold_restore_plan", HERE / "shared-cold-restore-plan.py")
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)
capture = plan.capture
LABEL = "io.github.liugang926.cold-drill"
ROLE_LABEL = LABEL + ".role"
DATA = "/var/lib/postgresql/data"


class DrillError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise DrillError(message)


def inventory_identity(inventory):
    """Preserve every entry and hardlink equivalence, independent of tar order."""
    entries = inventory["entries"]
    roots, groups = {}, {}
    for name, entry in entries.items():
        if entry["kind"] not in {"file", "hardlink"}:
            continue
        dest, seen = name, set()
        while entries[dest]["kind"] == "hardlink":
            require(dest not in seen, "hardlink cycle in verified inventory")
            seen.add(dest)
            dest = entries[dest]["target"]
        require(entries[dest]["kind"] == "file", "hardlink target is not a file")
        roots[name] = dest
        groups.setdefault(dest, []).append(name)
    result = {}
    for name, entry in entries.items():
        value = {key: entry[key] for key in ("kind", "mode", "uid", "gid")}
        if name in roots:
            original = entries[roots[name]]
            value.update(kind="file", size=original["size"], sha256=original["sha256"],
                         hardlink_group=sorted(groups[roots[name]]))
        elif entry["kind"] == "symlink":
            value.update(linkname=entry["linkname"], target=entry["target"])
        else:
            require(entry["kind"] == "directory", "unsupported inventory member type")
        result[name] = value
    return result


def require_representable(inventory):
    entries = inventory["entries"]
    normalized = inventory_identity(inventory)
    for name, entry in entries.items():
        require(type(entry["uid"]) is int and type(entry["gid"]) is int and
                0 <= entry["uid"] < 2**32 - 1 and 0 <= entry["gid"] < 2**32 - 1,
                "archive ownership cannot be represented on Linux")
        if entry["kind"] == "symlink":
            require(entry["mode"] == 0o777, "archive symlink mode cannot be represented on Linux")
        if entry["kind"] == "hardlink":
            target = normalized[entry["target"]]
            require(all(normalized[name][key] == target[key] for key in ("uid", "gid", "mode")),
                    "archive hardlink members have inconsistent ownership or modes")


def symlink_owner_script(inventory):
    # BusyBox tar does not restore symlink owners. lchown each archived link
    # using the immutable helper's chown -h; never follow the link itself.
    lines = ["set -eu", "cd " + shlex.quote(DATA)]
    for name, entry in sorted(inventory["entries"].items()):
        if entry["kind"] == "symlink":
            lines.append("chown -h " + str(entry["uid"]) + ":" + str(entry["gid"]) +
                         " -- " + shlex.quote("./" + name))
    return "\n".join(lines) + "\n"


def compare_inventory(expected, actual):
    left, right = inventory_identity(expected), inventory_identity(actual)
    require(set(left) == set(right), "restored volume has missing or extra entries")
    require(left == right, "restored bytes, ownership, mode or link topology differs")
    return hashlib.sha256(json.dumps(right, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def private_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def staged_archive(source, target, expected_hash):
    """Copy from a checked regular private file; authenticate the copied bytes."""
    plan.plain_owned(source, 0o600)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as incoming:
        before = os.fstat(incoming.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600,
                "archive descriptor is not private and regular")
        with target.open("xb") as output:
            os.chmod(target, 0o600)
            shutil.copyfileobj(incoming, output, 1024 * 1024)
        after = os.fstat(incoming.fileno())
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                "archive changed during staging")
    plan.plain_owned(target, 0o600)
    require(capture.sha256(target) == expected_hash, "staged archive differs from the verified pin")


class OwnedDrill:
    """Record immutable IDs/specifications; fail closed before every start/remove."""
    def __init__(self, cwd, token):
        require(len(token) == 24 and all(c in "0123456789abcdef" for c in token), "invalid drill token")
        self.cwd, self.token = cwd, token
        self.prefix = "nc-cold-drill-" + token
        self.volumes, self.containers, self.network = {}, {}, None
        self.attempted_resources = []

    def run(self, args, **kwargs):
        return capture.run(args, cwd=self.cwd, **kwargs)

    def inspect(self, kind, target):
        return capture.inspect(kind, target, self.cwd)

    def listed(self, kind, *, owned=False):
        command = ["docker", "ps", "-aq", "--no-trunc"] if kind == "container" else ["docker", kind, "ls", "-q"]
        if kind == "network":
            command += ["--no-trunc"]
        if owned:
            command += ["--filter", "label=" + LABEL + "=" + self.token]
        return self.run(command).decode().split()

    def create_volume(self, role):
        require(role not in self.volumes, "duplicate owned volume role")
        self.verify()
        name = self.prefix + "-" + role
        require(name not in self.listed("volume"), "fresh drill volume name is occupied")
        self.attempted_resources.append({"kind": "volume", "name": name})
        self.run(["docker", "volume", "create", "--driver", "local", "--label", LABEL + "=" + self.token,
                  "--label", ROLE_LABEL + "=" + role, name])
        item = self.inspect("volume", name)
        self.volumes[role] = {"name": name, "created_at": item.get("CreatedAt"), "mountpoint": item.get("Mountpoint")}
        self.verify()
        return name

    def create_network(self):
        self.verify()
        name = self.prefix + "-internal"
        require(self.network is None and name not in self.listed("network"), "fresh drill network is occupied")
        self.attempted_resources.append({"kind": "network", "name": name})
        network_id = self.run(["docker", "network", "create", "--internal", "--driver", "bridge",
                               "--label", LABEL + "=" + self.token, "--label", ROLE_LABEL + "=internal", name]).decode().strip()
        self.network = {"name": name, "id": network_id}
        self.verify()
        return name

    def create_container(self, role, image, volume_role, target, entrypoint, command,
                         *, readonly_volume=False, database=False, user="0:0"):
        require(role not in self.containers and plan.IMAGE.fullmatch(image), "invalid owned container specification")
        self.verify()
        name = self.prefix + "-" + role
        require(name not in self.run(["docker", "ps", "-a", "--format", "{{.Names}}"]).decode().split(),
                "fresh drill container name is occupied")
        volume = self.volumes[volume_role]["name"]
        network = self.network["name"] if database else "none"
        image_info = self.inspect("image", image)
        require(image_info.get("Id") == image and image_info.get("Os") == "linux", "captured immutable image is unavailable")
        require(set(image_info.get("Config", {}).get("Volumes") or {}) == {target},
                "captured helper/database image would create an untracked anonymous volume")
        caps = [] if database else (["DAC_OVERRIDE"] if readonly_volume else ["CHOWN", "DAC_OVERRIDE", "FOWNER"])
        args = ["docker", "create", "--pull", "never", "--name", name, "-i", "--network", network,
                "--label", LABEL + "=" + self.token, "--label", ROLE_LABEL + "=" + role,
                "--restart", "no", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--cpus", "1", "--memory", "512m" if database else "128m", "--pids-limit", "128",
                "--log-driver", "none", "--no-healthcheck", "--user", user,
                "--mount", f"type=volume,source={volume},target={target},volume-nocopy" + (",readonly" if readonly_volume else ""),
                "--entrypoint", entrypoint]
        for cap in caps:
            args += ["--cap-add", cap]
        if database:
            args += ["--tmpfs", "/tmp:rw,noexec,nosuid,size=16777216,mode=1777"]
        self.attempted_resources.append({"kind": "container", "name": name})
        container_id = self.run(args + [image] + command).decode().strip()
        self.containers[role] = {"id": container_id, "name": name, "image": image, "volume": volume,
                                 "target": target, "readonly_volume": readonly_volume, "database": database,
                                 "network": network, "entrypoint": [entrypoint], "command": command,
                                 "caps": caps, "user": user}
        self.verify()
        return container_id

    def verify(self):
        require(set(self.listed("volume", owned=True)) == {v["name"] for v in self.volumes.values()},
                "owned volume allowlist changed")
        require(set(self.listed("container", owned=True)) == {c["id"] for c in self.containers.values()},
                "owned container allowlist changed")
        networks = {self.network["id"]} if self.network else set()
        require(set(self.listed("network", owned=True)) == networks, "owned network allowlist changed")
        for role, saved in self.volumes.items():
            item = self.inspect("volume", saved["name"])
            labels = item.get("Labels") or {}
            require(item.get("Name") == saved["name"] and item.get("Driver") == "local" and not item.get("Options") and
                    item.get("CreatedAt") == saved["created_at"] and item.get("Mountpoint") == saved["mountpoint"] and
                    labels.get(LABEL) == self.token and labels.get(ROLE_LABEL) == role,
                    "owned volume identity, labels or driver changed")
        all_ids = self.listed("container")
        items = json.loads(self.run(["docker", "inspect"] + all_ids)) if all_ids else []
        own_ids = {value["id"] for value in self.containers.values()}
        own_volumes = {value["name"] for value in self.volumes.values()}
        for item in items:
            if item.get("Id") not in own_ids:
                require(not any(m.get("Type") == "volume" and m.get("Name") in own_volumes for m in item.get("Mounts", [])),
                        "a foreign container mounts an owned drill volume")
                require(not self.network or self.network["name"] not in (item.get("NetworkSettings", {}).get("Networks") or {}),
                        "a foreign container joined the owned internal network")
        by_id = {item["Id"]: item for item in items}
        for role, saved in self.containers.items():
            item = by_id.get(saved["id"])
            require(item is not None, "owned container disappeared")
            cfg, host = item.get("Config") or {}, item.get("HostConfig") or {}
            labels = cfg.get("Labels") or {}
            require(item.get("Name") == "/" + saved["name"] and item.get("Image") == saved["image"] and
                    cfg.get("Image") == saved["image"] and cfg.get("Entrypoint") == saved["entrypoint"] and
                    cfg.get("Cmd") == saved["command"] and cfg.get("User") == saved["user"] and
                    labels.get(LABEL) == self.token and labels.get(ROLE_LABEL) == role,
                    "owned container identity, image or execution specification changed")
            require(not host.get("Privileged") and host.get("ReadonlyRootfs") is True and
                    host.get("NetworkMode") == saved["network"] and not host.get("PortBindings") and
                    not host.get("PublishAllPorts") and host.get("RestartPolicy", {}).get("Name") == "no" and
                    set(host.get("CapDrop") or []) == {"ALL"} and
                    host.get("LogConfig", {}).get("Type") == "none" and
                    set(host.get("SecurityOpt") or []) == {"no-new-privileges"} and {cap.removeprefix("CAP_") for cap in (host.get("CapAdd") or [])} == set(saved["caps"]),
                    "owned container isolation or privileges changed")
            require(set(host.get("Tmpfs") or {}) == ({"/tmp"} if saved["database"] else set()),
                    "owned container tmpfs allowlist changed")
            volumes = [m for m in item.get("Mounts", []) if m.get("Type") == "volume"]
            require(len(volumes) == 1 and volumes[0].get("Name") == saved["volume"] and
                    volumes[0].get("Destination") == saved["target"] and
                    volumes[0].get("RW") == (not saved["readonly_volume"]) and
                    all(m.get("Type") == "volume" or (m.get("Type") == "tmpfs" and m.get("Destination") == "/tmp")
                        for m in item.get("Mounts", [])), "owned container mount allowlist changed")
            attached = set((item.get("NetworkSettings", {}).get("Networks") or {}))
            require(attached.issubset({saved["network"]}), "owned container joined another network")
        if self.network:
            item = self.inspect("network", self.network["id"])
            labels = item.get("Labels") or {}
            require(item.get("Name") == self.network["name"] and item.get("Id") == self.network["id"] and
                    item.get("Internal") is True and item.get("Driver") == "bridge" and
                    labels.get(LABEL) == self.token and labels.get(ROLE_LABEL) == "internal" and
                    set(item.get("Containers") or {}).issubset(own_ids), "owned network identity or peers changed")

    def require_recorded_container(self, container_id, *, database):
        matches = [saved for saved in self.containers.values() if saved["id"] == container_id]
        require(isinstance(container_id, str) and plan.CONTAINER.fullmatch(container_id) is not None and
                len(matches) == 1 and matches[0]["database"] is database,
                "start target is not a recorded owned container with the required purpose")
        self.verify()

    def execute(self, container_id, *, input_file=None, output_file=None):
        self.require_recorded_container(container_id, database=False)
        self.run(["docker", "start", "-a", "-i", container_id], input_file=input_file,
                 output_file=output_file, timeout=600)
        item = self.inspect("container", container_id)
        require(item.get("State", {}).get("Running") is False and item.get("State", {}).get("ExitCode") == 0,
                "volume helper did not complete successfully")
        self.verify()

    def start_database(self, container_id):
        self.require_recorded_container(container_id, database=True)
        self.run(["docker", "start", container_id])
        self.verify()

    def cleanup(self):
        # Never remove anything if any current identity, mount or foreign peer
        # differs. Docker also refuses in-use volumes/networks if a peer races.
        self.verify()
        for saved in self.containers.values():
            self.verify()
            self.run(["docker", "rm", "-f", saved["id"]])
            self.containers = {role: c for role, c in self.containers.items() if c["id"] != saved["id"]}
        self.verify()
        if self.network:
            self.run(["docker", "network", "rm", self.network["id"]])
            self.network = None
        for role in list(self.volumes):
            self.verify()
            self.run(["docker", "volume", "rm", self.volumes[role]["name"]])
            del self.volumes[role]
        self.verify()

    def resources(self):
        return json.loads(json.dumps({"prefix": self.prefix, "volumes": self.volumes,
                                      "containers": self.containers, "network": self.network,
                                      "attempted_resources": self.attempted_resources}))


def drill_plan(checked):
    return {"mode": "read_only_isolated_restore_plan", "manifest_sha256": checked["manifest_sha256"],
            "source_artifacts_verified": 16, "volume_archive_count": 8,
            "app_code_source_verification": checked["app_code_verification"],
            "captured_git_commit": checked["manifest"]["nextcloud_git_commit"],
            "source_artifact_sha256": checked["manifest"]["artifact_sha256"],
            "app_code_and_runtime_inputs_source_verified": True,
            "app_code_and_runtime_inputs_restored": False,
            "new_resources_only": True, "shared_target_modified": False,
            "application_runtime_restored": False, "external_replay_verified": False,
            "ingress_reopen_permitted": False}


def verify_databases(owner, checked):
    owner.create_network()
    report = {}
    for project in plan.PROJECTS:
        short = plan.SHORT[project]
        config = checked["configs"][project]
        service = "db" if project == capture.NC_PROJECT else "postgres"
        environment = config["services"][service].get("environment") or {}
        user = environment.get("POSTGRES_USER", "postgres")
        database = environment.get("POSTGRES_DB", user)
        require(isinstance(user, str) and user and isinstance(database, str) and database, "saved database identity is unavailable")
        archive = checked["archives"][f"{short}-postgres-data.tar"]
        root = archive["entries"].get("")
        require(root and root["kind"] == "directory", "PostgreSQL archive lacks its root ownership")
        require(not ({"standby.signal", "recovery.signal"} & set(archive["entries"])),
                "standby/external recovery state is not supported by this closed rehearsal")
        image = checked["manifest"]["projects"][project]["containers"][service]["image_id"]
        role = short + "-postgres"
        container = owner.create_container(role, image, short + "-postgres-data", DATA, "postgres",
                    ["-D", DATA, "-c", "listen_addresses=", "-c", "unix_socket_directories=/tmp",
                     "-c", "default_transaction_read_only=on", "-c", "unix_socket_permissions=0700",
                     "-c", "archive_mode=off", "-c", "archive_command=", "-c", "restore_command=",
                     "-c", "primary_conninfo=", "-c", "primary_slot_name=", "-c", "max_wal_senders=0",
                     "-c", "shared_preload_libraries=", "-c", "autovacuum=off"], database=True, user=f"{root['uid']}:{root['gid']}")
        owner.start_database(container)
        for _ in range(60):
            try:
                owner.run(["docker", "exec", container, "pg_isready", "-h", "/tmp", "-U", user, "-d", database], timeout=10)
                break
            except capture.CheckpointError:
                require(owner.inspect("container", container).get("State", {}).get("Running") is True, "restored PostgreSQL stopped")
                time.sleep(0.5)
        else:
            raise DrillError("restored PostgreSQL was not ready")
        if project == capture.NC_PROJECT:
            query = "SELECT json_build_object('server_version',current_setting('server_version'),'users',(SELECT count(*) FROM oc_users),'files',(SELECT count(*) FROM oc_filecache),'publication_states',(SELECT count(*) FROM oc_weknora_pub_state),'publication_decisions',(SELECT count(*) FROM oc_weknora_pub_audit),'integration_version',(SELECT configvalue FROM oc_appconfig WHERE appid='integration_weknora' AND configkey='installed_version'));"
        else:
            query = "SELECT json_build_object('server_version',current_setting('server_version'),'schema_version',(SELECT version FROM schema_migrations),'schema_dirty',(SELECT dirty FROM schema_migrations),'tenants',(SELECT count(*) FROM tenants),'knowledge_bases',(SELECT count(*) FROM knowledge_bases),'knowledges',(SELECT count(*) FROM knowledges),'chunks',(SELECT count(*) FROM chunks));"
        owner.verify()
        values = json.loads(owner.run(["docker", "exec", container, "psql", "-h", "/tmp", "-U", user, "-d", database,
                                       "-v", "ON_ERROR_STOP=1", "-Atc", query]).decode())
        require(isinstance(values, dict) and (project == capture.NC_PROJECT or values.get("schema_dirty") is False),
                "restored database schema is dirty or unavailable")
        report[short + "_postgres"] = values
        redis_image = checked["manifest"]["projects"][project]["containers"]["redis"]["image_id"]
        redis_root = checked["archives"][f"{short}-redis-data.tar"]["entries"].get("")
        require(redis_root and redis_root["kind"] == "directory", "Redis archive lacks its root ownership")
        redis = owner.create_container(short + "-redis", redis_image, short + "-redis-data", "/data", "redis-server",
                    ["--dir", "/data", "--appendonly", "yes", "--port", "0", "--unixsocket", "/tmp/redis.sock", "--unixsocketperm", "700", "--save", "", "--daemonize", "no"],
                    database=True, user=f"{redis_root['uid']}:{redis_root['gid']}")
        owner.start_database(redis)
        for _ in range(60):
            try:
                reply = owner.run(["docker", "exec", redis, "redis-cli", "-s", "/tmp/redis.sock", "PING"], timeout=10).strip()
                require(reply == b"PONG", "restored Redis did not reply")
                break
            except (capture.CheckpointError, DrillError):
                require(owner.inspect("container", redis).get("State", {}).get("Running") is True, "restored Redis stopped")
                time.sleep(0.5)
        else:
            raise DrillError("restored Redis was not ready")
        owner.verify()
        count = int(owner.run(["docker", "exec", redis, "redis-cli", "-s", "/tmp/redis.sock", "DBSIZE"]).strip())
        info = owner.run(["docker", "exec", redis, "redis-cli", "-s", "/tmp/redis.sock", "INFO", "server"]).decode()
        version = next(line.split(":", 1)[1].strip() for line in info.splitlines() if line.startswith("redis_version:"))
        report[short + "_redis"] = {"server_version": version, "db0_key_count": count}
    owner.verify()
    return report


def apply_drill(checked, checkpoint, cwd, output_dir, *, volumes_only=False):
    for inventory in checked["archives"].values():
        require_representable(inventory)
    token = secrets.token_hex(12)
    require(not output_dir.exists() and output_dir.is_absolute() and not output_dir.is_symlink(), "evidence destination must be a new absolute directory")
    require(output_dir.parent.resolve(strict=True) == output_dir.parent,
            "evidence parent contains a symlink")
    output_dir.mkdir(mode=0o700)
    plan.plain_owned(output_dir, 0o700, directory=True)
    owner = OwnedDrill(cwd, token)
    report = drill_plan(checked)
    report.update(mode="isolated_cold_volume_restore_drill", status="INCOMPLETE", volumes=[], database_validation={}, cleanup_verified=False)
    helper_image = checked["manifest"]["projects"][capture.NC_PROJECT]["containers"]["db"]["image_id"]
    try:
        for project in plan.PROJECTS:
            for role in sorted(plan.VOLUMES[project]):
                short, archive = plan.SHORT[project], f"{plan.SHORT[project]}-{role}.tar"
                volume_role = short + "-" + role
                copied, audited = output_dir / (archive + ".staged"), output_dir / (archive + ".audit")
                expected_sha = checked["manifest"]["artifact_sha256"][archive]
                staged_archive(checkpoint / archive, copied, expected_sha)
                expected = plan.archive_inventory(copied)
                compare_inventory(checked["archives"][archive], expected)
                owner.create_volume(volume_role)
                restore = owner.create_container(volume_role + "-extract", helper_image, volume_role, DATA, "tar", ["-C", DATA, "-xpf", "-"])
                owner.execute(restore, input_file=copied)
                require(capture.sha256(copied) == expected_sha, "staged archive changed during extraction")
                if any(entry["kind"] == "symlink" for entry in expected["entries"].values()):
                    link_script = output_dir / (archive + ".link-owners.sh")
                    with link_script.open("x") as stream:
                        os.chmod(link_script, 0o600)
                        stream.write(symlink_owner_script(expected))
                    links = owner.create_container(volume_role + "-links", helper_image, volume_role,
                                                   DATA, "sh", ["-s"])
                    owner.execute(links, input_file=link_script)
                    link_script.unlink()
                audit = owner.create_container(volume_role + "-audit", helper_image, volume_role, DATA, "tar", ["-C", DATA, "-cf", "-", "."], readonly_volume=True)
                with audited.open("xb") as stream:
                    os.chmod(audited, 0o600)
                    owner.execute(audit, output_file=stream)
                actual = plan.archive_inventory(audited)
                inventory_hash = compare_inventory(expected, actual)
                report["volumes"].append({"archive": archive, "source_archive_sha256": expected_sha,
                    "restored_inventory_sha256": inventory_hash, "member_count": len(expected["entries"]),
                    "payload_bytes": expected["payload_bytes"], "exact_members_bytes_owners_modes_links_verified": True})
                copied.unlink(); audited.unlink()
        report["byte_validation_before_database_start"] = True
        if not volumes_only:
            report["database_validation"] = verify_databases(owner, checked)
        report["database_validation_performed"] = not volumes_only
        report["resource_inventory_at_validation"] = owner.resources()
        owner.cleanup()
        report.update(status="COMPLETE", cleanup_verified=True)
        private_json(output_dir / "result.json", report)
        return report
    except BaseException:
        # Ownership loss must never turn into a broad cleanup. The resources
        # were born with closed sockets/no ports; retain them if proof fails.
        try:
            owner.cleanup()
            report["cleanup_verified"] = True
        except (DrillError, capture.CheckpointError, plan.RestorePlanError, OSError, ValueError, KeyError):
            report["retained_closed_resources"] = owner.resources()
        private_json(output_dir / "failure.json", report)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--nextcloud-dir", type=Path, default=plan.ROOT)
    parser.add_argument("--weknora-dir", type=Path, default=plan.ROOT.parent / "weknora-ldap-local")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--volumes-only", action="store_true", help="restore/verify all volumes without starting database processes")
    args = parser.parse_args(argv)
    try:
        nc, wk = args.nextcloud_dir, args.weknora_dir
        require(nc.is_absolute() and wk.is_absolute() and nc.is_dir() and wk.is_dir() and nc != wk,
                "source project roots must be distinct existing absolute directories")
        checked = plan.verify_checkpoint(args.checkpoint_dir, nc, wk, args.expected_manifest_sha256)
        if not args.apply:
            print(json.dumps(drill_plan(checked), indent=2, sort_keys=True))
            return 0
        capture.local_docker(nc)
        if args.evidence_dir is None:
            (plan.ROOT / "dist").mkdir(mode=0o700, exist_ok=True)
        output = args.evidence_dir or plan.ROOT / "dist" / ("shared-cold-drill-" + secrets.token_hex(12))
        result = apply_drill(checked, args.checkpoint_dir, nc, output, volumes_only=args.volumes_only)
        print(json.dumps({"status": result["status"], "evidence": str(output), "volume_count": len(result["volumes"]),
                          "database_validation_performed": result["database_validation_performed"],
                          "cleanup_verified": result["cleanup_verified"], "application_runtime_restored": False,
                          "external_replay_verified": False, "shared_target_modified": False}, indent=2, sort_keys=True))
        return 0
    except (DrillError, capture.CheckpointError, plan.RestorePlanError, OSError, ValueError, TypeError, KeyError):
        print("Isolated cold restore drill refused; inspect private evidence if present", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
