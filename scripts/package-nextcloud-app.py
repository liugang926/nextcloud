#!/usr/bin/env python3
"""Build and verify a reproducible, installable integration_weknora tarball."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET


PROJECT = Path(__file__).resolve().parents[1]
APP = PROJECT / "apps" / "integration_weknora"
APP_ID = "integration_weknora"
RUNTIME_SUFFIXES = {
    "appinfo": {".php", ".xml"},
    "css": {".css"},
    "js": {".js"},
    "lib": {".php"},
    "templates": {".php"},
}


def app_identity(info_file: Path) -> tuple[str, str]:
    root = ET.parse(info_file).getroot()
    app_id = root.findtext("id")
    version = root.findtext("version")
    if app_id != APP_ID or not version or any(c not in "0123456789." for c in version):
        raise ValueError("appinfo/info.xml has an unexpected app ID or version")
    return app_id, version


def runtime_files() -> list[Path]:
    files: list[Path] = []
    for folder, suffixes in RUNTIME_SUFFIXES.items():
        directory = APP / folder
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"missing or linked runtime directory: {directory}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"linked runtime path: {path}")
            if path.is_dir():
                continue
            if not path.is_file() or path.suffix not in suffixes:
                raise ValueError(f"unexpected runtime file: {path}")
            files.append(path)
    if APP / "appinfo" / "info.xml" not in files:
        raise ValueError("appinfo/info.xml is missing")
    if APP / "js" / "weknora-sidebar.js" not in files:
        raise ValueError("compiled Files sidebar is missing")
    return sorted(files, key=lambda path: path.relative_to(APP).as_posix())


def verify_frontend() -> None:
    with tempfile.TemporaryDirectory(prefix="integration-weknora-npm-") as temp:
        build_root = Path(temp)
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(APP / name, build_root / name)
        shutil.copytree(APP / "src", build_root / "src", symlinks=False)
        subprocess.run(
            ["npm", "ci", "--no-audit", "--no-fund"],
            cwd=build_root,
            check=True,
        )
        subprocess.run(["npm", "run", "build"], cwd=build_root, check=True)
        built = (build_root / "js" / "weknora-sidebar.js").read_bytes()
        checked_in = (APP / "js" / "weknora-sidebar.js").read_bytes()
        if built != checked_in:
            raise ValueError(
                "the committed Files sidebar differs from the lockfile build; "
                "run npm ci && npm run build in apps/integration_weknora"
            )


def add_file(archive: tarfile.TarFile, file: Path) -> None:
    data = file.read_bytes()
    entry = tarfile.TarInfo(f"{APP_ID}/{file.relative_to(APP).as_posix()}")
    entry.size = len(data)
    entry.mode = 0o644
    entry.mtime = 0
    entry.uid = entry.gid = 0
    archive.addfile(entry, io.BytesIO(data))


def add_directory(archive: tarfile.TarFile, name: str) -> None:
    entry = tarfile.TarInfo(name.rstrip("/") + "/")
    entry.type = tarfile.DIRTYPE
    entry.mode = 0o755
    entry.mtime = 0
    entry.uid = entry.gid = 0
    archive.addfile(entry)


def write_archive(output: Path, files: list[Path]) -> None:
    directories = {APP_ID}
    for file in files:
        relative = file.relative_to(APP)
        for parent in relative.parents:
            if parent != Path("."):
                directories.add(f"{APP_ID}/{parent.as_posix()}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".app-package-", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    for directory in sorted(directories, key=lambda name: (name.count("/"), name)):
                        add_directory(archive, directory)
                    for file in files:
                        add_file(archive, file)
        verify_archive(temporary, files)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def verify_archive(archive_file: Path, files: list[Path]) -> None:
    expected = {f"{APP_ID}/{file.relative_to(APP).as_posix()}" for file in files}
    with tempfile.TemporaryDirectory(prefix="integration-weknora-install-") as temp:
        install_root = Path(temp)
        with tarfile.open(archive_file, "r:gz") as archive:
            members = archive.getmembers()
            actual = {member.name for member in members if member.isfile()}
            if actual != expected or any(not (m.isfile() or m.isdir()) for m in members):
                raise ValueError("archive contains missing, extra, or linked entries")
            for member in members:
                if member.isfile():
                    source = APP / Path(member.name).relative_to(APP_ID)
                    if archive.extractfile(member).read() != source.read_bytes():
                        raise ValueError(f"archive content differs: {member.name}")
                target = install_root / member.name
                target.resolve().relative_to(install_root.resolve())
            archive.extractall(install_root)
        extracted = install_root / APP_ID
        app_id, _ = app_identity(extracted / "appinfo" / "info.xml")
        if extracted.name != app_id or not (extracted / "lib" / "AppInfo" / "Application.php").is_file():
            raise ValueError("extracted archive does not have a Nextcloud app install layout")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT / "dist", help="archive output directory"
    )
    args = parser.parse_args()
    _, version = app_identity(APP / "appinfo" / "info.xml")
    verify_frontend()
    files = runtime_files()
    output = args.output_dir.resolve() / f"{APP_ID}-{version}.tar.gz"
    write_archive(output, files)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"{output}\nsha256 {checksum}\nverified {len(files)} runtime files and install layout")


if __name__ == "__main__":
    main()
