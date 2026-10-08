#!/usr/bin/env python3
"""Verify a matched local cold checkpoint and print a read-only restore plan.

This companion never stops, starts, creates, removes or restores a Docker
resource and never extracts an archive. A successful verification proves only
that the checkpoint and stopped target satisfy this verifier's preconditions;
actual recovery and external publication withdrawal replay remain required.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("shared_cold_capture", HERE / "shared-cold-checkpoint.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
ROOT = capture.ROOT
PROJECTS = (capture.NC_PROJECT, capture.WK_PROJECT)
SHORT = {capture.NC_PROJECT: "nextcloud", capture.WK_PROJECT: "weknora"}
SERVICES = {capture.NC_PROJECT: capture.NC_SERVICES, capture.WK_PROJECT: capture.WK_SERVICES}
VOLUMES = {capture.NC_PROJECT: capture.NC_VOLUMES, capture.WK_PROJECT: capture.WK_VOLUMES}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTAINER = re.compile(r"[0-9a-f]{64}\Z")
MAX_MEMBERS = 2_000_000
MAX_MANIFEST_BYTES = 1024 * 1024


class RestorePlanError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RestorePlanError(message)


def json_object(data: bytes, label: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"{label} contains a duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise RestorePlanError(f"{label} is invalid JSON") from error
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def plain_owned(path: Path, mode: int, *, directory: bool = False) -> None:
    """Check each supplied path component, including parents hidden by resolve()."""
    require(path.is_absolute(), "checkpoint paths must be absolute")
    try:
        for part in (path, *path.parents):
            require(not part.is_symlink(), "checkpoint path contains a symlink")
        info = path.lstat()
    except OSError as error:
        raise RestorePlanError("checkpoint path is unavailable") from error
    wanted = stat.S_ISDIR if directory else stat.S_ISREG
    require(wanted(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == mode,
            "checkpoint path has an unsafe type, owner or private mode")
    if not directory:
        require(info.st_nlink == 1 and info.st_size > 0,
                "checkpoint file is empty or hardlinked")


def expected_artifacts() -> set[str]:
    names = {"nextcloud-app-code.tar", "runtime-inputs.tar"}
    for project in PROJECTS:
        short = SHORT[project]
        names.update((f"{short}.dump", f"{short}-globals.sql",
                      f"{short}-resolved-compose.json"))
        names.update(f"{short}-{role}.tar" for role in VOLUMES[project])
    return names


def safe_name(value: str, *, allow_root: bool = False) -> str:
    require(isinstance(value, str) and value and "\\" not in value and
            "\x00" not in value and not value.startswith("/"),
            "archive contains an unsafe path")
    parts = PurePosixPath(value).parts
    require(".." not in parts, "archive contains an unsafe path")
    cleaned = "/".join(part for part in parts if part not in {"", "."})
    require(bool(cleaned) or allow_root, "archive contains an unsafe root entry")
    return cleaned


def link_destination(name: str, target: str, *, symlink: bool) -> str:
    require(isinstance(target, str) and target and not target.startswith("/") and
            "\\" not in target and "\x00" not in target,
            "archive contains an unsafe link")
    parts = list(PurePosixPath(name).parent.parts) if symlink else []
    parts = [part for part in parts if part != "."]
    for part in PurePosixPath(target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            require(bool(parts), "archive link escapes its root")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def archive_inventory(path: Path, *, allowed_roots: set[str] | None = None) -> dict:
    """Read all payloads; reject path/link pivots, special nodes and duplicates.

    Returned hashes describe file bytes without extracting them. Hardlinks must
    ultimately target a regular archived file; symlinks may safely be dangling.
    """
    entries = {}
    total_bytes = 0
    try:
        with tarfile.open(path, "r:") as archive:
            for member in archive:
                require(len(entries) < MAX_MEMBERS, "archive has too many members")
                name = safe_name(member.name, allow_root=allowed_roots is None)
                require(name not in entries, "archive contains duplicate normalized paths")
                require(member.isdir() or member.isfile() or member.issym() or member.islnk(),
                        "archive contains a special file")
                require(not member.sparse and not member.mode & 0o6000,
                        "archive contains sparse or privileged entries")
                if not name:
                    require(member.isdir(), "archive root must be a directory")
                if allowed_roots is not None:
                    require(any(name == root or name.startswith(root + "/")
                                for root in allowed_roots),
                            "archive contains an unexpected runtime input")
                entry = {"kind": "directory", "mode": member.mode & 0o777,
                         "uid": member.uid, "gid": member.gid, "size": member.size}
                if member.isfile():
                    require(member.size >= 0, "archive file size is invalid")
                    digest = hashlib.sha256()
                    consumed = 0
                    stream = archive.extractfile(member)
                    require(stream is not None, "archive payload is unavailable")
                    with stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            consumed += len(chunk)
                    require(consumed == member.size, "archive payload is truncated")
                    entry.update(kind="file", sha256=digest.hexdigest())
                    total_bytes += consumed
                elif member.issym() or member.islnk():
                    dest = link_destination(name, member.linkname, symlink=member.issym())
                    if allowed_roots is not None:
                        # Each backed-up input is an independent extraction unit.
                        require(any((name == root or name.startswith(root + "/")) and
                                    (dest == root or dest.startswith(root + "/"))
                                    for root in allowed_roots),
                                "archive link crosses a runtime input root")
                    entry.update(kind="symlink" if member.issym() else "hardlink",
                                 target=dest, linkname=member.linkname)
                entries[name] = entry
    except (OSError, tarfile.TarError) as error:
        raise RestorePlanError("checkpoint tar archive could not be read completely") from error
    require(bool(entries), "checkpoint tar archive is empty")
    def actual_symlink_target(name: str, linkname: str) -> str:
        # Resolve archived symlink components before processing '..'. Lexical
        # normalization alone misses 'pivot/../escape' when pivot points to '.'.
        resolved = [part for part in PurePosixPath(name).parent.parts if part != "."]
        pending = list(PurePosixPath(linkname).parts)
        followed = set()
        while pending:
            part = pending.pop(0)
            if part in {"", "."}:
                continue
            if part == "..":
                require(bool(resolved), "archive symlink chain escapes its root")
                resolved.pop()
                continue
            resolved.append(part)
            candidate = "/".join(resolved)
            item = entries.get(candidate)
            if item and item["kind"] == "symlink":
                require(candidate not in followed and len(followed) < 128,
                        "archive symlink chain is cyclic or too deep")
                followed.add(candidate)
                resolved.pop()
                pending = list(PurePosixPath(item["linkname"]).parts) + pending
        return "/".join(resolved)

    for name, entry in entries.items():
        parents = PurePosixPath(name).parents
        for parent in parents:
            ancestor = entries.get(str(parent))
            require(ancestor is None or ancestor["kind"] == "directory",
                    "archive member traverses a non-directory ancestor")
        if entry["kind"] == "symlink":
            dest = actual_symlink_target(name, entry["linkname"])
            if allowed_roots is not None:
                require(any((name == root or name.startswith(root + "/")) and
                            (dest == root or dest.startswith(root + "/"))
                            for root in allowed_roots),
                        "archive symlink chain crosses a runtime input root")
        if entry["kind"] == "hardlink":
            seen = {name}
            dest = entry["target"]
            while True:
                require(dest not in seen and dest in entries,
                        "archive hardlink is cyclic or has no target")
                seen.add(dest)
                target = entries[dest]
                if target["kind"] == "file":
                    break
                require(target["kind"] == "hardlink", "archive hardlink target is not a file")
                dest = target["target"]
    return {"entries": entries, "payload_bytes": total_bytes}


def required_inputs(configs: dict, nc_dir: Path, wk_dir: Path,
                    archived_names: set[str]) -> dict[str, Path]:
    paths = set()
    app = nc_dir / "apps/integration_weknora"
    for config in configs.values():
        for service in config["services"].values():
            for mount in service.get("volumes", []):
                if mount["type"] == "bind":
                    paths.add(Path(mount["source"]))
    paths.update((nc_dir / ".env", nc_dir / "compose.yaml",
                  nc_dir / "integration/nextcloud.lan.yaml",
                  nc_dir / "integration/nextcloud.https.yaml",
                  nc_dir / "integration/weknora.override.yaml",
                  nc_dir / "integration/weknora-rag-local.override.yaml",
                  wk_dir / ".env", wk_dir / "compose.yaml", wk_dir / "rag-model.env"))
    if "weknora-local/rag-model-token" in archived_names:
        paths.add(wk_dir / "rag-model-token")
    paths.discard(app)
    roots = ((nc_dir, "nextcloud"), (wk_dir, "weknora-local"),
             (nc_dir.parent / "WeKnora-ldap-ad", "weknora-source"))
    result = {}
    for path in sorted(paths):
        require(path.is_absolute() and path.resolve(strict=True) == path,
                "saved bind source is not an absolute, normalized path without symlink parents")
        for root, prefix in roots:
            if root in path.parents:
                result[f"{prefix}/{path.relative_to(root).as_posix()}"] = path
                break
        else:
            raise RestorePlanError("saved input is outside or equals an approved root")
    return {arc: path for arc, path in result.items()
            if not any(parent != path and parent in path.parents for parent in paths)}


def verify_code(code: dict, commit: str, nc_dir: Path) -> dict:
    raw = capture.run(["git", "ls-tree", "-rz", commit, "--",
                       "apps/integration_weknora"], cwd=nc_dir)
    tracked = {}
    for line in raw.split(b"\x00"):
        if not line:
            continue
        try:
            meta, path = line.split(b"\t", 1)
            mode, kind, object_id = meta.decode("ascii").split()
            name = path.decode("utf-8")
        except (ValueError, UnicodeError) as error:
            raise RestorePlanError("captured app Git tree is invalid") from error
        require(kind == "blob" and mode in {"100644", "100755", "120000"},
                "captured app tree contains an unsupported Git entry")
        tracked[name] = (mode, object_id)
    require(bool(tracked), "captured app commit is unavailable or empty")
    actual = {name: entry for name, entry in code["entries"].items()
              if entry["kind"] != "directory"}
    require(set(tracked).issubset(actual),
            "app archive has missing files relative to the captured commit")
    extras = set(actual) - set(tracked)
    for name in extras:
        relative = PurePosixPath(name).relative_to("apps/integration_weknora")
        require(relative.parts[0] == "node_modules" or
                ("__pycache__" in relative.parts and relative.suffix == ".pyc"),
                "app archive contains unknown untracked application code")
    for name, (mode, object_id) in tracked.items():
        content = capture.run(["git", "cat-file", "blob", object_id], cwd=nc_dir)
        entry = actual[name]
        if mode == "120000":
            require(entry["kind"] == "symlink" and
                    entry["linkname"].encode("utf-8") == content,
                    "archived app symlink differs from the captured Git commit")
        else:
            require(entry["kind"] == "file" and
                    entry["sha256"] == hashlib.sha256(content).hexdigest() and
                    bool(entry["mode"] & 0o111) == (mode == "100755"),
                    "archived app code differs from the captured Git commit")

    return {"git_tracked_files_verified": len(tracked),
            "captured_build_extra_entries": len(extras)}


def verify_checkpoint(directory: Path, nc_dir: Path, wk_dir: Path,
                      expected_manifest: str | None = None) -> dict:
    plain_owned(directory, 0o700, directory=True)
    names = expected_artifacts()
    require({path.name for path in directory.iterdir()} == names | {"manifest.json"},
            "checkpoint has missing, unexpected or incomplete-marker files")
    for name in names | {"manifest.json"}:
        plain_owned(directory / name, 0o600)
    manifest_file = directory / "manifest.json"
    require(manifest_file.stat().st_size <= MAX_MANIFEST_BYTES, "checkpoint manifest is too large")
    manifest_hash = capture.sha256(manifest_file)
    if expected_manifest is not None:
        require(DIGEST.fullmatch(expected_manifest) is not None and
                manifest_hash == expected_manifest, "checkpoint manifest SHA-256 differs from the external pin")
    manifest = json_object(manifest_file.read_bytes(), "checkpoint manifest")
    require(manifest.get("status") == "COMPLETE" and
            manifest.get("scope") == "shared_local_cold_pair" and
            manifest.get("all_services_stopped_at_completion") is True and
            manifest.get("restore_or_runtime_switch_performed") is False,
            "checkpoint is not a complete, stopped matched local capture")
    commit = manifest.get("nextcloud_git_commit", "")
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None,
            "captured Git commit is invalid")
    require(type(manifest.get("created_at_unix")) is int and manifest["created_at_unix"] > 0,
            "checkpoint creation time is invalid")
    artifacts = manifest.get("artifact_sha256")
    require(isinstance(artifacts, dict) and set(artifacts) == names,
            "checkpoint artifact allowlist differs from the required sixteen artifacts")
    for name, digest in artifacts.items():
        require(isinstance(digest, str) and DIGEST.fullmatch(digest) is not None and
                capture.sha256(directory / name) == digest,
                "checkpoint artifact SHA-256 verification failed")
    require(manifest.get("nextcloud_app_code_sha256") == artifacts["nextcloud-app-code.tar"],
            "checkpoint app-code digest is inconsistent")
    projects = manifest.get("projects")
    require(isinstance(projects, dict) and set(projects) == set(PROJECTS),
            "checkpoint projects differ from the exact local pair")
    configs = {}
    old_ids = set()
    for project in PROJECTS:
        saved = projects[project]
        require(isinstance(saved, dict) and isinstance(saved.get("containers"), dict) and
                set(saved["containers"]) == SERVICES[project] and
                isinstance(saved.get("volumes"), dict) and set(saved["volumes"]) == VOLUMES[project],
                "checkpoint service or volume topology differs from the local pair")
        require(saved["volumes"] == {role: f"{project}_{role}" for role in VOLUMES[project]},
                "checkpoint volume names are not the exact owned local volumes")
        for service, identity in saved["containers"].items():
            require(isinstance(identity, dict) and isinstance(identity.get("id"), str) and
                    CONTAINER.fullmatch(identity["id"]) is not None and
                    isinstance(identity.get("image_id"), str) and
                    IMAGE.fullmatch(identity["image_id"]) is not None and
                    isinstance(identity.get("image_tag"), str) and identity["image_tag"],
                    "checkpoint container or immutable image identity is invalid")
            require(identity["id"] not in old_ids, "checkpoint container identity is duplicated")
            old_ids.add(identity["id"])
        config_path = directory / f"{SHORT[project]}-resolved-compose.json"
        require(saved.get("resolved_compose_sha256") == artifacts[config_path.name],
                "checkpoint resolved configuration digest is inconsistent")
        config = json_object(config_path.read_bytes(), "saved resolved Compose")
        capture.validate_config(config, project, nc_dir, wk_dir)
        require(isinstance(config.get("networks"), dict) and bool(config["networks"]),
                "saved Compose network topology is unavailable")
        for definition in config["networks"].values():
            require(isinstance(definition.get("name"), str) and
                    definition["name"].startswith(project + "_") and not definition.get("external"),
                    "saved Compose uses a foreign or external network")
        for service, identity in saved["containers"].items():
            require(config["services"][service]["image"] == identity["image_tag"],
                    "saved Compose image reference differs from captured container")
        configs[project] = config
    code = archive_inventory(directory / "nextcloud-app-code.tar",
                             allowed_roots={"apps/integration_weknora"})
    code_verification = verify_code(code, commit, nc_dir)
    initial = archive_inventory(directory / "runtime-inputs.tar",
                                allowed_roots={"nextcloud", "weknora-local", "weknora-source"})
    inputs = required_inputs(configs, nc_dir, wk_dir, set(initial["entries"]))
    runtime = archive_inventory(directory / "runtime-inputs.tar", allowed_roots=set(inputs))
    require(all(arc in runtime["entries"] for arc in inputs),
            "checkpoint is missing a required runtime input root")
    archives = {}
    for project in PROJECTS:
        for role in sorted(VOLUMES[project]):
            name = f"{SHORT[project]}-{role}.tar"
            archives[name] = archive_inventory(directory / name)
    # Check that hashes did not change while the longer archive/Git validation ran.
    plain_owned(directory, 0o700, directory=True)
    require({path.name for path in directory.iterdir()} == names | {"manifest.json"},
            "checkpoint files changed during verification")
    for name, digest in artifacts.items():
        plain_owned(directory / name, 0o600)
        require(capture.sha256(directory / name) == digest,
                "checkpoint artifact changed during verification")
    plain_owned(manifest_file, 0o600)
    require(capture.sha256(manifest_file) == manifest_hash,
            "checkpoint manifest changed during verification")
    return {"manifest": manifest, "manifest_sha256": manifest_hash,
            "configs": configs, "inputs": inputs, "archives": archives,
            "app_code_verification": code_verification}


def verify_stopped_target(checked: dict, nc_dir: Path, wk_dir: Path) -> dict:
    capture.local_docker(nc_dir)
    stacks = []
    current_ids = set()
    current_config_digests = {}
    for project in PROJECTS:
        cwd = nc_dir if project == capture.NC_PROJECT else wk_dir
        command = capture.compose_args(nc_dir, wk_dir, project)
        config_bytes = capture.run(command + ["config", "--format", "json"], cwd=cwd)
        current_config_digests[project] = hashlib.sha256(config_bytes).hexdigest()
        config = json_object(config_bytes, "current resolved Compose")
        capture.validate_config(config, project, nc_dir, wk_dir)
        # Restoration can revert a newer image, but must retain the identical
        # volume/bind destinations. Config/input bytes are restored as a pair.
        old = checked["configs"][project]
        for service in SERVICES[project]:
            require(config["services"][service].get("volumes", []) ==
                    old["services"][service].get("volumes", []),
                    "current target mounts differ from the captured restore destinations")
        ids = set(capture.run(["docker", "ps", "-aq", "--no-trunc", "--filter",
                              f"label=com.docker.compose.project={project}"], cwd=nc_dir).decode().split())
        containers = {}
        for service in sorted(SERVICES[project]):
            item = capture.inspect("container", f"{project}-{service}-1", nc_dir)
            labels = item.get("Config", {}).get("Labels") or {}
            state = item.get("State") or {}
            require(labels.get("com.docker.compose.project") == project and
                    labels.get("com.docker.compose.service") == service and
                    labels.get("com.docker.compose.container-number", "1") == "1" and
                    labels.get("com.docker.compose.oneoff", "False") in {"False", "false"} and
                    item.get("Id") in ids and state.get("Running") is False and
                    not state.get("Paused") and not state.get("Restarting") and
                    not state.get("Dead") and state.get("Status") in {"exited", "created"},
                    "target must contain exactly the fifteen owned, stopped services")
            require(capture.container_runtime_matches(item, config["services"][service], config) and
                    item.get("Config", {}).get("Image") == config["services"][service]["image"],
                    "stopped target mounts, ports, environment or image reference differs from Compose")
            require(isinstance(item.get("Id"), str) and CONTAINER.fullmatch(item["Id"]) is not None and
                    isinstance(item.get("Image"), str) and IMAGE.fullmatch(item["Image"]) is not None,
                    "current container or image ID is invalid")
            require(item["Id"] not in current_ids, "current container ID is duplicated")
            current_ids.add(item["Id"])
            containers[service] = {"id": item["Id"], "image_id": item.get("Image")}
        require(ids == {item["id"] for item in containers.values()},
                "target has an extra or missing Compose container")
        volume_names = set(capture.run(["docker", "volume", "ls", "-q", "--filter",
                                       f"label=com.docker.compose.project={project}"], cwd=nc_dir).decode().split())
        expected = {f"{project}_{role}" for role in VOLUMES[project]}
        require(volume_names == expected, "target has extra or missing owned volumes")
        for role in VOLUMES[project]:
            name = f"{project}_{role}"
            volume = capture.inspect("volume", name, nc_dir)
            labels = volume.get("Labels") or {}
            require(volume.get("Name") == name and volume.get("Driver") == "local" and
                    not volume.get("Options") and
                    labels.get("com.docker.compose.project") == project and
                    labels.get("com.docker.compose.volume") == role,
                    "target volume ownership, driver or options differ from the expected local role")
        stacks.append({"project": project, "containers": containers,
                       "volumes": {role: f"{project}_{role}" for role in VOLUMES[project]},
                       "config": config, "cwd": cwd, "command": command})
    names = {name for stack in stacks for name in stack["volumes"].values()}
    networks = {entry["name"] for stack in stacks
                for entry in stack["config"].get("networks", {}).values()}
    for stack in stacks:
        for role, definition in stack["config"].get("networks", {}).items():
            require(definition.get("name", "").startswith(stack["project"] + "_"),
                    "target uses a foreign or external network")
            network = capture.inspect("network", definition["name"], nc_dir)
            labels = network.get("Labels") or {}
            require(labels.get("com.docker.compose.project") == stack["project"] and
                    labels.get("com.docker.compose.network") == role,
                    "target network does not have exact Compose ownership")
    all_ids = set(capture.run(["docker", "ps", "-aq", "--no-trunc"], cwd=nc_dir).decode().split())
    for container_id in all_ids - current_ids:
        item = capture.inspect("container", container_id, nc_dir)
        require(not any(mount.get("Type") == "volume" and mount.get("Name") in names
                        for mount in item.get("Mounts", [])),
                "a foreign container, including a stopped container, mounts a restore volume")
        require(not (set((item.get("NetworkSettings", {}).get("Networks") or {})) & networks),
                "a foreign container, including a stopped container, joins a restore network")
    old_images = {identity["image_id"] for project in PROJECTS
                  for identity in checked["manifest"]["projects"][project]["containers"].values()}
    current_images = {identity["image_id"] for stack in stacks
                      for identity in stack["containers"].values()}
    for image_id in sorted(old_images | current_images):
        image = capture.inspect("image", image_id, nc_dir)
        require(image.get("Id") == image_id and image.get("Os") == "linux",
                "an immutable checkpoint image or current recovery image is unavailable in the local Docker daemon")
    inputs = capture.input_paths(stacks, nc_dir, wk_dir)
    inputs["nextcloud-app-code"] = nc_dir / "apps/integration_weknora"
    fingerprint = capture.source_fingerprint(inputs)
    for stack in stacks:
        require(hashlib.sha256(capture.run(stack["command"] + ["config", "--format", "json"],
                                          cwd=stack["cwd"])).hexdigest() ==
                current_config_digests[stack["project"]],
                "current Compose configuration changed during target verification")
        for service, original in stack["containers"].items():
            item = capture.inspect("container", original["id"], nc_dir)
            state = item.get("State") or {}
            require(item.get("Id") == original["id"] and item.get("Image") == original["image_id"] and
                    state.get("Running") is False and not state.get("Paused") and
                    not state.get("Restarting") and not state.get("Dead") and
                    capture.container_runtime_matches(item, stack["config"]["services"][service], stack["config"]),
                    "stopped target changed during verification")
    require(capture.source_fingerprint(inputs) == fingerprint,
            "current bind inputs changed during target verification")
    return {"containers": {stack["project"]: stack["containers"] for stack in stacks},
            "resolved_compose_sha256": current_config_digests,
            "runtime_inputs_fingerprint": fingerprint,
            "old_images_present": sorted(old_images),
            "current_images_present": sorted(current_images)}


def restore_plan(checked: dict, *, target_verified: bool, current_target: dict | None = None) -> dict:
    manifest = checked["manifest"]
    volumes = [{"project": project, "role": role,
                "volume": manifest["projects"][project]["volumes"][role],
                "archive": f"{SHORT[project]}-{role}.tar"}
               for project in PROJECTS for role in sorted(VOLUMES[project])]
    old_images = {project: {service: identity["image_id"] for service, identity in
                           sorted(manifest["projects"][project]["containers"].items())}
                  for project in PROJECTS}
    steps = [
        {"order": 1, "action": "keep_all_fifteen_services_and_ingress_stopped",
         "requires": "repeat the exact stopped-resource and foreign-user checks immediately before any write"},
        {"order": 2, "action": "capture_pre_restore_recovery_copy",
         "requires": "new private cold archives of all eight CURRENT volumes, app code, inputs and current immutable image IDs; verify every SHA before overwriting"},
        {"order": 3, "action": "restore_captured_bind_inputs_and_app_code",
         "requires": "safe per-input staging then replacement; preserve archive ownership/modes and restore the captured Git app commit; no extraction over a live checkout"},
        {"order": 4, "action": "replace_all_eight_volume_contents_as_one_cold_pair",
         "requires": "exact pair and manifest SHA confirmation; clear destination contents only after recovery copy; restore ownership/modes; never overlay or mix logical dumps with cold PostgreSQL volumes",
         "volumes": volumes},
        {"order": 5, "action": "pin_all_restored_compose_services_to_captured_image_ids",
         "requires": "private resolved Compose configuration matching the verified saved inputs; use image IDs, not mutable tags; disable restart policies and retain closed ingress",
         "images": old_images},
        {"order": 6, "action": "verify_restored_bytes_and_isolated_database_start",
         "requires": "verify restored volumes/inputs/app code against archives; start only fenced database/Redis validation with no ingress or application traffic; verify database versions, schemas and paired identity"},
        {"order": 7, "action": "replay_external_post_checkpoint_withdrawals_and_reconcile",
         "requires": "authoritative external withdrawal/ACL/deletion ledger through recovery time; replay every post-checkpoint change while public ingress, cron and ordinary app workers remain closed; only fenced operator reconciliation may access apps"},
        {"order": 8, "action": "prove_revoked_sources_stay_denied_before_reopen",
         "requires": "both Nextcloud and WeKnora agree on publication versions and pairing; previously revoked sources denied in search/chat/history/download; no pending replay; record operator acceptance"},
        {"order": 9, "action": "operator_controlled_reopen",
         "requires": "all preceding recovery and external replay evidence accepted; open applications/workers first and ingress last; the verifier never starts them"},
    ]
    return {"mode": "read_only_restore_plan", "pair": capture.PAIR_CONFIRMATION,
            "checkpoint_manifest_sha256": checked["manifest_sha256"],
            "source_checkpoint_integrity_verified": True,
            "current_stopped_target_verified": target_verified,
            "current_stopped_inventory": current_target,
            "restore_verified": False, "apply_supported": False,
            "automatic_reopen_permitted": False,
            "external_withdrawal_replay_required": True,
            "nextcloud_git_commit": manifest["nextcloud_git_commit"],
            "nextcloud_app_code_sha256": manifest["nextcloud_app_code_sha256"],
            "app_code_verification": checked.get("app_code_verification"),
            "steps": steps,
            "open_gates": ["pre-restore recovery copy", "destructive restore implementation and rehearsal",
                           "restored byte/runtime verification", "external withdrawal replay and publication reconciliation",
                           "revocation acceptance before ingress reopening"] +
                          ([] if target_verified else ["live stopped target, owned volumes/networks and old immutable images"]) +
                          (["operator review of captured Git-ignored build extras"]
                           if checked.get("app_code_verification", {}).get("captured_build_extra_entries", 0) else [])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", required=True, type=Path)
    parser.add_argument("--nextcloud-dir", type=Path, default=ROOT)
    parser.add_argument("--weknora-dir", type=Path, default=ROOT.parent / "weknora-ldap-local")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--offline", action="store_true",
                        help="verify archives and saved Git code only; live target gates stay open")
    args = parser.parse_args(argv)
    try:
        for root in (args.nextcloud_dir, args.weknora_dir):
            require(root.is_absolute() and root.is_dir() and not root.is_symlink(),
                    "both project roots must be existing absolute real directories")
        nc_dir, wk_dir = args.nextcloud_dir.resolve(), args.weknora_dir.resolve()
        require(nc_dir != wk_dir, "project roots must differ")
        checked = verify_checkpoint(args.checkpoint_dir, nc_dir, wk_dir,
                                    args.expected_manifest_sha256)
        current_target = None
        if not args.offline:
            current_target = verify_stopped_target(checked, nc_dir, wk_dir)
        print(json.dumps(restore_plan(checked, target_verified=not args.offline,
                                     current_target=current_target),
                         indent=2, sort_keys=True))
        return 0
    except (RestorePlanError, capture.CheckpointError, OSError, ValueError, TypeError, KeyError) as error:
        # Do not print parser/Docker payloads: saved Compose contains secrets.
        if isinstance(error, (RestorePlanError, capture.CheckpointError)):
            message = str(error)
        else:
            message = "checkpoint or target could not be verified"
        print(f"Cold restore plan refused: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
