#!/usr/bin/env python3
"""Safely rotate the private LAN address of the local Nextcloud/WeKnora pair.

Run from the primary Nextcloud checkout with --old-ip, --new-ip and --apply.
Without --apply, this performs read-only preflight. It never edits the CA or
server private keys. A mode-0600 backup is kept before the first change.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
from typing import Any


PROJECT = Path(__file__).resolve().parent.parent
WEKNORA = PROJECT.parent / "weknora-ldap-local"
RFC1918 = tuple(ipaddress.ip_network(value) for value in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
LAN_ENV = ("NEXTCLOUD_LAN_HOST", "NEXTCLOUD_HTTP_BIND_IP", "NEXTCLOUD_HTTP_PORT",
           "NEXTCLOUD_HTTPS_PORT", "NEXTCLOUD_HTTPS_CERT_FILE",
           "NEXTCLOUD_HTTPS_KEY_FILE")


class RotationError(Exception):
    pass


def private_host_ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise RotationError("LAN address must be an IPv4 address") from error
    if not any(address in network for network in RFC1918):
        raise RotationError("LAN address must be an RFC1918 private IPv4 address")
    if any(address in (network.network_address, network.broadcast_address)
           for network in RFC1918):
        raise RotationError("LAN address cannot be a network or broadcast address")
    return str(address)


def local_ipv4_addresses() -> set[str]:
    """Read host interfaces without using a default-route or public-IP service."""
    addresses: set[str] = set()
    for args in (("ip", "-j", "-4", "addr", "show"), ("ifconfig", "-a")):
        try:
            result = subprocess.run(args, text=True, capture_output=True,
                                    check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode:
            continue
        if args[0] == "ip":
            try:
                interfaces = json.loads(result.stdout)
                addresses.update(info["local"] for interface in interfaces
                                 for info in interface.get("addr_info", [])
                                 if info.get("family") == "inet")
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        else:
            addresses.update(re.findall(r"\binet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)\b",
                                        result.stdout))
    return addresses


def run(args: list[str], label: str, *, cwd: Path = PROJECT,
        env: dict[str, str] | None = None, timeout: int = 180) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, env=env, text=True,
                                capture_output=True, check=False, timeout=timeout)
    except OSError as error:
        raise RotationError(f"{label} could not start: {error.strerror}") from error
    except subprocess.TimeoutExpired as error:
        raise RotationError(f"{label} timed out") from error
    if result.returncode:
        # Compose config, occ and OpenSSL output can contain local secrets.
        raise RotationError(f"{label} failed (exit {result.returncode})")
    return result.stdout.strip()


def safe_env() -> dict[str, str]:
    result = os.environ.copy()
    for name in list(result):
        if name.startswith("COMPOSE_") or name in LAN_ENV:
            result.pop(name)
    return result


def validate_local_docker(env: dict[str, str]) -> None:
    override = env.get("DOCKER_HOST")
    if override and not override.startswith("unix://"):
        raise RotationError("Docker must use a local Unix socket, not a remote daemon")
    contexts = load_json(["docker", "context", "inspect"],
                         "Docker context inspection", cwd=PROJECT, env=env)
    try:
        host = contexts[0]["Endpoints"]["docker"]["Host"]
    except (IndexError, KeyError, TypeError) as error:
        raise RotationError("Docker context has no Docker endpoint") from error
    if not isinstance(host, str) or not host.startswith("unix://"):
        raise RotationError("Docker must use a local Unix socket, not a remote daemon")


def nc_compose() -> list[str]:
    return ["docker", "compose", "--env-file", str(PROJECT / ".env"),
            "-f", str(PROJECT / "compose.yaml"),
            "-f", str(PROJECT / "integration/nextcloud.lan.yaml"),
            "-f", str(PROJECT / "integration/nextcloud.https.yaml"),
            "--profile", "lan-https"]


def wk_compose() -> list[str]:
    return ["docker", "compose", "--project-directory", str(WEKNORA),
            "-f", str(WEKNORA / "compose.yaml")]


def no_symlink_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RotationError(f"expected a regular file: {path}")


def env_values(text: str) -> dict[str, str]:
    wanted = {"NEXTCLOUD_LAN_HOST", "NEXTCLOUD_HTTP_BIND_IP", "COMPOSE_FILE",
              "NEXTCLOUD_HTTP_PORT", "NEXTCLOUD_HTTPS_PORT",
              "NEXTCLOUD_HTTPS_CERT_FILE", "NEXTCLOUD_HTTPS_KEY_FILE"}
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, equals, value = line.partition("=")
        if equals and key in wanted:
            if key in found:
                raise RotationError(f"duplicate {key} in Nextcloud .env")
            found[key] = value
    return found


def validate_nc_env(text: str, old_ip: str) -> tuple[int, int]:
    values = env_values(text)
    if values.get("NEXTCLOUD_LAN_HOST") != old_ip:
        raise RotationError("Nextcloud .env LAN host differs from --old-ip")
    if values.get("NEXTCLOUD_HTTP_BIND_IP", "127.0.0.1") != "127.0.0.1":
        raise RotationError("Nextcloud base HTTP listener must remain on loopback")
    if values.get("COMPOSE_FILE") != "compose.yaml:integration/nextcloud.lan.yaml":
        raise RotationError("Nextcloud COMPOSE_FILE is not the expected LAN overlay")
    for key, expected in (("NEXTCLOUD_HTTPS_CERT_FILE",
                           WEKNORA / "certs/weknora-lan-signed.crt"),
                          ("NEXTCLOUD_HTTPS_KEY_FILE",
                           WEKNORA / "certs/weknora-lan.key")):
        configured = Path(values[key]) if key in values else expected
        if not configured.is_absolute():
            configured = PROJECT / configured
        if configured.resolve() != expected.resolve():
            raise RotationError(f"{key} points outside the shared LAN certificate")
    try:
        http_port = int(values.get("NEXTCLOUD_HTTP_PORT", "18082"))
        https_port = int(values.get("NEXTCLOUD_HTTPS_PORT", "18482"))
    except ValueError as error:
        raise RotationError("Nextcloud LAN port is invalid") from error
    if not (1 <= http_port <= 65535 and 1 <= https_port <= 65535
            and http_port != https_port):
        raise RotationError("Nextcloud LAN ports are invalid")
    return http_port, https_port


def replace_exact_line(text: str, pattern: str, old: str, new: str,
                       label: str) -> str:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1 or matches[0].group("value") != old:
        raise RotationError(f"{label} does not uniquely contain the expected old value")
    match = matches[0]
    start, end = match.span("value")
    return text[:start] + new + text[end:]


def rewrite_weknora_compose(text: str, old_ip: str, new_ip: str) -> str:
    old_url = f"https://{old_ip}:18443"
    new_url = f"https://{new_ip}:18443"
    for key in ("FRONTEND_BASE_URL", "APP_EXTERNAL_URL"):
        text = replace_exact_line(
            text, rf"^\s*{key}:\s*(?P<value>\S+?)\s*$", old_url, new_url,
            f"WeKnora {key}")
    return replace_exact_line(
        text, r'^\s*-\s*"(?P<value>[^"\r\n]+:18443:443)"\s*$',
        f"{old_ip}:18443:443", f"{new_ip}:18443:443", "WeKnora LAN bind")


def rewrite_nginx(text: str, old_ip: str, new_ip: str) -> str:
    return replace_exact_line(
        text, r"^\s*server_name\s+(?P<value>[^;\s]+);\s*$",
        old_ip, new_ip, "WeKnora nginx server_name")


def rewrite_san_config(text: str, old_ip: str, new_ip: str) -> str:
    matches = list(re.finditer(r"^\s*subjectAltName\s*=\s*(?P<value>[^\r\n#]+?)\s*$",
                               text, re.MULTILINE))
    if len(matches) != 1:
        raise RotationError("expected one OpenSSL subjectAltName line")
    match = matches[0]
    entries = [entry.strip() for entry in match.group("value").split(",")]
    if entries.count(f"IP:{old_ip}") != 1 or any(not e.startswith("IP:") for e in entries):
        raise RotationError("OpenSSL SAN does not contain exactly one old IP entry")
    if len(entries) != len(set(entries)):
        raise RotationError("OpenSSL SAN contains duplicate IP entries")
    for entry in entries:
        private_host_ipv4(entry[3:])
    updated = [entry for entry in entries if entry != f"IP:{old_ip}"]
    if f"IP:{new_ip}" not in updated:
        position = entries.index(f"IP:{old_ip}")
        updated.insert(position, f"IP:{new_ip}")
    start, end = match.span("value")
    return text[:start] + ",".join(updated) + text[end:]


def load_json(args: list[str], label: str, *, cwd: Path, env: dict[str, str]) -> Any:
    try:
        return json.loads(run(args, label, cwd=cwd, env=env))
    except json.JSONDecodeError as error:
        raise RotationError(f"{label} returned invalid JSON") from error


def one_port(config: dict[str, Any], service: str, host: str,
             published: int, target: int) -> None:
    try:
        ports = config["services"][service]["ports"]
    except (KeyError, TypeError) as error:
        raise RotationError(f"{service} port configuration is missing") from error
    if (not isinstance(ports, list) or len(ports) != 1
            or not isinstance(ports[0], dict)
            or ports[0].get("host_ip") != host
            or str(ports[0].get("published")) != str(published)
            or ports[0].get("target") != target
            or ports[0].get("protocol") != "tcp"):
        raise RotationError(f"{service} has an unexpected published listener")


def validate_compose(host: str, http_port: int, https_port: int,
                     env: dict[str, str]) -> None:
    nc = load_json([*nc_compose(), "config", "--format", "json"],
                   "Nextcloud Compose config", cwd=PROJECT, env=env)
    if nc.get("name") != "nextcloud-weknora-dev":
        raise RotationError("unexpected Nextcloud Compose project")
    one_port(nc, "nextcloud-https", host, https_port, 443)
    expected_nc_mounts = {
        "/etc/nginx/tls/server.crt": WEKNORA / "certs/weknora-lan-signed.crt",
        "/etc/nginx/tls/server.key": WEKNORA / "certs/weknora-lan.key",
    }
    for target, source in expected_nc_mounts.items():
        require_readonly_mount(nc, "nextcloud-https", target, source)
    try:
        ports = nc["services"]["nextcloud"]["ports"]
    except (KeyError, TypeError) as error:
        raise RotationError("Nextcloud HTTP listeners are missing") from error
    expected = {(host, str(http_port), 80), ("127.0.0.1", str(http_port), 80)}
    if not isinstance(ports, list) or any(not isinstance(p, dict) for p in ports):
        raise RotationError("Nextcloud HTTP listeners are malformed")
    actual = {(p.get("host_ip"), str(p.get("published")), p.get("target")) for p in ports}
    if len(ports) != 2 or actual != expected:
        raise RotationError("Nextcloud HTTP listeners differ from loopback and private LAN")
    wk = load_json([*wk_compose(), "config", "--format", "json"],
                   "WeKnora Compose config", cwd=WEKNORA, env=env)
    if wk.get("name") != "weknora-ldap-local":
        raise RotationError("unexpected WeKnora Compose project")
    one_port(wk, "app", "127.0.0.1", 18081, 8080)
    one_port(wk, "frontend", "127.0.0.1", 18080, 80)
    one_port(wk, "lan-gateway", host, 18443, 443)
    require_readonly_mount(wk, "lan-gateway", "/certs/weknora-lan.crt",
                           WEKNORA / "certs/weknora-lan-signed.crt")
    require_readonly_mount(wk, "lan-gateway", "/certs/weknora-lan.key",
                           WEKNORA / "certs/weknora-lan.key")
    app_env = wk["services"]["app"]["environment"]
    if not isinstance(app_env, dict):
        raise RotationError("WeKnora app environment is malformed")
    for key in ("FRONTEND_BASE_URL", "APP_EXTERNAL_URL"):
        if app_env.get(key) != f"https://{host}:18443":
            raise RotationError(f"WeKnora {key} does not match the LAN listener")


def require_readonly_mount(config: dict[str, Any], service: str,
                           target: str, source: Path) -> None:
    try:
        mounts = config["services"][service]["volumes"]
    except (KeyError, TypeError) as error:
        raise RotationError(f"{service} mounts are missing") from error
    matching = [item for item in mounts if isinstance(item, dict)
                and item.get("target") == target]
    if (len(matching) != 1 or matching[0].get("type") != "bind"
            or not matching[0].get("read_only")
            or Path(matching[0].get("source", "")).resolve() != source.resolve()):
        raise RotationError(f"{service} {target} must be the shared read-only LAN file")


def openssl_public_key(args: list[str], label: str, env: dict[str, str]) -> str:
    return run(["openssl", *args], label, cwd=WEKNORA, env=env)


def validate_ca_and_old_cert(old_ip: str, env: dict[str, str]) -> None:
    certs = WEKNORA / "certs"
    ca = certs / "weknora-lan-ca.crt"
    ca_key = certs / "weknora-lan-ca.key"
    cert = certs / "weknora-lan-signed.crt"
    key = certs / "weknora-lan.key"
    for path in (ca, ca_key, cert, key):
        no_symlink_file(path)
    for path in (ca_key, key):
        if path.stat().st_mode & 0o077:
            raise RotationError(f"private key permissions are too broad: {path}")
    ca_pub = openssl_public_key(["x509", "-in", str(ca), "-pubkey", "-noout"],
                                "CA certificate public key", env)
    key_pub = openssl_public_key(["pkey", "-in", str(ca_key), "-pubout",
                                  "-passin", "pass:"], "CA private key public key", env)
    if ca_pub != key_pub:
        raise RotationError("LAN CA certificate and private key do not match")
    run(["openssl", "verify", "-purpose", "sslserver", "-CAfile", str(ca), str(cert)],
        "existing LAN certificate verification", cwd=WEKNORA, env=env)
    cert_pub = openssl_public_key(["x509", "-in", str(cert), "-pubkey", "-noout"],
                                  "server certificate public key", env)
    server_pub = openssl_public_key(["pkey", "-in", str(key), "-pubout",
                                    "-passin", "pass:"], "server private key public key", env)
    if cert_pub != server_pub:
        raise RotationError("LAN server certificate and private key do not match")
    if ("IP Address", old_ip) not in certificate_sans(cert):
        raise RotationError("existing LAN certificate lacks the expected old IP SAN")


def certificate_sans(cert: Path) -> tuple[Any, ...]:
    try:
        return tuple(ssl._ssl._test_decode_cert(str(cert)).get("subjectAltName", ()))
    except (OSError, ssl.SSLError, ValueError) as error:
        raise RotationError("LAN certificate SAN cannot be decoded") from error


def sign_candidate(san_config: str, new_ip: str, destination: Path,
                   env: dict[str, str]) -> None:
    certs = WEKNORA / "certs"
    config = destination.parent / "server.cnf"
    csr = destination.parent / "server.csr"
    config.write_text(san_config)
    os.chmod(config, 0o600)
    run(["openssl", "req", "-new", "-sha256", "-key", str(certs / "weknora-lan.key"),
         "-passin", "pass:", "-subj", f"/CN={new_ip}", "-out", str(csr)],
        "LAN certificate request", cwd=WEKNORA, env=env)
    serial = "0x" + secrets.token_hex(16)
    run(["openssl", "x509", "-req", "-sha256", "-days", "365", "-in", str(csr),
         "-CA", str(certs / "weknora-lan-ca.crt"),
         "-CAkey", str(certs / "weknora-lan-ca.key"), "-passin", "pass:",
         "-set_serial", serial, "-extfile", str(config), "-extensions", "server_cert",
         "-out", str(destination)], "LAN certificate signing", cwd=WEKNORA, env=env)
    run(["openssl", "verify", "-purpose", "sslserver", "-CAfile",
         str(certs / "weknora-lan-ca.crt"), str(destination)],
        "new LAN certificate verification", cwd=WEKNORA, env=env)
    if ("IP Address", new_ip) not in certificate_sans(destination):
        raise RotationError("new LAN certificate lacks the new IP SAN")
    new_pub = openssl_public_key(["x509", "-in", str(destination), "-pubkey", "-noout"],
                                 "new certificate public key", env)
    key_pub = openssl_public_key(["pkey", "-in", str(certs / "weknora-lan.key"),
                                  "-pubout", "-passin", "pass:"],
                                 "server private key public key", env)
    if new_pub != key_pub:
        raise RotationError("new LAN certificate and existing server key do not match")


def nc_occ_get(name: str, env: dict[str, str], *, allow_missing: bool = False) -> Any:
    marker = f"__lan_rotation_missing_{secrets.token_hex(16)}__" if allow_missing else None
    args = [*nc_compose(), "exec", "-T", "-u", "www-data", "nextcloud",
            "php", "occ", "config:system:get", name, "--output=json"]
    if marker:
        args.append(f"--default-value={marker}")
    raw = run(args,
              f"Nextcloud {name} read", env=env)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RotationError(f"Nextcloud {name} returned invalid JSON") from error
    return [] if marker is not None and value == marker else value


def nc_occ_set(name: str, value: Any, env: dict[str, str], *, as_json: bool = False) -> None:
    args = [*nc_compose(), "exec", "-T", "-u", "www-data", "nextcloud",
            "php", "occ", "config:system:set", name]
    if as_json:
        args.append("--type=json")
    args.append("--value=" + (json.dumps(value, separators=(",", ":")) if as_json else value))
    run(args, f"Nextcloud {name} update", env=env)


def web_url(env: dict[str, str]) -> str:
    return run([*nc_compose(), "exec", "-T", "-u", "www-data", "nextcloud",
                "php", "occ", "config:app:get", "integration_weknora", "weknora_web_url"],
               "Nextcloud WeKnora web URL read", env=env)


def verify_weknora_https(host: str, env: dict[str, str]) -> None:
    status = run(["curl", "--noproxy", "*", "--silent", "--show-error",
                  "--max-time", "20", "--output", "/dev/null",
                  "--write-out", "%{http_code}", "--cacert",
                  str(WEKNORA / "certs/weknora-lan-ca.crt"),
                  f"https://{host}:18443/"],
                 "WeKnora HTTPS probe", env=env)
    if not (status.isdigit() and 200 <= int(status) < 400):
        raise RotationError("WeKnora HTTPS endpoint did not return a success or redirect")


def read_runtime(old_ip: str, http_port: int, https_port: int,
                 env: dict[str, str]) -> dict[str, Any]:
    domains = nc_occ_get("trusted_domains", env)
    cli_url = nc_occ_get("overwrite.cli.url", env)
    wk_url = web_url(env)
    if (not isinstance(domains, list) or domains.count(old_ip) != 1
            or any(not isinstance(value, str) for value in domains)):
        raise RotationError("Nextcloud trusted_domains lacks one expected old LAN host")
    if cli_url not in (f"http://{old_ip}:{http_port}",
                       f"https://{old_ip}:{https_port}"):
        raise RotationError("Nextcloud overwrite.cli.url differs from the old LAN host")
    if wk_url != f"https://{old_ip}:18443":
        raise RotationError("Nextcloud WeKnora web URL differs from the old LAN host")
    # The gateway helper owns proxy state. A copy is retained for recovery.
    proxies = nc_occ_get("trusted_proxies", env, allow_missing=True)
    if not isinstance(proxies, list) or any(not isinstance(x, str) for x in proxies):
        raise RotationError("Nextcloud trusted_proxies is not a string array")
    return {"trusted_domains": domains, "trusted_proxies": proxies,
            "overwrite.cli.url": cli_url, "weknora_web_url": wk_url}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def managed_files() -> dict[str, Path]:
    files = {"nextcloud.env": PROJECT / ".env",
             "weknora.compose.yaml": WEKNORA / "compose.yaml",
             "weknora.nginx-lan.conf": WEKNORA / "nginx-lan.conf",
             "weknora.openssl.cnf": WEKNORA / "certs/weknora-lan-openssl.cnf",
             "weknora.server.crt": WEKNORA / "certs/weknora-lan-signed.crt"}
    state = PROJECT / "dist/nextcloud-https-gateway.json"
    if state.exists():
        files["nextcloud.gateway-state.json"] = state
    return files


def backup_files(files: dict[str, Path], old_ip: str, new_ip: str,
                 runtime: dict[str, Any],
                 expected_hashes: dict[str, str] | None = None) -> Path:
    base = PROJECT / "dist" / "lan-ip-rotation-backups"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    backup = Path(tempfile.mkdtemp(prefix="rotation-", dir=base))
    os.chmod(backup, 0o700)
    entries: dict[str, Any] = {}
    for label, source in files.items():
        no_symlink_file(source)
        before_hash = sha256(source)
        if expected_hashes is not None and before_hash != expected_hashes[label]:
            raise RotationError(f"{label} changed during preflight")
        target = backup / label
        with source.open("rb") as inp, target.open("xb") as out:
            shutil.copyfileobj(inp, out)
        os.chmod(target, 0o600)
        if sha256(target) != before_hash or sha256(source) != before_hash:
            raise RotationError(f"backup verification failed for {label}")
        entries[label] = {"path": str(source), "sha256": sha256(source),
                          "original_mode": source.stat().st_mode & 0o777}
    manifest = {"version": 1, "old_ip": old_ip, "new_ip": new_ip,
                "files": entries, "gateway_state_existed":
                "nextcloud.gateway-state.json" in files, "nextcloud_occ": runtime}
    manifest_path = backup / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(manifest_path, 0o600)
    return backup


def atomic_replace(path: Path, content: bytes, *, expected_hash: str | None = None) -> None:
    no_symlink_file(path)
    if expected_hash is not None and sha256(path) != expected_hash:
        raise RotationError(f"file changed after backup: {path}")
    mode = path.stat().st_mode & 0o777
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.rotate-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def ensure_running_image_matches(env: dict[str, str]) -> None:
    config = load_json([*wk_compose(), "config", "--format", "json"],
                       "WeKnora Compose config", cwd=WEKNORA, env=env)
    image = config["services"]["app"]["image"]
    ids = run([*wk_compose(), "ps", "-q", "app"], "WeKnora app lookup",
              cwd=WEKNORA, env=env).splitlines()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise RotationError("WeKnora app must have one running container")
    current = run(["docker", "inspect", "--format", "{{.Image}}", ids[0]],
                  "WeKnora running image lookup", env=env)
    declared = run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                   "WeKnora declared image lookup", env=env)
    if current != declared:
        raise RotationError("WeKnora running app image differs from Compose image; "
                            "recreation requires manual review")
    hash_line = run([*wk_compose(), "config", "--hash", "app"],
                    "WeKnora app Compose hash", cwd=WEKNORA, env=env)
    parts = hash_line.split()
    if len(parts) != 2 or parts[0] != "app" or not re.fullmatch(r"[0-9a-f]{64}", parts[1]):
        raise RotationError("WeKnora app Compose hash is invalid")
    running_hash = run(["docker", "inspect", "--format",
                        '{{index .Config.Labels "com.docker.compose.config-hash"}}', ids[0]],
                       "WeKnora running app config hash", env=env)
    if running_hash != parts[1]:
        raise RotationError("WeKnora running app config differs from Compose; "
                            "recreation requires manual review")


def preflight(old_ip: str, new_ip: str, env: dict[str, str]) -> tuple[dict[str, str],
                                                                    dict[str, Any], int, int,
                                                                    dict[str, str]]:
    if new_ip not in local_ipv4_addresses():
        raise RotationError("new LAN address is not assigned to a host interface")
    validate_local_docker(env)
    files = managed_files()
    for path in files.values():
        no_symlink_file(path)
    http_port, https_port = validate_nc_env(files["nextcloud.env"].read_text(), old_ip)
    transformed = {
        "weknora.compose.yaml": rewrite_weknora_compose(
            files["weknora.compose.yaml"].read_text(), old_ip, new_ip),
        "weknora.nginx-lan.conf": rewrite_nginx(
            files["weknora.nginx-lan.conf"].read_text(), old_ip, new_ip),
        "weknora.openssl.cnf": rewrite_san_config(
            files["weknora.openssl.cnf"].read_text(), old_ip, new_ip),
    }
    validate_compose(old_ip, http_port, https_port, env)
    validate_ca_and_old_cert(old_ip, env)
    run([sys.executable, str(PROJECT / "scripts/enable-lan-https.py"), "--check-only"],
        "Nextcloud HTTPS helper preflight", env=env)
    ensure_running_image_matches(env)
    runtime = read_runtime(old_ip, http_port, https_port, env)
    return transformed, runtime, http_port, https_port, {
        label: sha256(path) for label, path in files.items()}


def apply(old_ip: str, new_ip: str, env: dict[str, str]) -> Path:
    transformed, runtime, http_port, https_port, expected = preflight(old_ip, new_ip, env)
    files = managed_files()
    if set(files) != set(expected) or any(sha256(path) != expected[label]
                                             for label, path in files.items()):
        raise RotationError("managed files changed after preflight")
    with tempfile.TemporaryDirectory(prefix="lan-cert-", dir=WEKNORA / "certs") as directory:
        candidate = Path(directory) / "server.crt"
        sign_candidate(transformed["weknora.openssl.cnf"], new_ip, candidate, env)
        backup = backup_files(files, old_ip, new_ip, runtime, expected)
        try:
            for label in ("weknora.compose.yaml", "weknora.nginx-lan.conf",
                          "weknora.openssl.cnf"):
                atomic_replace(files[label], transformed[label].encode(),
                               expected_hash=expected[label])
            atomic_replace(files["weknora.server.crt"], candidate.read_bytes(),
                           expected_hash=expected["weknora.server.crt"])
            # This helper updates only Nextcloud's LAN .env keys, adds the new
            # trusted domain and restarts only the Nextcloud service.
            run([str(PROJECT / "scripts/allow-lan-access.sh"), new_ip],
                "Nextcloud LAN helper", env=env)
            validate_nc_env(files["nextcloud.env"].read_text(), new_ip)
            validate_compose(new_ip, http_port, https_port, env)
            run([sys.executable, str(PROJECT / "scripts/enable-lan-https.py"),
                 "--check-only"], "Nextcloud HTTPS helper preflight", env=env)
            run([*wk_compose(), "up", "-d", "--no-deps", "--wait",
                 "--wait-timeout", "120", "app", "lan-gateway"],
                "WeKnora app and LAN gateway restart", cwd=WEKNORA, env=env)
            verify_weknora_https(new_ip, env)
            domains = nc_occ_get("trusted_domains", env)
            if (not isinstance(domains, list) or domains.count(old_ip) != 1
                    or domains.count(new_ip) != 1):
                raise RotationError("Nextcloud trusted_domains changed unexpectedly")
            desired = [value for value in domains if value != old_ip]
            nc_occ_set("trusted_domains", desired, env, as_json=True)
            run([*nc_compose(), "exec", "-T", "-u", "www-data", "nextcloud",
                 "php", "occ", "config:app:set", "integration_weknora", "weknora_web_url",
                 f"--value=https://{new_ip}:18443"],
                "Nextcloud WeKnora web URL update", env=env)
            run([sys.executable, str(PROJECT / "scripts/enable-lan-https.py")],
                "Nextcloud HTTPS helper", env=env)
            if (nc_occ_get("trusted_domains", env) != desired
                    or nc_occ_get("overwrite.cli.url", env)
                    != f"https://{new_ip}:{https_port}"
                    or web_url(env) != f"https://{new_ip}:18443"):
                raise RotationError("Nextcloud LAN settings did not persist")
            validate_compose(new_ip, http_port, https_port, env)
        except (RotationError, OSError) as error:
            raise RotationError(f"{error}; changes may be partial; restore from {backup}") from error
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-ip", required=True, help="current private LAN IPv4 address")
    parser.add_argument("--new-ip", required=True, help="new private LAN IPv4 address")
    parser.add_argument("--apply", action="store_true",
                        help="after preflight, back up files, sign a cert and update services")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        old_ip = private_host_ipv4(args.old_ip)
        new_ip = private_host_ipv4(args.new_ip)
        if old_ip == new_ip:
            raise RotationError("old and new LAN addresses must differ")
        env = safe_env()
        if args.apply:
            lock_path = PROJECT / "dist/lan-ip-rotation.lock"
            lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with lock_path.open("a+") as lock:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                backup = apply(old_ip, new_ip, env)
            print(f"LAN IP rotation complete: {new_ip}; backup: {backup}")
        else:
            preflight(old_ip, new_ip, env)
            print(f"LAN IP rotation preflight passed: {old_ip} -> {new_ip}")
    except RotationError as error:
        print(f"LAN IP rotation: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
