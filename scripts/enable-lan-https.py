#!/usr/bin/env python3
"""Start the optional LAN TLS gateway and trust only its current Docker IP."""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple


PROJECT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT / "dist" / "nextcloud-https-gateway.json"
LOCK_FILE = PROJECT / "dist" / "nextcloud-https-gateway.lock"
DEFAULT_CA = PROJECT.parent / "weknora-ldap-local" / "certs" / "weknora-lan-ca.crt"
CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}\Z")


class GatewayError(Exception):
    pass


def command(args: Sequence[str], label: str, *, env: Optional[Dict[str, str]] = None) -> str:
    try:
        result = subprocess.run(
            list(args), cwd=PROJECT, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=180,
        )
    except OSError as error:
        raise GatewayError(f"{label} could not start: {error.strerror}") from error
    except subprocess.TimeoutExpired as error:
        raise GatewayError(f"{label} timed out") from error
    if result.returncode:
        # Compose config and occ output can contain credentials. Never echo it.
        raise GatewayError(f"{label} failed (exit {result.returncode})")
    return result.stdout.strip()


def compose_args() -> List[str]:
    return [
        "docker", "compose", "--env-file", str(PROJECT / ".env"),
        "-f", str(PROJECT / "compose.yaml"),
        "-f", str(PROJECT / "integration" / "nextcloud.lan.yaml"),
        "-f", str(PROJECT / "integration" / "nextcloud.https.yaml"),
        "--profile", "lan-https",
    ]


def strict_private_ipv4(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise GatewayError(f"{label} is not an IPv4 address string")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise GatewayError(f"{label} is not an IP address") from error
    if (address.version != 4 or not address.is_private or address.is_loopback
            or address.is_link_local or address.is_unspecified):
        raise GatewayError(f"{label} must be a private, non-loopback IPv4 address")
    return str(address)


def gateway_config(config: Dict[str, Any]) -> Tuple[str, int, int, Path, Path]:
    if not isinstance(config, dict):
        raise GatewayError("Compose configuration is invalid")
    if config.get("name") != "nextcloud-weknora-dev":
        raise GatewayError("unexpected Compose project name")
    try:
        service = config["services"]["nextcloud-https"]
        ports = service["ports"]
        mounts = service["volumes"]
        nextcloud_ports = config["services"]["nextcloud"]["ports"]
    except (KeyError, TypeError) as error:
        raise GatewayError("Compose gateway or Nextcloud service is incomplete") from error
    if not isinstance(ports, list) or len(ports) != 1:
        raise GatewayError("HTTPS gateway must have exactly one published port")
    if not isinstance(nextcloud_ports, list):
        raise GatewayError("Nextcloud LAN listener is missing")
    port = ports[0]
    if not isinstance(port, dict) or port.get("target") != 443 or port.get("protocol") != "tcp":
        raise GatewayError("HTTPS gateway must publish TCP port 443 only")
    host = strict_private_ipv4(port.get("host_ip"), "HTTPS listener host")
    try:
        published = int(port["published"])
    except (KeyError, TypeError, ValueError) as error:
        raise GatewayError("HTTPS listener port is invalid") from error
    if not 1 <= published <= 65535:
        raise GatewayError("HTTPS listener port is invalid")
    http_ports = [p for p in nextcloud_ports if isinstance(p, dict)
                  and p.get("host_ip") == host and p.get("target") == 80]
    if len(http_ports) != 1:
        raise GatewayError("run scripts/allow-lan-access.sh before enabling HTTPS")
    try:
        http_port = int(http_ports[0]["published"])
    except (KeyError, TypeError, ValueError) as error:
        raise GatewayError("HTTP listener port is invalid") from error
    if not 1 <= http_port <= 65535:
        raise GatewayError("HTTP listener port is invalid")
    if http_port == published:
        raise GatewayError("HTTP and HTTPS listeners cannot share one host port")
    volumes = {m.get("target"): m for m in mounts if isinstance(m, dict)}
    paths = []
    for target in ("/etc/nginx/tls/server.crt", "/etc/nginx/tls/server.key"):
        mount = volumes.get(target)
        if (not mount or mount.get("type") != "bind" or not mount.get("read_only")
                or not isinstance(mount.get("source"), str)):
            raise GatewayError(f"{target} must be a read-only bind mount")
        paths.append(Path(mount["source"]))
    return host, published, http_port, paths[0], paths[1]


def validate_certificate(host: str, cert: Path, key: Path, ca: Path) -> None:
    for label, path in (("certificate", cert), ("private key", key), ("CA", ca)):
        if not path.is_file():
            raise GatewayError(f"{label} file is missing: {path}")
    try:
        names = ssl._ssl._test_decode_cert(str(cert)).get("subjectAltName", ())
    except (OSError, ssl.SSLError, ValueError) as error:
        raise GatewayError("certificate cannot be decoded") from error
    if ("IP Address", host) not in names:
        raise GatewayError(f"certificate SAN does not contain {host}")
    command(["openssl", "verify", "-purpose", "sslserver", "-CAfile", str(ca), str(cert)],
            "certificate chain verification")
    cert_public = command(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
                          "certificate public key extraction")
    key_public = command(["openssl", "pkey", "-in", str(key), "-pubout", "-passin", "pass:"],
                         "private key public key extraction")
    if cert_public != key_public:
        raise GatewayError("certificate and private key do not match")


def parse_container_id(value: str) -> Optional[str]:
    lines = value.splitlines()
    if not lines:
        return None
    if len(lines) != 1 or not CONTAINER_ID.fullmatch(lines[0]):
        raise GatewayError("gateway container lookup is ambiguous")
    return lines[0]


def container_ip(container_id: Optional[str]) -> Optional[str]:
    if container_id is None:
        return None
    raw = command(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}",
                   container_id], "gateway network inspection")
    try:
        networks = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GatewayError("gateway network inspection returned invalid JSON") from error
    if not isinstance(networks, dict) or len(networks) != 1:
        raise GatewayError("gateway must have exactly one Docker network")
    network = next(iter(networks.values()))
    if not isinstance(network, dict):
        raise GatewayError("gateway Docker network is invalid")
    value = network.get("IPAddress")
    return strict_private_ipv4(value, "gateway Docker IP") if value else None


def read_state() -> Optional[str]:
    if not STATE_FILE.exists():
        return None
    try:
        value = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise GatewayError("gateway state file is unreadable") from error
    if not isinstance(value, dict) or value.get("version") != 1:
        raise GatewayError("gateway state file has an unknown format")
    return strict_private_ipv4(value.get("managed_proxy_ip"), "saved gateway IP")


def write_state(ip: str) -> None:
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE_FILE.with_name(STATE_FILE.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("x") as stream:
            json.dump({"version": 1, "managed_proxy_ip": ip}, stream)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, STATE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def desired_proxies(existing: Any, new_ip: str, old_ips: Sequence[Optional[str]]) -> List[str]:
    if not isinstance(existing, list) or any(not isinstance(x, str) for x in existing):
        raise GatewayError("trusted_proxies is not a string array; refusing to edit it")
    stale = {ip for ip in old_ips if ip and ip != new_ip}
    result = [ip for ip in existing if ip not in stale]
    if new_ip not in result:
        result.append(new_ip)
    return result


def system_config(compose: Sequence[str], env: Dict[str, str]) -> Dict[str, Any]:
    raw = command([*compose, "exec", "-T", "-u", "www-data", "nextcloud", "php", "occ",
                   "config:list", "system", "--output=json"], "Nextcloud configuration read", env=env)
    try:
        values = json.loads(raw)["system"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise GatewayError("Nextcloud configuration JSON is invalid") from error
    if not isinstance(values, dict):
        raise GatewayError("Nextcloud system configuration is invalid")
    return values


def occ_set(compose: Sequence[str], env: Dict[str, str], name: str, value: str,
            *, json_type: bool = False) -> None:
    args = [*compose, "exec", "-T", "-u", "www-data", "nextcloud", "php", "occ",
            "config:system:set", name]
    if json_type:
        args.extend(["--type=json"])
    args.append(f"--value={value}")
    command(args, f"Nextcloud {name} update", env=env)


def fetch_login(url: str, ca: Optional[Path], headers: Sequence[str] = ()) -> Tuple[str, str, str]:
    with tempfile.NamedTemporaryFile(prefix="nc-lan-login-") as body:
        args = ["curl", "--noproxy", "*", "--silent", "--show-error", "--location",
                "--max-redirs", "5", "--max-time", "20", "--output", body.name,
                "--write-out", "%{http_code}|%{url_effective}"]
        if ca is not None:
            args.extend(["--cacert", str(ca)])
        for header in headers:
            args.extend(["--header", header])
        raw = command([*args, f"{url}/login"], "LAN login probe")
        body.seek(0)
        html = body.read().decode("utf-8", errors="replace")
    status, separator, final_url = raw.partition("|")
    if not separator:
        raise GatewayError("LAN login probe returned invalid metadata")
    return status, final_url, html


def validate_login_origin(url: str, result: Tuple[str, str, str]) -> None:
    status, final_url, html = result
    if status != "200" or not final_url.startswith(f"{url}/"):
        raise GatewayError(f"LAN login did not finish on {url} with HTTP 200")
    if url not in html:
        raise GatewayError(f"LAN login HTML has no canonical {url} origin")
    other_scheme = "http://" if url.startswith("https://") else "https://"
    wrong_url = other_scheme + url.split("://", 1)[1]
    if wrong_url in html:
        raise GatewayError(f"LAN login HTML contains wrong-scheme own-origin URLs for {url}")


def verify_login(https_url: str, http_url: str, ca: Path) -> None:
    validate_login_origin(https_url, fetch_login(https_url, ca))
    validate_login_origin(
        https_url,
        fetch_login(https_url, ca,
                    ("X-Real-IP: 203.0.113.7", "X-Forwarded-Proto: http")),
    )
    validate_login_origin(http_url, fetch_login(http_url, None))


def enable(ca: Path, *, check_only: bool = False) -> None:
    if not (PROJECT / ".env").is_file():
        raise GatewayError("run scripts/dev-up.sh and scripts/allow-lan-access.sh first")
    env = os.environ.copy()
    # The private listener belongs to this checkout's saved .env, not to
    # ambient Compose variables from another terminal or project.
    for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "NEXTCLOUD_LAN_HOST",
                 "NEXTCLOUD_HTTP_BIND_IP", "NEXTCLOUD_HTTP_PORT"):
        env.pop(name, None)
    compose = compose_args()
    raw = command([*compose, "config", "--format", "json"], "Compose configuration", env=env)
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GatewayError("Compose configuration JSON is invalid") from error
    host, port, http_port, cert, key = gateway_config(config)
    validate_certificate(host, cert, key, ca)
    url = f"https://{host}:{port}"
    if check_only:
        print(f"Nextcloud LAN HTTPS preflight passed: {url}")
        return

    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        saved_ip = read_state()
        old_id = parse_container_id(command([*compose, "ps", "-q", "nextcloud-https"],
                                            "old gateway lookup", env=env))
        old_ip = container_ip(old_id)
        nextcloud_id = parse_container_id(command([*compose, "ps", "-q", "nextcloud"],
                                                  "Nextcloud container lookup", env=env))
        if nextcloud_id is None:
            raise GatewayError("Nextcloud is not running")
        before = system_config(compose, env)
        domains = before.get("trusted_domains", [])
        if not isinstance(domains, list) or host not in domains:
            raise GatewayError("LAN host is absent from trusted_domains; run allow-lan-access.sh")

        command([*compose, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120",
                 "nextcloud-https"], "HTTPS gateway start", env=env)
        new_id = parse_container_id(command([*compose, "ps", "-q", "nextcloud-https"],
                                            "new gateway lookup", env=env))
        new_ip = container_ip(new_id)
        if new_id is None or new_ip is None:
            raise GatewayError("HTTPS gateway has no running container IP")

        current = system_config(compose, env)
        proxies = desired_proxies(current.get("trusted_proxies", []), new_ip,
                                  (saved_ip, old_ip))
        if proxies != current.get("trusted_proxies", []):
            occ_set(compose, env, "trusted_proxies", json.dumps(proxies, separators=(",", ":")),
                    json_type=True)
        if current.get("overwrite.cli.url") != url:
            occ_set(compose, env, "overwrite.cli.url", url)
        updated = system_config(compose, env)
        if updated.get("trusted_proxies") != proxies or updated.get("overwrite.cli.url") != url:
            raise GatewayError("Nextcloud gateway configuration did not persist")
        verify_login(url, f"http://{host}:{http_port}", ca)
        write_state(new_ip)
        print(f"Nextcloud LAN HTTPS ready: {url} (trusted proxy {new_ip})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca-file", type=Path, default=DEFAULT_CA,
                        help="CA certificate trusted by LAN clients")
    parser.add_argument("--check-only", action="store_true",
                        help="validate Compose, bind address, and certificate without Docker changes")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        enable(args.ca_file.expanduser().resolve(), check_only=args.check_only)
    except GatewayError as error:
        print(f"Nextcloud LAN HTTPS: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
