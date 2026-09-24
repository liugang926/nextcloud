#!/usr/bin/env python3
"""Create private credentials for two new loopback-only pilot Compose projects.

This prepares files only. It never starts Docker or modifies an existing
Nextcloud checkout. The Nextcloud directory must be a separate Git worktree
with no .env; the state directory must not exist. Discard both after the pilot.
"""

import argparse
import os
from pathlib import Path
import re
import secrets
import socket


PILOT_PROJECT = re.compile(r"pilot-[a-z0-9][a-z0-9-]{0,59}\Z")
ROOT = Path(__file__).resolve().parents[2]


def free_loopback_port(port):
    if not 1024 <= port <= 65535:
        raise ValueError("pilot ports must be 1024..65535")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise ValueError(f"loopback port {port} is unavailable") from error


def private_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nextcloud-worktree", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--nextcloud-project", required=True)
    parser.add_argument("--weknora-project", required=True)
    parser.add_argument("--nextcloud-port", required=True, type=int)
    parser.add_argument("--weknora-port", required=True, type=int)
    parser.add_argument("--weknora-image", required=True)
    parser.add_argument("--weknora-config-file", required=True, type=Path)
    args = parser.parse_args()
    nc = args.nextcloud_worktree.resolve()
    state = args.state_dir.resolve()
    wk_config = args.weknora_config_file.resolve()
    if not (nc / ".git").is_file() or not (nc / "compose.yaml").is_file():
        parser.error("Nextcloud must be a separate Git worktree with compose.yaml")
    if (nc / ".env").exists() or state.exists():
        parser.error("refusing to overwrite an existing .env or state directory")
    if not wk_config.is_file() or not (ROOT / "scripts/ops/pilot-models.yaml").is_file():
        parser.error("WeKnora config and pilot model catalog must exist")
    for project in (args.nextcloud_project, args.weknora_project):
        if not PILOT_PROJECT.fullmatch(project):
            parser.error("both project names must start with pilot-")
    if args.nextcloud_project == args.weknora_project or args.nextcloud_port == args.weknora_port:
        parser.error("projects and loopback ports must differ")
    if not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,200}", args.weknora_image):
        parser.error("invalid image reference")
    try:
        free_loopback_port(args.nextcloud_port)
        free_loopback_port(args.weknora_port)
    except ValueError as error:
        parser.error(str(error))

    nc_env = "\n".join((
        "NEXTCLOUD_ADMIN_USER=pilotadmin",
        f"NEXTCLOUD_ADMIN_PASSWORD={secrets.token_urlsafe(24)}",
        f"NEXTCLOUD_DB_PASSWORD={secrets.token_urlsafe(24)}",
        f"WEKNORA_SERVICE_TOKEN={secrets.token_urlsafe(32)}",
        "NEXTCLOUD_HTTP_BIND_IP=127.0.0.1",
        f"NEXTCLOUD_HTTP_PORT={args.nextcloud_port}",
        "WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=1", "",
    ))
    admin_email = "pilot@weknora.test"
    admin_env = "\n".join((
        f"WEKNORA_TEST_ADMIN_EMAIL={admin_email}",
        f"WEKNORA_TEST_ADMIN_PASSWORD={secrets.token_urlsafe(24)}", "",
    ))
    db_password = secrets.token_urlsafe(24)
    wk_env = "\n".join((
        f"PILOT_WEKNORA_ENV_FILE={state / '.env'}",
        f"PILOT_WEKNORA_CONFIG_FILE={wk_config}",
        f"PILOT_MODELS_FILE={ROOT / 'scripts/ops/pilot-models.yaml'}",
        f"PILOT_NEXTCLOUD_PROJECT={args.nextcloud_project}",
        f"PILOT_WEKNORA_IMAGE={args.weknora_image}",
        f"PILOT_WEKNORA_PORT={args.weknora_port}",
        "POSTGRES_USER=weknora", f"POSTGRES_PASSWORD={db_password}",
        "POSTGRES_DB=weknora", "DB_DRIVER=postgres", "DB_HOST=pg", "DB_PORT=5432",
        "DB_USER=weknora", f"DB_PASSWORD={db_password}", "DB_NAME=weknora",
        "REDIS_ADDR=redis:6379", "DOCREADER_ADDR=docreader:50051",
        "STORAGE_TYPE=local", "LOCAL_STORAGE_BASE_DIR=/data/files",
        "RETRIEVE_DRIVER=postgres", "AUTO_MIGRATE=true", "GIN_MODE=release",
        "DISABLE_REGISTRATION=false", f"JWT_SECRET={secrets.token_urlsafe(32)}",
        f"SYSTEM_AES_KEY={secrets.token_hex(16)}",
        "WEKNORA_NEXTCLOUD_DEV_HTTP=1",
        "WEKNORA_NEXTCLOUD_ALLOWED_ORIGINS=http://nextcloud",
        "SSRF_WHITELIST_EXTRA=mock-embedding",
        "BUILTIN_MODELS_CONFIG=/run/config/pilot-models.yaml",
        f"WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL={admin_email}",
        "WEKNORA_SANDBOX_DOCKER_ENABLED=false", "TZ=Asia/Shanghai", "",
    ))

    created = []
    try:
        state.mkdir(mode=0o700, parents=False)
        created.append(state)
        for path, content in ((nc / ".env", nc_env),
                              (state / "admin.env", admin_env),
                              (state / ".env", wk_env)):
            private_file(path, content)
            created.append(path)
    except Exception:
        for path in reversed(created):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        raise
    print(f"private pilot env files prepared: {nc / '.env'} and {state}")
    print("credentials were not displayed; both Docker projects must be removed with their volumes after inspection")


if __name__ == "__main__":
    main()
