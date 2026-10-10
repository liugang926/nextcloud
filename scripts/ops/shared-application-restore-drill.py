#!/usr/bin/env python3
"""Restore captured applications/LDAP into a fresh internal, fenced rehearsal.

No host ports, public ingress, shared resources, model calls or replay are
permitted. The default verifies the source without writes. The original
sixteen artifacts remain unchanged; runtime fencing is an explicit overlay.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("volume_drill", HERE / "shared-cold-restore-drill.py")
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)
plan, capture, require = drill.plan, drill.capture, drill.require


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_private(path, value):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(value)
    return path


def extract_private(source, destination, expected, *, code=False):
    """Extract only authenticated inventory entries without following links.

    Runtime inputs have private host permissions. Application files retain
    their executable bits and are readable by the restored PHP service.
    Symlinks/hardlinks are created last, after every parent and file is safe.
    """
    require(not destination.exists() and not destination.is_symlink(), "staging root is occupied")
    destination.mkdir(mode=0o700)
    entries = expected["entries"]
    roots = set()
    for name in entries:
        for parent in Path(name).parents:
            if str(parent) != ".":
                roots.add(str(parent))
    roots.update(name for name, entry in entries.items() if entry["kind"] == "directory")
    for name in sorted(roots, key=lambda value: (len(Path(value).parts), value)):
        target = destination / name
        target.mkdir(mode=0o755 if code else 0o700)
    with tarfile.open(source, "r:") as archive:
        members = {plan.safe_name(m.name): m for m in archive}
        require(set(members) == set(entries), "staging archive membership differs")
        for name, entry in entries.items():
            if entry["kind"] != "file":
                continue
            target = destination / name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            descriptor = os.open(target, flags, 0o644 if code else 0o600)
            hasher, count = hashlib.sha256(), 0
            with os.fdopen(descriptor, "wb") as out, archive.extractfile(members[name]) as incoming:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    out.write(chunk)
                    hasher.update(chunk)
                    count += len(chunk)
            require(count == entry["size"] and hasher.hexdigest() == entry["sha256"], "extracted input bytes differ")
            os.chmod(target, 0o755 if code and entry["mode"] & 0o111 else 0o644 if code else 0o600)
        pending = {name for name, entry in entries.items() if entry["kind"] == "hardlink"}
        while pending:
            ready = {name for name in pending if (destination / entries[name]["target"]).is_file()}
            require(bool(ready), "staged hardlink target is unavailable")
            for name in ready:
                os.link(destination / entries[name]["target"], destination / name, follow_symlinks=False)
            pending -= ready
        for name, entry in entries.items():
            if entry["kind"] == "symlink":
                os.symlink(entry["linkname"], destination / name)
    verify_staging(destination, expected, code=code)


def verify_staging(root, expected, *, code=False):
    require(root.is_dir() and not root.is_symlink(), "private staging root disappeared")
    require(stat.S_IMODE(root.stat().st_mode) == 0o700 and root.stat().st_uid == os.getuid(), "staging root is not private")
    # Include implicit ancestor directories because capture may start at a file.
    allowed = set(expected["entries"])
    for name in list(allowed):
        allowed.update(str(p) for p in Path(name).parents if str(p) != ".")
    actual = {str(p.relative_to(root)) for p in root.rglob("*")}
    require(actual == allowed, "staging has missing or unexpected members")
    inode_groups = {}
    for name, entry in expected["entries"].items():
        path = root / name
        info = path.lstat()
        require(info.st_uid == os.getuid(), "staged input ownership changed")
        if entry["kind"] == "directory":
            require(stat.S_ISDIR(info.st_mode), "staged directory changed type")
        elif entry["kind"] == "symlink":
            require(stat.S_ISLNK(info.st_mode) and os.readlink(path) == entry["linkname"], "staged symlink changed")
        else:
            require(stat.S_ISREG(info.st_mode), "staged file changed type")
            original = entry
            while original["kind"] == "hardlink":
                original = expected["entries"][original["target"]]
            require(info.st_size == original["size"] and capture.sha256(path) == original["sha256"], "staged file bytes changed")
            inode_groups[name] = (info.st_dev, info.st_ino)
            require(stat.S_IMODE(info.st_mode) == (0o755 if code and entry["mode"] & 0o111 else 0o644 if code else 0o600), "staged file permissions changed")
    identity = drill.inventory_identity(expected)
    for name, inode in inode_groups.items():
        require({n for n, value in inode_groups.items() if value == inode} == set(identity[name]["hardlink_group"]), "staged hardlink topology changed")


def fenced_environment(saved, kind, hosts):
    """Retain captured secrets/config, then override only isolation controls."""
    env = dict(saved)
    require(all(isinstance(k, str) and isinstance(v, str) and "\n" not in k + v and "\x00" not in k + v for k, v in env.items()), "unsupported saved environment")
    for key in list(env):
        if key.lower().endswith("proxy") or key in {"RAG_MODEL_TOKEN", "WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL"}:
            env.pop(key)
    if kind == "weknora":
        env.update(DB_HOST=hosts["weknora-postgres"], REDIS_ADDR=hosts["weknora-redis"] + ":6379",
                   AUTO_MIGRATE="false", WEKNORA_NEXTCLOUD_SYNC_RECOVERY_ENABLED="false",
                   WEKNORA_SANDBOX_DOCKER_ENABLED="false", LOG_LEVEL="warn", GIN_MODE="release")
    elif kind == "nextcloud":
        env.update(POSTGRES_HOST=hosts["nextcloud-postgres"], REDIS_HOST=hosts["nextcloud-redis"])
    return env


class ApplicationDrill(drill.OwnedDrill):
    def __init__(self, cwd, token):
        super().__init__(cwd, token)
        self.staged_roots, self.control_files = [], {}

    def run(self, args, *, input_file=None, output_file=None, timeout=120):
        # Runtime startup may fail before a process can write Docker logs.
        # Preserve that daemon/exec stderr privately without echoing arguments.
        if not hasattr(self, "evidence"):
            return super().run(args, input_file=input_file, output_file=output_file, timeout=timeout)
        try:
            with (input_file.open("rb") if input_file else open(os.devnull, "rb")) as source:
                result = subprocess.run(args, cwd=self.cwd, stdin=source, stdout=output_file or subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=timeout, env=capture.safe_environment())
        except (OSError, subprocess.TimeoutExpired) as error:
            write_private(self.evidence / ("command-failure-" + secrets.token_hex(8) + ".log"), str(error).encode())
            raise capture.CheckpointError("owned Docker command could not complete") from error
        if result.returncode:
            write_private(self.evidence / ("command-failure-" + secrets.token_hex(8) + ".log"),
                          (result.stdout if output_file is None else b"") + result.stderr)
            raise capture.CheckpointError("owned Docker command failed; inspect private command log")
        return result.stdout if output_file is None else b""

    def verify(self):
        for root, inventory, code in self.staged_roots:
            verify_staging(root, inventory, code=code)
        for path, saved in self.control_files.items():
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_uid == os.getuid() and
                    info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == saved["mode"] and
                    (info.st_dev, info.st_ino) == saved["identity"], "runtime overlay identity or mode changed")
            if not saved["mutable"]:
                require(capture.sha256(path) == saved["sha256"], "runtime overlay bytes changed")
        super().verify()

    def control(self, path, value, *, mutable=False, public=False):
        if path in self.control_files:
            self.verify()
            require(self.control_files[path]["mutable"] == mutable and (mutable or self.control_files[path]["sha256"] == hashlib.sha256(value).hexdigest()), "recorded runtime overlay differs")
            return path
        write_private(path, value)
        if public:
            os.chmod(path, 0o644)
        info = path.stat()
        self.control_files[path] = {"mode": stat.S_IMODE(info.st_mode), "identity": (info.st_dev, info.st_ino),
                                    "sha256": capture.sha256(path), "mutable": mutable}
        return path

    def create_runtime(self, role, image, entrypoint, command, mounts, *, environment=None,
                       user="0:0", caps=(), tmpfs=None, memory="1g", network=True):
        require(plan.IMAGE.fullmatch(image), "invalid runtime specification")
        self.verify()
        require(self.network is not None, "runtime requires an owned internal network")
        name = self.prefix + "-" + role
        if role not in self.containers:
            require(name not in self.run(["docker", "ps", "-a", "--format", "{{.Names}}"]).decode().split(), "fresh runtime name is occupied")
        info = self.inspect("image", image)
        require(info.get("Id") == image and info.get("Os") == "linux", "captured runtime image is unavailable")
        require(set(info.get("Config", {}).get("Volumes") or {}).issubset({m["target"] for m in mounts}), "runtime image would create anonymous volumes")
        network_name = self.network["name"] if network else "none"
        args = ["docker", "create", "--pull", "never", "--name", name, "--network", network_name,
                "--label", drill.LABEL + "=" + self.token, "--label", drill.ROLE_LABEL + "=" + role,
                "--restart", "no", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--cpus", "1", "--memory", memory, "--pids-limit", "256", "--log-driver", "json-file",
                "--log-opt", "max-size=5m", "--log-opt", "max-file=2", "--no-healthcheck", "--user", user,
                "--entrypoint", entrypoint]
        for cap in caps:
            args += ["--cap-add", cap]
        tmpfs = tmpfs or {"/tmp": "rw,nosuid,size=67108864,mode=1777"}
        for target, options in tmpfs.items():
            args += ["--tmpfs", target + ":" + options]
        expected_mounts = []
        for m in mounts:
            require(m["type"] in {"volume", "bind"}, "unsupported runtime mount")
            if m["type"] == "volume":
                require(m["source"] in self.volumes, "runtime mounts an unowned volume")
                source = self.volumes[m["source"]]["name"]
                args += ["--mount", f"type=volume,source={source},target={m['target']},volume-nocopy" + (",volume-subpath=" + m["subpath"] if m.get("subpath") else "") + (",readonly" if m["readonly"] else "")]
                expected_mounts.append({"type": "volume", "source": source, "target": m["target"], "readonly": m["readonly"], "subpath": m.get("subpath", ""), "volume_path": self.volumes[m["source"]]["mountpoint"]})
            else:
                source = Path(m["source"])
                require(source.is_absolute() and (any(source == root or root in source.parents for root, _, _ in self.staged_roots) or source in self.control_files), "runtime bind is not private owned staging")
                args += ["--mount", f"type=bind,source={source},target={m['target']}" + (",readonly" if m["readonly"] else "")]
                expected_mounts.append({"type": "bind", "source": str(source), "target": m["target"], "readonly": m["readonly"]})
        environment = environment or {}
        if environment:
            env_path = self.control(self.evidence / (role + ".env"), "".join(k + "=" + v + "\n" for k, v in sorted(environment.items())).encode())
            args += ["--env-file", str(env_path)]
        # Image ENV defaults must be part of the exact specification too.
        merged = dict(v.split("=", 1) for v in info["Config"].get("Env") or [])
        merged.update(environment)
        if role in self.containers:
            saved = self.containers[role]
            require(saved.get("runtime") and saved["image"] == image and saved["entrypoint"] == [entrypoint] and
                    (saved["command"] or []) == command and saved["user"] == user and saved["caps"] == list(caps) and
                    saved["network"] == network_name and saved["tmpfs"] == tmpfs and saved["mounts"] == expected_mounts and
                    saved["environment_sha256"] == digest(merged), "prepared runtime differs from captured fenced specification")
            return saved["id"]
        self.attempted_resources.append({"kind": "container", "name": name})
        cid = self.run(args + [image] + command).decode().strip()
        self.containers[role] = {"id": cid, "name": name, "image": image, "runtime": True,
                "database": True, "network": network_name, "entrypoint": [entrypoint], "command": command,
                "mounts": expected_mounts, "caps": list(caps), "user": user, "tmpfs": tmpfs,
                "environment_sha256": digest(merged), "memory": 1024**3 if memory == "1g" else 512 * 1024**2}
        self.verify()
        return cid

    def verify_runtime_container(self, role, saved, item):
        require(item is not None, "owned runtime disappeared")
        cfg, host = item.get("Config") or {}, item.get("HostConfig") or {}
        labels = cfg.get("Labels") or {}
        require(item.get("Id") == saved["id"] and item.get("Name") == "/" + saved["name"] and
                item.get("Image") == saved["image"] and cfg.get("Image") == saved["image"] and
                cfg.get("Entrypoint") == saved["entrypoint"] and (cfg.get("Cmd") or []) == (saved["command"] or []) and
                cfg.get("User") == saved["user"] and labels.get(drill.LABEL) == self.token and
                labels.get(drill.ROLE_LABEL) == role and
                digest(dict(v.split("=", 1) for v in cfg.get("Env") or [])) == saved["environment_sha256"], "owned runtime identity, image, environment or command changed")
        require(host.get("ReadonlyRootfs") is True and not host.get("Privileged") and
                host.get("NetworkMode") == saved["network"] and not host.get("PortBindings") and
                not host.get("PublishAllPorts") and host.get("RestartPolicy", {}).get("Name") == "no" and
                set(host.get("CapDrop") or []) == {"ALL"} and
                {v.removeprefix("CAP_") for v in host.get("CapAdd") or []} == set(saved["caps"]) and
                set(host.get("SecurityOpt") or []) == {"no-new-privileges"} and
                host.get("NanoCpus") == 1_000_000_000 and host.get("Memory") == saved["memory"] and
                host.get("PidsLimit") == 256 and host.get("LogConfig") == {"Type": "json-file", "Config": {"max-size": "5m", "max-file": "2"}} and
                host.get("Tmpfs") == saved["tmpfs"], "owned runtime isolation changed")
        actual = []
        for mount in item.get("Mounts", []):
            if mount["Type"] == "tmpfs":
                require(mount["Destination"] in saved["tmpfs"], "unknown runtime tmpfs")
                continue
            source = mount.get("Name") if mount["Type"] == "volume" else mount.get("Source")
            # Docker Desktop records host bind paths under /host_mnt;
            # requested HostConfig mounts are checked with the same documented alias below.
            if mount["Type"] == "bind" and isinstance(source, str) and source.startswith("/host_mnt/"):
                source = source[len("/host_mnt"):]
            value = {"type": mount["Type"], "source": source, "target": mount["Destination"], "readonly": not mount["RW"]}
            expected = next((m for m in saved["mounts"] if m["target"] == mount["Destination"]), {})
            if mount["Type"] == "volume" and "volume_path" in expected:
                # Engine inspect reports the volume root even for Subpath.
                # Historical own records appended Subpath to that root;
                # accept only this deterministic representation, while
                # checking the requested Subpath independently below.
                physical = mount["Source"]
                if expected["subpath"] and expected["volume_path"] == physical + "/" + expected["subpath"]:
                    physical += "/" + expected["subpath"]
                value.update(volume_path=physical, subpath=expected["subpath"])
            actual.append(value)
        require(sorted(actual, key=lambda m: m["target"]) == sorted(saved["mounts"], key=lambda m: m["target"]), "owned runtime mounts changed")
        if any(m["type"] == "bind" or m.get("subpath") for m in saved["mounts"]):
            requested = [{"type": m["Type"], "source": m["Source"][len("/host_mnt"):] if m["Type"] == "bind" and m["Source"].startswith("/host_mnt/") else m["Source"], "target": m["Target"], "readonly": bool(m.get("ReadOnly"))} for m in host.get("Mounts") or []]
            for value, raw in zip(requested, host.get("Mounts") or []):
                if raw["Type"] == "volume":
                    expected = next((m for m in saved["mounts"] if m["target"] == value["target"]), {})
                    if "subpath" in expected:
                        value["subpath"] = raw.get("VolumeOptions", {}).get("Subpath", "")
                    else:
                        require(not raw.get("VolumeOptions", {}).get("Subpath"), "runtime gained an unexpected volume subpath")
            expected_requested = [{k: v for k, v in m.items() if k != "volume_path"} for m in saved["mounts"]]
            require(sorted(requested, key=lambda m: m["target"]) == sorted(expected_requested, key=lambda m: m["target"]), "runtime requested mount specification changed")
        require(set(item.get("NetworkSettings", {}).get("Networks") or {}).issubset({saved["network"]}), "runtime joined another network")

    def exec(self, role, command, *, user=None):
        self.verify()
        saved = self.containers[role]
        require(saved.get("runtime"), "exec target is not an owned runtime")
        args = ["docker", "exec"] + (["--user", user] if user else [])
        return self.run(args + [saved["id"]] + command, timeout=30)

    def ready(self, role, command, accept=lambda value: bool(value), *, user=None):
        for _ in range(60):
            try:
                value = self.exec(role, command, user=user)
                if not accept(value):
                    path = self.evidence / (role + "-rejected-readiness.log")
                    if not path.exists():
                        write_private(path, value)
                    raise drill.DrillError("runtime readiness reply differs")
                return value
            except (drill.DrillError, capture.CheckpointError):
                require(self.inspect("container", self.containers[role]["id"]).get("State", {}).get("Running") is True, "restored runtime stopped")
                time.sleep(0.5)
        raise drill.DrillError("restored runtime was not ready")

    def resources(self):
        result = super().resources()
        result["control_files"] = {str(path): saved for path, saved in self.control_files.items()}
        return result

    def save_logs(self, evidence):
        self.verify()
        for role, value in self.containers.items():
            if value.get("runtime"):
                path = evidence / (role + ".log")
                if not path.exists():
                    with path.open("xb") as stream:
                        os.chmod(path, 0o600)
                        result = subprocess.run(["docker", "logs", value["id"]], cwd=self.cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=30, env=capture.safe_environment())
                        require(result.returncode == 0, "owned runtime logs could not be preserved")


def volume(source, target, readonly=True, subpath=None):
    result = {"type": "volume", "source": source, "target": target, "readonly": readonly}
    if subpath:
        require(plan.safe_name(subpath) == subpath, "invalid runtime volume subpath")
        result["subpath"] = subpath
    return result


def bind(source, target, readonly=True):
    return {"type": "bind", "source": source, "target": target, "readonly": readonly}


def staged_bind_path(source, roots):
    source = Path(source)
    matches = [(original, staged) for original, staged in roots.items()
               if source == original or original in source.parents]
    require(len(matches) == 1, "saved bind does not have one approved runtime input root")
    original, staged = matches[0]
    target = staged / source.relative_to(original)
    require(target.exists() and target.resolve(strict=True) == target and (target.is_file() or target.is_dir()),
            "saved bind descendant is missing, symbolic or not regular")
    return target


def audit_volume(owner, role, image, path, expected, *, ignore_lmdb_lock=False):
    helper_role = role + "-" + path.stem
    if helper_role in owner.containers:
        saved = owner.containers[helper_role]
        require(saved.get("runtime") is not True and saved["database"] is False and saved["image"] == image and
                saved["volume"] == owner.volumes[role]["name"] and saved["target"] == drill.DATA and
                saved["readonly_volume"] is True and saved["entrypoint"] == ["tar"] and saved["command"] == ["-C", drill.DATA, "-cf", "-", "."], "prepared audit helper differs")
        helper = saved["id"]
    else:
        helper = owner.create_container(helper_role, image, role, drill.DATA, "tar", ["-C", drill.DATA, "-cf", "-", "."], readonly_volume=True)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        owner.execute(helper, output_file=stream)
    actual = plan.archive_inventory(path)
    if ignore_lmdb_lock:
        # Only LMDB mutex bytes may change. All membership/metadata and every
        # database/configuration byte are still compared exactly.
        left, right = drill.inventory_identity(expected), drill.inventory_identity(actual)
        require(set(left) == set(right), "LDAP execution copy membership changed")
        lock = "data/lock.mdb"
        require(lock in left and left[lock]["kind"] == right[lock]["kind"] == "file", "LDAP mutex file is unavailable")
        for key in ("sha256", "size"):
            left[lock].pop(key); right[lock].pop(key)
        require(left == right, "LDAP execution copy database/configuration bytes or metadata changed")
        result = digest(right)
    else:
        result = drill.compare_inventory(expected, actual)
    path.unlink()
    return result


def verify_ldap(owner, checked, original_paths, evidence):
    # slapd rejects an entirely read-only MDB directory at startup. Preserve
    # the restored original read-only and use a verified disposable execution
    # copy. Original slapd.d is submounted read-only; only MDB lock/data paths
    # can be written on the execution copy, and database bytes must stay equal.
    ldap_config = checked["configs"][capture.WK_PROJECT]["services"]["openldap"]
    archive = evidence / "ldap-runtime-copy.tar"
    drill.staged_archive(owner.checkpoint / "weknora-ldap-data.tar", archive, checked["manifest"]["artifact_sha256"]["weknora-ldap-data.tar"])
    copied_inventory = plan.archive_inventory(archive)
    drill.compare_inventory(checked["archives"]["weknora-ldap-data.tar"], copied_inventory)
    new_cache = "ldap-runtime-cache" not in owner.volumes
    if new_cache:
        owner.create_volume("ldap-runtime-cache")
    helper_image = checked["manifest"]["projects"][capture.NC_PROJECT]["containers"]["db"]["image_id"]
    if new_cache:
        helper = owner.create_container("ldap-runtime-extract", helper_image, "ldap-runtime-cache", drill.DATA, "tar", ["-C", drill.DATA, "-xpf", "-"])
        owner.execute(helper, input_file=archive)
    audit_volume(owner, "ldap-runtime-cache", helper_image, evidence / ("ldap-before-start.tar" if new_cache else "ldap-resume-before-start.tar"), copied_inventory, ignore_lmdb_lock=not new_cache)
    archive.unlink()
    ldap_mounts = [volume("ldap-runtime-cache", "/bitnami/openldap", False),
                   volume("weknora-ldap-data", "/recovery/ldap-source"),
                   volume("weknora-ldap-data", "/bitnami/openldap/slapd.d", subpath="slapd.d")]
    for mount in ldap_config["volumes"]:
        if mount["type"] == "bind":
            ldap_mounts.append(bind(staged_bind_path(mount["source"], original_paths), mount["target"]))

    password = owner.control(evidence / "ldap-bind-password", ldap_config["environment"]["LDAP_ADMIN_PASSWORD"].encode())
    ldap_mounts.append(bind(password, "/run/recovery/ldap-password"))
    if "openldap" in owner.containers:
        cid = owner.containers["openldap"]["id"]
    else:
        cid = owner.create_runtime("openldap", checked["manifest"]["projects"][capture.WK_PROJECT]["containers"]["openldap"]["image_id"], "/opt/bitnami/openldap/sbin/slapd",
               ["-F", "/bitnami/openldap/slapd.d", "-h", "ldap://0.0.0.0:1389/ ldaps://0.0.0.0:1636/", "-d", "256"], ldap_mounts, caps=["DAC_OVERRIDE", "NET_BIND_SERVICE"],
               tmpfs={"/tmp": "rw,nosuid,size=16777216,mode=1777", "/opt/bitnami/openldap/var/run": "rw,nosuid,size=16777216"})
    owner.start_database(cid)
    ldap_env = ldap_config["environment"]
    # The private LDAP bind password is supplied by file, never argv.
    admin_dn = "cn=" + ldap_env["LDAP_ADMIN_USERNAME"] + "," + ldap_env["LDAP_ROOT"]
    ldap_command = ["/opt/bitnami/openldap/bin/ldapwhoami", "-x", "-H", "ldap://127.0.0.1:1389", "-D", admin_dn, "-y", "/run/recovery/ldap-password"]
    ldap_reply = owner.ready("openldap", ldap_command, lambda v: v.strip().startswith(b"dn:"))
    require(ldap_reply.strip() == ("dn:" + admin_dn).encode(), "restored LDAP admin identity differs")
    audit_volume(owner, "ldap-runtime-cache", helper_image, evidence / "ldap-after-bind.tar", copied_inventory, ignore_lmdb_lock=True)
    return True


def prepare_nextcloud_data_cache(owner, checked, evidence):
    """Writable data root with every original file/subdirectory mounted RO.

    Nextcloud requires a writable data root even for console status. The
    disposable backing root retains exact copies of all original flat files.
    The active view covers those files and every original subtree with RO
    subpath mounts from the original verified HTML volume.
    """
    original = checked["archives"]["nextcloud-nextcloud-html.tar"]
    entries = original["entries"]
    require(entries.get("data", {}).get("kind") == "directory", "captured HTML has no ordinary data root")
    top = {name: entry for name, entry in entries.items() if name.startswith("data/") and len(Path(name).parts) == 2}
    require(top and all(entry["kind"] in {"file", "directory"} for entry in top.values()), "unsupported Nextcloud data-root file/link type")
    require(all("," not in name and "\n" not in name for name in top), "data-root mount path cannot be represented safely")
    require(any(top.get("data/" + marker, {}).get("kind") == "file" for marker in (".ncdata", ".ocdata")), "captured data-root marker is unavailable")
    copied, filtered = evidence / "nextcloud-html-for-root-cache.tar", evidence / "nextcloud-data-root.tar"
    drill.staged_archive(owner.checkpoint / "nextcloud-nextcloud-html.tar", copied,
                         checked["manifest"]["artifact_sha256"]["nextcloud-nextcloud-html.tar"])
    write_private(filtered, b"")
    with tarfile.open(copied, "r:") as source, tarfile.open(filtered, "w") as target:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.uid, root.gid, root.mode = (entries["data"][key] for key in ("uid", "gid", "mode"))
        target.addfile(root)
        for member in source:
            name = plan.safe_name(member.name, allow_root=True)
            if name in top and top[name]["kind"] == "file":
                renamed = copy.copy(member)
                renamed.name = Path(name).name
                with source.extractfile(member) as content:
                    target.addfile(renamed, content)
    os.chmod(filtered, 0o600)
    expected = plan.archive_inventory(filtered)
    for name, entry in expected["entries"].items():
        require(entry == entries["data" if not name else "data/" + name], "Nextcloud cache input differs from original bytes/metadata")
    copied.unlink()
    owner.create_volume("nextcloud-runtime-cache")
    helper_image = checked["manifest"]["projects"][capture.NC_PROJECT]["containers"]["db"]["image_id"]
    helper = owner.create_container("nextcloud-runtime-extract", helper_image, "nextcloud-runtime-cache", drill.DATA,
                                    "tar", ["-C", drill.DATA, "-xpf", "-"])
    owner.execute(helper, input_file=filtered)
    audit_volume(owner, "nextcloud-runtime-cache", helper_image, evidence / "nextcloud-cache-before-start.tar", expected)
    filtered.unlink()
    mounts = [volume("nextcloud-runtime-cache", "/var/www/html/data", False)]
    for name in sorted(top):
        mounts.append(volume("nextcloud-nextcloud-html", "/var/www/html/" + name, subpath=name))
    return mounts, expected, {Path(name).name for name, entry in top.items() if entry["kind"] == "directory"}, helper_image


def audit_nextcloud_data_cache(owner, expected, directory_names, image, evidence):
    path = evidence / "nextcloud-cache-after-start.tar"
    helper = owner.create_container("nextcloud-runtime-final-audit", image, "nextcloud-runtime-cache", drill.DATA,
                                    "tar", ["-C", drill.DATA, "-cf", "-", "."], readonly_volume=True)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        owner.execute(helper, output_file=stream)
    actual = plan.archive_inventory(path)
    before, after = drill.inventory_identity(expected), drill.inventory_identity(actual)
    require(set(before).issubset(after) and all(after[name] == entry for name, entry in before.items()),
            "Nextcloud startup overwrote original data-root file bytes/owners/modes/links")
    extras = {name: entry for name, entry in actual["entries"].items() if name not in before}
    # Every old subtree is a RO submount. The backing-volume directories are
    # only empty Docker mount points, not restored originals or writable homes.
    require(all(name in directory_names and entry["kind"] == "directory" for name, entry in extras.items()),
            "Nextcloud runtime cache has unexpected new files or subtrees")
    drill.private_json(evidence / "nextcloud-runtime-cache-inventory.json", actual)
    path.unlink()
    return {"original_flat_file_count": len(before) - 1, "original_flat_files_bytes_uid_gid_mode_links_unchanged": True,
            "readonly_original_subdirectory_count": len(directory_names), "cache_mountpoint_directory_count": len(extras),
            "new_temporary_files_at_validation": 0, "runtime_cache_inventory_sha256": digest(after)}


def application_validation(owner, checked, checkpoint, evidence):
    owner.evidence, owner.checkpoint = evidence, checkpoint
    staged = {}
    for archive, code, roots in [("nextcloud-app-code.tar", True, {"apps/integration_weknora"}),
                                  ("runtime-inputs.tar", False, set(checked["inputs"]))]:
        copied = evidence / (archive + ".staged")
        drill.staged_archive(checkpoint / archive, copied, checked["manifest"]["artifact_sha256"][archive])
        inventory = plan.archive_inventory(copied, allowed_roots=roots)
        root = evidence / ("app-code" if code else "runtime-inputs")
        if root.exists():
            verify_staging(root, inventory, code=code)
        else:
            extract_private(copied, root, inventory, code=code)
        owner.staged_roots.append((root, inventory, code))
        copied.unlink()
        staged[archive] = root
    runtime, app_code = staged["runtime-inputs.tar"], staged["nextcloud-app-code.tar"]
    original_paths = {path: runtime / arc for arc, path in checked["inputs"].items()}
    if owner.network is None:
        owner.create_network()
    hosts = {role: owner.prefix + "-" + role for role in ["nextcloud-postgres", "weknora-postgres", "nextcloud-redis", "weknora-redis"]}
    database_report, identities_before = {}, {}
    def image(project, role):
        return checked["manifest"]["projects"][project]["containers"][role]["image_id"]
    def sql(short, query):
        env = checked["configs"][capture.NC_PROJECT if short == "nextcloud" else capture.WK_PROJECT]["services"]["db" if short == "nextcloud" else "postgres"]["environment"]
        return owner.exec(short + "-postgres", ["psql", "-h", "/tmp", "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"], "-v", "ON_ERROR_STOP=1", "-Atc", query])
    identity_queries = {
        "nextcloud": "SELECT row_to_json(t) FROM (SELECT appid,configkey,configvalue FROM oc_appconfig WHERE appid='integration_weknora' ORDER BY appid,configkey) t; SELECT row_to_json(t) FROM (SELECT * FROM oc_weknora_binding_id ORDER BY binding_id) t; SELECT row_to_json(t) FROM (SELECT * FROM oc_weknora_pub_state ORDER BY binding_id,file_id) t;",
        "weknora": "SELECT row_to_json(t) FROM (SELECT * FROM schema_migrations ORDER BY version) t; SELECT row_to_json(t) FROM (SELECT * FROM data_sources ORDER BY id) t; SELECT row_to_json(t) FROM (SELECT * FROM nextcloud_source_versions ORDER BY tenant_id,knowledge_base_id,datasource_id,external_id) t;"}
    for project in plan.PROJECTS:
        short = plan.SHORT[project]
        cfg = checked["configs"][project]
        service = "db" if short == "nextcloud" else "postgres"
        env = cfg["services"][service]["environment"]
        root = checked["archives"][short + "-postgres-data.tar"]["entries"][""]
        require(not ({"standby.signal", "recovery.signal"} & set(checked["archives"][short + "-postgres-data.tar"]["entries"])), "external PostgreSQL recovery is unsupported")
        if short + "-postgres" in owner.containers:
            cid = owner.containers[short + "-postgres"]["id"]
        else:
            cid = owner.create_runtime(short + "-postgres", image(project, service), "postgres",
                 ["-D", drill.DATA, "-c", "listen_addresses=*", "-c", "unix_socket_directories=/tmp", "-c", "default_transaction_read_only=on",
                  "-c", "archive_mode=off", "-c", "archive_command=", "-c", "restore_command=", "-c", "primary_conninfo=", "-c", "primary_slot_name=",
                  "-c", "max_wal_senders=0", "-c", "shared_preload_libraries=", "-c", "autovacuum=off"],
                  [volume(short + "-postgres-data", drill.DATA, False)], user=f"{root['uid']}:{root['gid']}", memory="512m")
        owner.start_database(cid)
        owner.ready(short + "-postgres", ["pg_isready", "-h", "/tmp", "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"]])
        require(sql(short, "SHOW default_transaction_read_only;").strip() == b"on", "PostgreSQL write fence is absent")
        identities_before[short] = hashlib.sha256(sql(short, identity_queries[short])).hexdigest()
        query = "SELECT json_build_object('server_version',current_setting('server_version'),'integration_version',(SELECT configvalue FROM oc_appconfig WHERE appid='integration_weknora' AND configkey='installed_version'),'bindings',(SELECT count(*) FROM oc_weknora_binding_id),'publication_states',(SELECT count(*) FROM oc_weknora_pub_state));" if short == "nextcloud" else "SELECT json_build_object('server_version',current_setting('server_version'),'schema_version',(SELECT version FROM schema_migrations),'schema_dirty',(SELECT dirty FROM schema_migrations),'data_sources',(SELECT count(*) FROM data_sources));"
        database_report[short + "_postgres"] = json.loads(sql(short, query))
        require(short == "nextcloud" or database_report[short + "_postgres"]["schema_dirty"] is False, "restored schema is dirty")
        root = checked["archives"][short + "-redis-data.tar"]["entries"][""]
        if short + "-redis" in owner.containers:
            cid = owner.containers[short + "-redis"]["id"]
        else:
            cid = owner.create_runtime(short + "-redis", image(project, "redis"), "redis-server",
                  ["--dir", "/data", "--appendonly", "yes", "--port", "6379", "--protected-mode", "no", "--save", ""],
                  [volume(short + "-redis-data", "/data", False)], user=f"{root['uid']}:{root['gid']}", memory="512m")
        owner.start_database(cid)
        owner.ready(short + "-redis", ["redis-cli", "PING"], lambda v: v.strip() == b"PONG")
        count = int(owner.exec(short + "-redis", ["redis-cli", "DBSIZE"]).strip())
        # EVAL and writes are denied even when Asynq goroutines register. No
        # pending task can be claimed or its payload processed under this ACL.
        if b"NOPERM" not in owner.exec(short + "-redis", ["redis-cli", "EVAL", "return 1", "0"]):
            owner.exec(short + "-redis", ["redis-cli", "ACL", "SETUSER", "default", "reset", "on", "nopass", "~*", "+@read", "+ping", "+info", "+select"])
        refusal = owner.exec(short + "-redis", ["redis-cli", "EVAL", "return 1", "0"])
        require(b"NOPERM" in refusal, "Redis queue execution fence is absent")
        refusal = owner.exec(short + "-redis", ["redis-cli", "SET", "recovery-write-proof", "blocked"])
        require(b"NOPERM" in refusal, "Redis write fence is absent")
        database_report[short + "_redis"] = {"db0_key_count": count, "eval_denied": True, "set_denied": True}
    verify_ldap(owner, checked, original_paths, evidence)
    nc_cfg = checked["configs"][capture.NC_PROJECT]["services"]["nextcloud"]
    # Separate public overlay carries only fence controls; original config.php,
    # instance ID and encryption keys are kept on the read-only restored HTML.
    overlay = owner.control(evidence / "recovery.config.php", ("<?php\n$CONFIG = array ('maintenance' => true, 'config_is_read_only' => true, 'dbhost' => '" + hosts["nextcloud-postgres"] + "', 'redis' => array('host' => '" + hosts["nextcloud-redis"] + "','port' => 6379), 'logfile' => '/tmp/nextcloud-recovery.log', 'upgrade.disable-web' => true);\n").encode(), public=True)
    require("config/upgrade-disable-web.config.php" in checked["archives"]["nextcloud-nextcloud-html.tar"]["entries"], "read-only HTML lacks the existing recovery-overlay mount target")
    nc_cache_mounts, nc_cache_expected, nc_directory_names, nc_helper_image = prepare_nextcloud_data_cache(owner, checked, evidence)
    nc_mounts = [volume("nextcloud-nextcloud-html", "/var/www/html"), bind(app_code / "apps/integration_weknora", "/var/www/html/custom_apps/integration_weknora"), bind(overlay, "/var/www/html/config/upgrade-disable-web.config.php")] + nc_cache_mounts
    nc_env = fenced_environment(nc_cfg["environment"], "nextcloud", hosts)
    nc_tmp = {"/tmp": "rw,nosuid,size=67108864,mode=1777", "/var/run/apache2": "rw,nosuid,size=16777216", "/var/lock/apache2": "rw,nosuid,size=16777216"}
    cid = owner.create_runtime("nextcloud-app", image(capture.NC_PROJECT, "nextcloud"), "apache2-foreground", [], nc_mounts, environment=nc_env,
               caps=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "NET_BIND_SERVICE"], tmpfs=nc_tmp)
    owner.start_database(cid)
    status = json.loads(owner.ready("nextcloud-app", ["php", "occ", "status", "--output=json"], lambda v: b'"installed":true' in v.replace(b" ", b""), user="33:33"))
    require(status.get("installed") is True and status.get("maintenance") is True and status.get("needsDbUpgrade") is False, "restored Nextcloud is not installed, maintained or schema compatible")
    nc_identity = owner.exec("nextcloud-app", ["php", "-r", "require '/var/www/html/config/config.php'; echo hash('sha256', json_encode([$CONFIG['instanceid'],$CONFIG['secret'],$CONFIG['passwordsalt']]));"])
    require(len(nc_identity.strip()) == 64, "restored Nextcloud identity is unavailable")
    restored_app_version = owner.exec("nextcloud-app", ["php", "occ", "config:app:get", "integration_weknora", "installed_version"], user="33:33").decode().strip()
    require(restored_app_version == database_report["nextcloud_postgres"]["integration_version"], "Nextcloud application cannot read its restored integration state")
    # Only public readiness metadata is requested, through container loopback.
    nc_http = owner.ready("nextcloud-app", ["php", "-r", "$v=json_decode(file_get_contents('http://127.0.0.1/status.php'),true); if(!$v||!$v['maintenance'])exit(1); echo json_encode($v);"])
    require(json.loads(nc_http)["maintenance"] is True, "Nextcloud HTTP maintenance fence is absent")
    wk_cfg = checked["configs"][capture.WK_PROJECT]["services"]["app"]
    wk_mounts = [volume("weknora-app-data", "/data/files"), volume("weknora-docreader-tmp", "/tmp/docreader")]
    for mount in wk_cfg["volumes"]:
        if mount["type"] == "bind":
            wk_mounts.append(bind(staged_bind_path(mount["source"], original_paths), mount["target"]))
    wk_env = fenced_environment(wk_cfg["environment"], "weknora", hosts)
    cid = owner.create_runtime("weknora-app", image(capture.WK_PROJECT, "app"), "/app/WeKnora", [], wk_mounts,
               environment=wk_env, caps=["DAC_OVERRIDE"])
    owner.start_database(cid)
    health_reply = owner.ready("weknora-app", ["curl", "--fail", "--silent", "http://127.0.0.1:8080/health"], lambda v: json.loads(v).get("status") == "ok")
    auth_reply = owner.ready("weknora-app", ["curl", "--fail", "--silent", "http://127.0.0.1:8080/api/v1/auth/config"], lambda v: bool(json.loads(v)))
    auth = json.loads(auth_reply)
    require(isinstance(auth, dict) and auth.get("success") is True, "restored WeKnora auth configuration is not healthy")
    for short, query in identity_queries.items():
        require(hashlib.sha256(sql(short, query)).hexdigest() == identities_before[short], "application startup changed restored identity, binding or source version state")
    # Read-only transaction and EVAL fences must remain after startup.
    for short in ("nextcloud", "weknora"):
        require(sql(short, "SHOW default_transaction_read_only;").strip() == b"on", "database write fence changed")
        require(b"NOPERM" in owner.exec(short + "-redis", ["redis-cli", "EVAL", "return 1", "0"]), "queue fence changed")
    nc_cache_report = audit_nextcloud_data_cache(owner, nc_cache_expected, nc_directory_names, nc_helper_image, evidence)
    owner.verify()
    return {"mode": "isolated_application_restore_drill", "database_validation": database_report,
            "app_code_and_runtime_inputs_restored": True, "application_runtime_restored": True,
            "ldap_runtime_restored": True, "nextcloud_status": status,
            "nextcloud_instance_and_keys_sha256": nc_identity.decode().strip(),
            "nextcloud_http_maintenance_verified": True, "nextcloud_restored_database_app_version_read_verified": True, "weknora_readonly_auth_entry_verified": True, "weknora_health_verified": True,
            "ldap_admin_bind_verified": True, "identity_binding_source_versions_unchanged": True,
            "metadata_identity_sha256": identities_before,
            "restored_application_and_original_ldap_data_readonly": True,
            "ldap_original_data_readonly": True, "ldap_runtime_cache_writable": True,
            "ldap_identity_data_unchanged": True,
            "nextcloud_original_data_readonly": True, "nextcloud_runtime_cache_writable": True,
            "nextcloud_restored_data_subtrees_and_flat_files_readonly": True, "nextcloud_runtime_cache_validation": nc_cache_report,
            "ordinary_worker_registration": "present_in_captured_weknora_process",
            "ordinary_worker_execution": "fenced_by_postgresql_readonly_and_redis_acl_no_eval_or_writes",
            "runtime_overlay": {"source_artifacts_replaced": False, "model_token_and_proxies_removed": True,
                                "auto_migrate": False, "nextcloud_maintenance": True, "nextcloud_config_is_read_only": True,
                                "nextcloud_mutable_cache": "data root only; every original file and subtree is mounted read-only",
                                "ldap_mutable_cache": "verified disposable execution copy; data.mdb/config unchanged after startup and bind"},
            "paid_model_requests_performed": False, "enterprise_document_read_entry_invoked": False,
            "public_ingress_started": False, "external_replay_verified": False,
            "ingress_reopen_permitted": False, "full_recovery_acceptance": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--nextcloud-dir", type=Path, default=plan.ROOT)
    parser.add_argument("--weknora-dir", type=Path, default=plan.ROOT.parent / "weknora-ldap-local")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        checked = plan.verify_checkpoint(args.checkpoint_dir, args.nextcloud_dir, args.weknora_dir, args.expected_manifest_sha256)
        if not args.apply:
            result = drill.drill_plan(checked)
            result.update(mode="read_only_application_restore_plan", captured_workers_require_runtime_fence=True)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        require(args.evidence_dir is not None, "apply requires a new absolute private evidence directory")
        capture.local_docker(args.nextcloud_dir)
        result = drill.apply_drill(checked, args.checkpoint_dir, args.nextcloud_dir, args.evidence_dir,
                                   owner_type=ApplicationDrill, runtime_validator=application_validation)
        print(json.dumps({key: result[key] for key in ["status", "cleanup_verified", "application_runtime_restored", "ldap_runtime_restored", "external_replay_verified", "ingress_reopen_permitted"]}, sort_keys=True))
        return 0
    except (drill.DrillError, capture.CheckpointError, plan.RestorePlanError, OSError, ValueError, TypeError, KeyError):
        print("Isolated application restore refused; inspect private evidence if present", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
