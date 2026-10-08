#!/usr/bin/env python3
"""Cold, matched checkpoint of the two explicitly named local LAN stacks.

The default invocation is a read-only plan. --apply plus --confirm-pair is
required before any service is stopped. A failed or interrupted capture leaves
an INCOMPLETE marker and the services stopped; this helper never starts a
service, restores a volume, removes Docker state, or switches an image.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import time


ROOT = Path(__file__).resolve().parents[2]
NC_PROJECT = "nextcloud-weknora-dev"
WK_PROJECT = "weknora-ldap-local"
PAIR_CONFIRMATION = NC_PROJECT + "/" + WK_PROJECT
NC_SERVICES = frozenset(("db", "redis", "nextcloud", "cron", "event-worker",
                         "event-status-worker", "mock-embedding", "nextcloud-https"))
WK_SERVICES = frozenset(("postgres", "redis", "docreader", "openldap", "app",
                         "frontend", "lan-gateway"))
NC_VOLUMES = frozenset(("postgres-data", "redis-data", "nextcloud-html"))
WK_VOLUMES = frozenset(("postgres-data", "redis-data", "app-data",
                        "docreader-tmp", "ldap-data"))
DB_REDIS = {NC_PROJECT: frozenset(("db", "redis")),
            WK_PROJECT: frozenset(("postgres", "redis"))}
MIN_FREE_BYTES = 2 * 1024 ** 3


def recovery_journal_module():
    source = Path(__file__).with_name('publication-recovery-journal.py')
    spec = importlib.util.spec_from_file_location('publication_recovery_journal', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckpointError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckpointError(message)


def run(args: list[str], *, cwd: Path, input_file: Path | None = None,
        output_file=None, timeout: int = 120) -> bytes:
    """Never expose command stderr: Compose configuration can contain secrets."""
    try:
        with (input_file.open("rb") if input_file else open(os.devnull, "rb")) as source:
            result = subprocess.run(args, cwd=cwd, stdin=source,
                                    stdout=output_file or subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout,
                                    check=False, env=safe_environment())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckpointError(f"{args[0]} could not complete") from error
    require(result.returncode == 0,
            f"{args[0]} failed (exit {result.returncode}); inspect the private checkpoint")
    return result.stdout if output_file is None else b""


def safe_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("COMPOSE_"):
            env.pop(key)
    return env


def load_json(args: list[str], *, cwd: Path) -> dict:
    try:
        return json.loads(run(args, cwd=cwd))
    except (ValueError, TypeError) as error:
        raise CheckpointError(f"{args[0]} returned invalid JSON") from error


def local_docker(nc_dir: Path) -> None:
    docker_host = os.environ.get("DOCKER_HOST", "")
    require(not docker_host or docker_host.startswith("unix://"),
            "remote Docker daemon is forbidden")
    contexts = load_json(["docker", "context", "inspect"], cwd=nc_dir)
    try:
        host = contexts[0]["Endpoints"]["docker"]["Host"]
    except (KeyError, IndexError, TypeError) as error:
        raise CheckpointError("Docker context endpoint is unavailable") from error
    require(isinstance(host, str) and host.startswith("unix://"),
            "Docker context must use a local Unix socket")


def compose_args(nc_dir: Path, wk_dir: Path, project: str) -> list[str]:
    if project == NC_PROJECT:
        return ["docker", "compose", "--env-file", str(nc_dir / ".env"),
                "-f", str(nc_dir / "compose.yaml"),
                "-f", str(nc_dir / "integration/nextcloud.lan.yaml"),
                "-f", str(nc_dir / "integration/nextcloud.https.yaml"),
                "--profile", "lan-https"]
    require(project == WK_PROJECT, "unknown Compose project")
    return ["docker", "compose", "--project-directory", str(wk_dir),
            "-f", str(wk_dir / "compose.yaml"),
            "-f", str(nc_dir / "integration/weknora.override.yaml"),
            "-f", str(nc_dir / "integration/weknora-rag-local.override.yaml")]


def is_plain_path(path: Path) -> bool:
    return path.exists() and not path.is_symlink() and (path.is_file() or path.is_dir())


def git_identity(nc_dir: Path) -> str:
    top = run(["git", "rev-parse", "--show-toplevel"], cwd=nc_dir).decode().strip()
    require(Path(top).resolve() == nc_dir, "Nextcloud directory is not its Git checkout root")
    status = run(["git", "status", "--porcelain=v1", "--untracked-files=all"],
                 cwd=nc_dir)
    require(not status, "Nextcloud Git checkout must be clean, including untracked files")
    head = run(["git", "rev-parse", "HEAD"], cwd=nc_dir).decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", head) is not None,
            "Nextcloud Git commit is invalid")
    return head


def validate_config(config: dict, project: str, nc_dir: Path, wk_dir: Path) -> None:
    services = NC_SERVICES if project == NC_PROJECT else WK_SERVICES
    volumes = NC_VOLUMES if project == NC_PROJECT else WK_VOLUMES
    require(config.get("name") == project and
            set(config.get("services", {})) == services and
            set(config.get("volumes", {})) == volumes,
            f"{project} Compose topology changed; update this helper first")
    for role in volumes:
        require(config["volumes"][role].get("name") == f"{project}_{role}",
                f"{project} volume name changed: {role}")
    for service, definition in config["services"].items():
        require(isinstance(definition.get("image"), str) and definition["image"],
                f"{project}/{service} has no pinned runtime image")
        for mount in definition.get("volumes", []):
            kind, source = mount.get("type"), mount.get("source")
            require(kind in {"bind", "volume"} and isinstance(source, str),
                    f"{project}/{service} has an unsupported mount")
            if kind == "volume":
                require(source in volumes, f"{project}/{service} has an unknown volume")
            else:
                path = Path(source)
                require(path.is_absolute() and is_plain_path(path),
                        f"{project}/{service} bind source is missing or a symlink")
                require(any(path == root or root in path.parents for root in
                            (nc_dir, wk_dir, nc_dir.parent / "WeKnora-ldap-ad")),
                        f"{project}/{service} bind source is outside approved roots")
    for service in DB_REDIS[project]:
        require(not config["services"][service].get("ports"),
                f"{project}/{service} cannot publish a host port during a checkpoint")
    redis_command = config["services"]["redis"].get("command") or []
    require(isinstance(redis_command, list) and
            any(redis_command[i:i + 2] == ["--appendonly", "yes"]
                for i in range(len(redis_command) - 1)),
            f"{project} Redis must persist its AOF before the cold archive")
    nc_app = (nc_dir / "apps/integration_weknora").resolve()
    if project == NC_PROJECT:
        for service in ("nextcloud", "cron", "event-worker", "event-status-worker"):
            mounts = config["services"][service].get("volumes", [])
            require(any(m.get("type") == "bind" and
                        Path(m.get("source", "")).resolve() == nc_app and
                        m.get("target") == "/var/www/html/custom_apps/integration_weknora"
                        for m in mounts), "Nextcloud app code bind mount changed")
    else:
        app_mounts = config["services"]["app"].get("volumes", [])
        require(any(m.get("type") == "volume" and m.get("source") == "app-data" and
                    m.get("target") == "/data/files" for m in app_mounts),
                "WeKnora app-data mount changed")


def inspect(kind: str, name: str, nc_dir: Path) -> dict:
    data = load_json(["docker", kind, "inspect", name], cwd=nc_dir)
    require(isinstance(data, list) and len(data) == 1,
            f"Docker {kind} inspect returned an unexpected count")
    return data[0]


def known_stopped_port_loss(expected: set, actual: set) -> bool:
    """Docker Desktop can clear a fixed LAN address while stopped.

    Accept only one fixed private-LAN binding, or an additional same-port LAN
    binding beside a surviving loopback binding. The empty loopback placeholder
    cannot replace a requested loopback/public/wildcard binding. The caller
    freezes the verified running bindings, identity and Compose hash.
    """
    missing, extra = expected - actual, actual - expected
    if not missing or len(expected) != len(actual) or len(missing) != len(extra):
        return False
    for target, address, published in extra:
        if published != "" or address != "127.0.0.1":
            return False
        candidates = set()
        for t, host, port in missing:
            try:
                requested = ipaddress.IPv4Address(host)
            except ipaddress.AddressValueError:
                continue
            private_lan = any(requested in ipaddress.IPv4Network(network) for network in
                              ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
            if (t == target and private_lan and port.isdecimal() and
                    1 <= int(port) <= 65535 and
                    (len(expected) == 1 or (target, address, port) in expected & actual)):
                candidates.add((t, host, port))
        if len(candidates) != 1:
            return False
        missing -= candidates
    return not missing


def container_runtime_matches(item: dict, service: dict, config: dict, *,
                              allow_stopped_port_loss: bool = False) -> bool:
    """Compare the mounted checkout and every runtime input to resolved Compose."""
    expected_mounts = set()
    for mount in service.get("volumes", []):
        kind = mount["type"]
        source = (config["volumes"][mount["source"]]["name"] if kind == "volume"
                  else mount["source"])
        expected_mounts.add((mount["target"], kind, source,
                             bool(mount.get("read_only", False))))
    actual_mounts = set()
    for mount in item.get("Mounts", []):
        kind = mount.get("Type")
        source = mount.get("Name") if kind == "volume" else mount.get("Source")
        if kind == "bind" and isinstance(source, str) and source.startswith("/host_mnt/"):
            # Docker Desktop exposes the same macOS file beneath this VM prefix.
            source = source[len("/host_mnt"):]
        actual_mounts.add((mount.get("Destination"), kind, source,
                           not mount.get("RW", True)))
    if expected_mounts != actual_mounts:
        return False

    expected_ports = set()
    for port in service.get("ports", []):
        expected_ports.add((f"{port['target']}/{port.get('protocol', 'tcp')}",
                            str(port.get("host_ip", "0.0.0.0")),
                            str(port["published"])))
    actual_ports = {(target, binding["HostIp"], binding["HostPort"])
                    for target, mappings in
                    (item.get("HostConfig", {}).get("PortBindings") or {}).items()
                    for binding in mappings or []}
    actual_count = sum(len(mappings or []) for mappings in
                       (item.get("HostConfig", {}).get("PortBindings") or {}).values())
    if (len(expected_ports) != len(service.get("ports", [])) or
            len(actual_ports) != actual_count):
        return False
    if expected_ports != actual_ports:
        if (not allow_stopped_port_loss or item.get("State", {}).get("Running") is not False or
                not known_stopped_port_loss(expected_ports, actual_ports)):
            return False

    expected_env = service.get("environment") or {}
    actual_env = dict(line.split("=", 1) for line in
                      item.get("Config", {}).get("Env") or [] if "=" in line)
    if not all(actual_env.get(key) == value for key, value in expected_env.items()):
        return False

    if item.get("State", {}).get("Running"):
        expected_networks = {config["networks"][name]["name"]
                             for name in service.get("networks", {})}
        actual_networks = set((item.get("NetworkSettings", {}).get("Networks") or {}))
        if expected_networks != actual_networks:
            return False
    return True


def inventory_stack(nc_dir: Path, wk_dir: Path, project: str) -> dict:
    cwd = nc_dir if project == NC_PROJECT else wk_dir
    command = compose_args(nc_dir, wk_dir, project)
    config_bytes = run(command + ["config", "--format", "json"], cwd=cwd)
    try:
        config = json.loads(config_bytes)
    except ValueError as error:
        raise CheckpointError(f"{project} Compose configuration is invalid") from error
    validate_config(config, project, nc_dir, wk_dir)
    expected_services = NC_SERVICES if project == NC_PROJECT else WK_SERVICES
    expected_volumes = NC_VOLUMES if project == NC_PROJECT else WK_VOLUMES
    hashes = {}
    for line in run(command + ["config", "--hash", "*"], cwd=cwd).decode().splitlines():
        fields = line.split()
        require(len(fields) == 2 and fields[0] not in hashes and
                re.fullmatch(r"[0-9a-f]{64}", fields[1]) is not None,
                f"{project} Compose hash output is invalid")
        hashes[fields[0]] = fields[1]
    require(set(hashes) == expected_services, f"{project} Compose hash scopes changed")
    ids = set(run(["docker", "ps", "-aq", "--no-trunc", "--filter",
                   f"label=com.docker.compose.project={project}"],
                  cwd=nc_dir).decode().split())
    containers = {}
    for service in sorted(expected_services):
        name = f"{project}-{service}-1"
        item = inspect("container", name, nc_dir)
        labels = item.get("Config", {}).get("Labels") or {}
        require(labels.get("com.docker.compose.project") == project and
                labels.get("com.docker.compose.service") == service and
                item.get("Id") in ids and item.get("State", {}).get("Running") is True,
                f"{project}/{service} is absent, stopped, or not owned by Compose")
        require(item.get("Config", {}).get("Image") == config["services"][service]["image"],
                f"{project}/{service} running image tag differs from Compose")
        require(container_runtime_matches(item, config["services"][service], config),
                f"{project}/{service} mounts, ports, environment or networks differ from Compose")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("Image", "")) is not None,
                f"{project}/{service} running image ID is invalid")
        compose_hash = labels.get("com.docker.compose.config-hash", "")
        require(compose_hash == hashes[service],
                f"{project}/{service} creation-time Compose hash differs from configuration")
        containers[service] = {"id": item["Id"], "image_id": item["Image"],
                               "image_tag": item["Config"]["Image"],
                               "compose_config_hash": compose_hash,
                               "running_port_bindings": item.get("HostConfig", {}).get("PortBindings") or {}}
    require(ids == {item["id"] for item in containers.values()},
            f"{project} has an unrecognized Compose container")
    volume_info = {}
    for role in sorted(expected_volumes):
        name = f"{project}_{role}"
        item = inspect("volume", name, nc_dir)
        labels = item.get("Labels") or {}
        require(item.get("Name") == name and
                labels.get("com.docker.compose.project") == project and
                labels.get("com.docker.compose.volume") == role,
                f"{project}/{role} is not the expected Compose volume")
        volume_info[role] = name
    return {"project": project, "cwd": cwd, "command": command,
            "config_bytes": config_bytes, "config": config,
            "containers": containers, "volumes": volume_info}


def reject_foreign_volume_users(stacks: list[dict], nc_dir: Path) -> None:
    owned = {item["id"] for stack in stacks for item in stack["containers"].values()}
    names = {name for stack in stacks for name in stack["volumes"].values()}
    networks = {network["name"] for stack in stacks
                for network in stack["config"].get("networks", {}).values()}
    other_ids = set(run(["docker", "ps", "-q", "--no-trunc"],
                        cwd=nc_dir).decode().split()) - owned
    for container_id in other_ids:
        item = inspect("container", container_id, nc_dir)
        for mount in item.get("Mounts", []):
            require(not (mount.get("Type") == "volume" and mount.get("Name") in names),
                    "another running container mounts a checkpoint volume")
        require(not (set((item.get("NetworkSettings", {}).get("Networks") or {})) &
                     networks),
                "another running container joins a checkpoint network")


def database_has_no_other_clients(container: str, user: str, database: str,
                                  nc_dir: Path) -> None:
    query = ("SELECT count(*) FROM pg_stat_activity "
             "WHERE backend_type='client backend' AND pid<>pg_backend_pid()")
    count = run(["docker", "exec", container, "psql", "-U", user, "-d", database,
                 "-Atq", "-c", query], cwd=nc_dir, timeout=30).decode().strip()
    require(count == "0", "PostgreSQL still has an external client after app shutdown")


def input_paths(stacks: list[dict], nc_dir: Path, wk_dir: Path) -> dict[str, Path]:
    app_code = nc_dir / "apps/integration_weknora"
    paths: set[Path] = set()
    for stack in stacks:
        for service in stack["config"]["services"].values():
            for mount in service.get("volumes", []):
                if mount.get("type") == "bind":
                    paths.add(Path(mount["source"]))
    paths.update((nc_dir / ".env", nc_dir / "compose.yaml",
                  nc_dir / "integration/nextcloud.lan.yaml",
                  nc_dir / "integration/nextcloud.https.yaml",
                  nc_dir / "integration/weknora.override.yaml",
                  nc_dir / "integration/weknora-rag-local.override.yaml",
                  wk_dir / ".env", wk_dir / "compose.yaml",
                  wk_dir / "rag-model.env"))
    optional_token = wk_dir / "rag-model-token"
    if optional_token.exists():
        paths.add(optional_token)
    paths.discard(app_code)
    for path in paths:
        require(is_plain_path(path), "a runtime input is missing or a symlink")
    roots = ((nc_dir, "nextcloud"), (wk_dir, "weknora-local"),
             (nc_dir.parent / "WeKnora-ldap-ad", "weknora-source"))
    mapping = {}
    for path in sorted(paths):
        for root, prefix in roots:
            if path == root or root in path.parents:
                mapping[f"{prefix}/{path.relative_to(root)}"] = path
                break
        else:
            raise CheckpointError("runtime input is outside approved roots")
    # A mounted directory already contains its mounted child files.
    return {arc: path for arc, path in mapping.items()
            if not any(parent != path and parent in path.parents for parent in paths)}


def source_fingerprint(paths: dict[str, Path]) -> str:
    """Detect a host-side edit to a bind input while services are frozen."""
    digest = hashlib.sha256()
    for arc, source in sorted(paths.items()):
        members = [source]
        if source.is_dir():
            members.extend(sorted(source.rglob("*")))
        for member in members:
            relative = member.relative_to(source) if member != source else Path(".")
            descriptor = f"{arc}/{relative}".encode()
            metadata = member.lstat()
            digest.update(len(descriptor).to_bytes(8, "big"))
            digest.update(descriptor)
            digest.update(str(stat.S_IFMT(metadata.st_mode)).encode())
            digest.update(str(stat.S_IMODE(metadata.st_mode)).encode())
            digest.update(str(metadata.st_size).encode())
            if member.is_symlink():
                digest.update(os.readlink(member).encode())
            elif member.is_file():
                with member.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
            else:
                require(member.is_dir(), "unsupported runtime input file type")
    return digest.hexdigest()


def ensure_state(stack: dict, nc_dir: Path, *, stopped: set[str]) -> None:
    for service, original in stack["containers"].items():
        item = inspect("container", original["id"], nc_dir)
        require(item.get("Id") == original["id"] and
                item.get("Image") == original["image_id"] and
                item.get("State", {}).get("Running") is (service not in stopped),
                f"{stack['project']}/{service} changed state during checkpoint")
        require((item.get("Config", {}).get("Labels") or {}).get(
                    "com.docker.compose.config-hash") == original["compose_config_hash"],
                f"{stack['project']}/{service} creation-time Compose hash changed")
        require(container_runtime_matches(item, stack["config"]["services"][service],
                                          stack["config"],
                                          allow_stopped_port_loss=service in stopped),
                f"{stack['project']}/{service} runtime inputs changed during checkpoint")


def private_file(path: Path, *, data: bytes | None = None, command: list[str] | None = None,
                 cwd: Path | None = None, input_file: Path | None = None,
                 timeout: int = 600) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        if data is not None:
            output.write(data)
        else:
            require(command is not None and cwd is not None, "missing archive command")
            run(command, cwd=cwd, input_file=input_file, output_file=output,
                timeout=timeout)
        output.flush()
        os.fsync(output.fileno())
    require(path.stat().st_size > 0 and (path.stat().st_mode & 0o777) == 0o600,
            "private checkpoint file is empty or has unsafe permissions")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_tar(path: Path) -> None:
    members = 0
    try:
        with tarfile.open(path, "r:") as archive:
            for entry in archive:
                name = Path(entry.name)
                require(not name.is_absolute() and ".." not in name.parts,
                        "archive contains an unsafe path")
                members += 1
    except (OSError, tarfile.TarError) as error:
        raise CheckpointError("checkpoint tar archive could not be read") from error
    require(members > 0, "checkpoint tar archive is empty")


def host_archive(path: Path, sources: dict[str, Path]) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        with tarfile.open(fileobj=output, mode="w", dereference=False) as archive:
            for arc, source in sorted(sources.items()):
                require(is_plain_path(source), "runtime input changed during checkpoint")
                archive.add(source, arcname=arc, recursive=True)
        output.flush()
        os.fsync(output.fileno())
    require((path.stat().st_mode & 0o777) == 0o600, "archive permission is unsafe")
    verify_tar(path)


def free_bytes(path: Path) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def estimate_volume_bytes(stacks: list[dict], nc_dir: Path) -> int:
    """Read-only capacity estimate before the first service is stopped."""
    image = stacks[0]["containers"]["db"]["image_id"]
    inspect("image", image, nc_dir)
    total = 0
    for stack in stacks:
        for name in stack["volumes"].values():
            raw = run(["docker", "run", "--rm", "--pull", "never",
                       "--network", "none", "--mount",
                       f"type=volume,source={name},target=/payload,readonly",
                       "--entrypoint", "du", image, "-sk", "/payload"],
                      cwd=nc_dir, timeout=300).decode().strip()
            match = re.fullmatch(r"([0-9]+)\s+/payload", raw)
            require(match is not None, "volume size estimate is invalid")
            total += int(match.group(1)) * 1024
    return total


def verify_recovery_anchor_source(stack: dict, anchor: dict, nc_dir: Path) -> None:
    sequence = int(anchor['sequence'])
    require(sequence >= 0, 'invalid external anchor sequence')
    # instanceid is a system config value, so read it from the live NC process
    # before stopping applications; do not substitute a database UUID.
    instance = run(['docker', 'exec', '-u', 'www-data', stack['containers']['nextcloud']['id'],
                    'php', 'occ', 'config:system:get', 'instanceid'], cwd=nc_dir).decode().strip()
    query = ("SELECT json_build_object('stream_id',stream_id,'head_sequence',sequence,'prefix_hash'," +
             ("'" + '0' * 64 + "'" if sequence == 0 else
              "(SELECT chain_sha256 FROM oc_weknora_recovery_log WHERE sequence=" + str(sequence) + ")") +
             ") FROM oc_weknora_recovery_head WHERE id=1")
    data = json.loads(run(['docker','exec',stack['containers']['db']['id'],'psql','-X','-U','nextcloud',
                          '-d','nextcloud','-v','ON_ERROR_STOP=1','-At','-c',query],cwd=nc_dir))
    require(instance == anchor['instance_id'] and data['stream_id'] == anchor['stream_id'] and
            sequence <= int(data['head_sequence']) and data['prefix_hash'] == anchor['database_chain_sha256'],
            'external journal anchor does not match this live snapshot source')


def checkpoint(stacks: list[dict], nc_dir: Path, wk_dir: Path,
               evidence: Path, git_head: str, recovery_journal=None) -> dict:
    stages: list[str] = []
    incomplete = evidence / "INCOMPLETE"
    private_file(incomplete, data=b"Checkpoint capture is incomplete; do not restore from it.\n")
    try:
        inputs = input_paths(stacks, nc_dir, wk_dir)
        all_bind_inputs = {**inputs,
                           "nextcloud-app-code": nc_dir / "apps/integration_weknora"}
        before_inputs = source_fingerprint(all_bind_inputs)
        estimated = estimate_volume_bytes(stacks, nc_dir)
        required = int(estimated * 1.25) + MIN_FREE_BYTES
        require(free_bytes(evidence) >= required,
                "checkpoint destination lacks estimated volume space plus 2 GiB reserve")
        for stack in stacks:
            ensure_state(stack, nc_dir, stopped=set())
            require(run(stack["command"] + ["config", "--format", "json"],
                        cwd=stack["cwd"]) == stack["config_bytes"],
                    f"{stack['project']} Compose configuration changed before shutdown")
        require(git_identity(nc_dir) == git_head and
                source_fingerprint(all_bind_inputs) == before_inputs,
                "runtime inputs changed before shutdown")
        recovery_anchor = None
        if recovery_journal is not None:
            path, key, pin = recovery_journal
            recovery_anchor = recovery_journal_module().checkpoint_anchor(path, key, pin,
                [nc_dir, wk_dir, evidence, nc_dir.parent / 'WeKnora-ldap-ad', *inputs.values()])
            verify_recovery_anchor_source(stacks[0], recovery_anchor, nc_dir)
        reject_foreign_volume_users(stacks, nc_dir)
        for stack in stacks:
            services = sorted(set(stack["containers"]) - DB_REDIS[stack["project"]])
            run(stack["command"] + ["stop", "-t", "60", *services],
                cwd=stack["cwd"], timeout=240)
        for stack in stacks:
            ensure_state(stack, nc_dir,
                         stopped=set(stack["containers"]) - DB_REDIS[stack["project"]])
        reject_foreign_volume_users(stacks, nc_dir)
        stages.append("both_application_and_ingress_sides_stopped")

        artifacts = {}
        for stack, service, user, database in (
                (stacks[0], "db", "nextcloud", "nextcloud"),
                (stacks[1], "postgres", "weknora", "weknora")):
            container = stack["containers"][service]["id"]
            short = "nextcloud" if stack["project"] == NC_PROJECT else "weknora"
            database_has_no_other_clients(container, user, database, nc_dir)
            dump = evidence / f"{short}.dump"
            private_file(dump, command=["docker", "exec", container, "pg_dump",
                                        "-U", user, "-Fc", "-d", database],
                         cwd=nc_dir, timeout=900)
            run(["docker", "exec", "-i", container, "pg_restore", "--list"],
                cwd=nc_dir, input_file=dump, timeout=120)
            artifacts[dump.name] = sha256(dump)
            globals_path = evidence / f"{short}-globals.sql"
            private_file(globals_path,
                         command=["docker", "exec", container, "pg_dumpall",
                                  "-U", user, "--globals-only"],
                         cwd=nc_dir, timeout=180)
            artifacts[globals_path.name] = sha256(globals_path)
            database_has_no_other_clients(container, user, database, nc_dir)
        stages.append("logical_database_dumps_verified")

        for stack in stacks:
            run(stack["command"] + ["stop", "-t", "60",
                                    *sorted(DB_REDIS[stack["project"]])],
                cwd=stack["cwd"], timeout=240)
        for stack in stacks:
            ensure_state(stack, nc_dir, stopped=set(stack["containers"]))
        stages.append("all_fifteen_services_stopped")

        tar_image = stacks[0]["containers"]["db"]["image_id"]
        for stack in stacks:
            short = "nextcloud" if stack["project"] == NC_PROJECT else "weknora"
            for role, name in sorted(stack["volumes"].items()):
                archive = evidence / f"{short}-{role}.tar"
                private_file(archive, command=["docker", "run", "--rm", "--pull", "never",
                                               "--network", "none", "--mount",
                                               f"type=volume,source={name},target=/payload,readonly",
                                               "--entrypoint", "tar", tar_image,
                                               "-C", "/payload", "-cf", "-", "."],
                             cwd=nc_dir, timeout=1800)
                verify_tar(archive)
                artifacts[archive.name] = sha256(archive)
                require(free_bytes(evidence) >= MIN_FREE_BYTES,
                        "checkpoint destination crossed 2 GiB free-space floor")
        stages.append("all_eight_cold_volumes_archived")

        code_path = evidence / "nextcloud-app-code.tar"
        host_archive(code_path, {"apps/integration_weknora":
                                 nc_dir / "apps/integration_weknora"})
        artifacts[code_path.name] = sha256(code_path)
        inputs_path = evidence / "runtime-inputs.tar"
        host_archive(inputs_path, inputs)
        artifacts[inputs_path.name] = sha256(inputs_path)
        for stack in stacks:
            short = "nextcloud" if stack["project"] == NC_PROJECT else "weknora"
            path = evidence / f"{short}-resolved-compose.json"
            private_file(path, data=stack["config_bytes"])
            artifacts[path.name] = sha256(path)
            current = run(stack["command"] + ["config", "--format", "json"],
                          cwd=stack["cwd"])
            require(current == stack["config_bytes"],
                    f"{stack['project']} Compose configuration changed during checkpoint")
            ensure_state(stack, nc_dir, stopped=set(stack["containers"]))
        require(git_identity(nc_dir) == git_head,
                "Nextcloud checkout changed during checkpoint")
        require(source_fingerprint(all_bind_inputs) == before_inputs,
                "a bind-mounted runtime input changed during checkpoint")
        reject_foreign_volume_users(stacks, nc_dir)
        stages.append("runtime_inputs_and_stopped_state_verified")

        for name, digest in artifacts.items():
            path = evidence / name
            require(path.is_file() and sha256(path) == digest and
                    (path.stat().st_mode & 0o777) == 0o600,
                    "checkpoint artifact failed final digest or permission check")
        manifest = {
            "status": "COMPLETE", "scope": "shared_local_cold_pair",
            "created_at_unix": int(time.time()), "nextcloud_git_commit": git_head,
            "nextcloud_app_code_sha256": artifacts[code_path.name],
            "projects": {stack["project"]: {
                "containers": stack["containers"], "volumes": stack["volumes"],
                "resolved_compose_sha256": hashlib.sha256(stack["config_bytes"]).hexdigest(),
            } for stack in stacks},
            "artifact_sha256": artifacts,
            "all_services_stopped_at_completion": True,
            "restore_or_runtime_switch_performed": False,
        }
        if recovery_anchor is not None:
            path, key, pin = recovery_journal
            current_anchor = recovery_journal_module().checkpoint_anchor(path, key, pin,
                [nc_dir, wk_dir, evidence, nc_dir.parent / 'WeKnora-ldap-ad', *inputs.values()])
            require(current_anchor == recovery_anchor, 'external recovery anchor changed during capture')
            manifest['external_recovery_anchor'] = recovery_anchor
        private_file(evidence / "manifest.json",
                     data=(json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        incomplete.unlink()
        return manifest
    except BaseException:
        # Never restart either stack after an uncertain failure. A process kill
        # also leaves INCOMPLETE in place because it was written first.
        try:
            private_file(evidence / "failure.json", data=(json.dumps({
                "status": "INCOMPLETE", "completed_stages": stages,
                "operator_action": "inspect both stacks; keep them stopped until reviewed",
            }, indent=2) + "\n").encode())
        except (OSError, CheckpointError):
            pass
        raise


def prepare_evidence(path: Path) -> Path:
    require(path.is_absolute() and not path.exists(),
            "evidence directory must be a new absolute path")
    parent = path.parent
    require(parent.is_dir() and not parent.is_symlink(),
            "evidence parent must be an existing real directory")
    path.mkdir(mode=0o700)
    require((path.stat().st_mode & 0o777) == 0o700,
            "evidence directory must be private")
    return path


def safe_evidence_location(path: Path, nc_dir: Path, wk_dir: Path,
                           runtime_inputs: dict[str, Path]) -> None:
    candidate = path.resolve(strict=False)
    nc_dir, wk_dir = nc_dir.resolve(), wk_dir.resolve()
    forbidden = [nc_dir / "apps/integration_weknora", wk_dir,
                 nc_dir.parent / "WeKnora-ldap-ad", *runtime_inputs.values()]
    for source in forbidden:
        source = source.resolve(strict=False)
        require(candidate != source and source not in candidate.parents,
                "evidence cannot be placed inside a runtime input")
    if candidate == nc_dir or nc_dir in candidate.parents:
        backup_root = nc_dir / "dist/backups"
        require(backup_root in candidate.parents,
                "evidence inside Nextcloud checkout must be under ignored dist/backups")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nextcloud-dir", type=Path, default=ROOT)
    parser.add_argument("--weknora-dir", type=Path,
                        default=ROOT.parent / "weknora-ldap-local")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument('--recovery-journal', type=Path)
    parser.add_argument('--recovery-key-file', type=Path)
    parser.add_argument('--recovery-record-sha256')
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-pair", default="",
                        help=f"required with --apply: {PAIR_CONFIRMATION}")
    args = parser.parse_args(argv)
    os.umask(0o077)
    evidence = None
    try:
        nc_dir, wk_dir = args.nextcloud_dir.resolve(), args.weknora_dir.resolve()
        require(nc_dir != wk_dir and nc_dir.is_dir() and wk_dir.is_dir(),
                "both local project directories are required")
        require(not args.nextcloud_dir.is_symlink() and
                not args.weknora_dir.is_symlink(), "project directories cannot be symlinks")
        local_docker(nc_dir)
        git_head = git_identity(nc_dir)
        stacks = [inventory_stack(nc_dir, wk_dir, project)
                  for project in (NC_PROJECT, WK_PROJECT)]
        reject_foreign_volume_users(stacks, nc_dir)
        inputs = input_paths(stacks, nc_dir, wk_dir)
        if not args.apply:
            require(not args.confirm_pair and args.evidence_dir is None,
                    "--confirm-pair and --evidence-dir require --apply")
            print(json.dumps({"mode": "read_only_plan", "pair": PAIR_CONFIRMATION,
                              "nextcloud_git_commit": git_head,
                              "services_to_stop": {s["project"]: sorted(s["containers"])
                                                   for s in stacks},
                              "volumes_to_capture": {s["project"]: sorted(s["volumes"])
                                                     for s in stacks}}, sort_keys=True))
            return 0
        require(args.confirm_pair == PAIR_CONFIRMATION,
                f"--apply requires --confirm-pair {PAIR_CONFIRMATION}")
        require(args.evidence_dir is not None,
                "--apply requires a new absolute --evidence-dir")
        safe_evidence_location(args.evidence_dir, nc_dir, wk_dir, inputs)
        recovery_values = (args.recovery_journal, args.recovery_key_file, args.recovery_record_sha256)
        require(all(value is not None for value in recovery_values) or all(value is None for value in recovery_values),
                'all three external journal options are required together')
        recovery_journal = recovery_values if args.recovery_journal is not None else None
        evidence = prepare_evidence(args.evidence_dir)
        result = checkpoint(stacks, nc_dir, wk_dir, evidence, git_head, recovery_journal)
        print(json.dumps({"status": result["status"], "evidence": str(evidence),
                          "all_services_stopped": True}, sort_keys=True))
        return 0
    except (CheckpointError, OSError, ValueError) as error:
        suffix = f"; private evidence: {evidence}" if evidence else ""
        print(f"Cold checkpoint failed: {error}{suffix}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
