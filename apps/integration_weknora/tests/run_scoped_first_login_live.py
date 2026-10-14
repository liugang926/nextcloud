#!/usr/bin/env python3
"""Bounded owned NC + actual Samba LDAPS first-login/production-router acceptance.

No runtime provisioning, LDAP/tenant-membership shortcuts, model fallback, or
changes to existing container networks. Run only inside an allocated actor slot.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlparse

OWNER = "auto-space-prd-20261010"
NC = "nextcloud-auto-space-prd-test-20261010"
AD = "weknora-auto-space-ad-20261010"
TEST = "TestNextcloudScopedFirstLoginActualLDAPSAndProductionRoutes"


def private_file(path):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Descriptor must be a regular private file (0600)")
    return path.resolve(strict=True)


def inspect(name):
    result = subprocess.run(["docker", "inspect", name], check=True, timeout=15,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return json.loads(result.stdout)[0]


def require_owned(details, expected_name, running=True):
    if details["Name"] != "/" + expected_name or details["Config"].get("Labels", {}).get("codex.task") != OWNER:
        raise RuntimeError("Unowned runtime container")
    if any(details["HostConfig"].get("PortBindings", {}).values()):
        raise RuntimeError("Existing fixture must not publish host ports")
    if running and not details["State"]["Running"]:
        raise RuntimeError("Required existing owned runtime is not running")


def run(argv, timeout=45, output=None):
    # Caller never puts passwords, tokens, or model keys into argv.
    return subprocess.run(argv, check=True, timeout=timeout, stdout=output or subprocess.PIPE,
                          stderr=subprocess.STDOUT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--ldap-private-config", required=True, type=Path,
                        help="0600 JSON: ServerURL, ServerName, BindDN, BindPassword, BaseDN, UserBaseDN, GroupBaseDN, LoginIdentifier, LoginPassword")
    parser.add_argument("--model-config", required=True, type=Path, help="Existing approved 0600 model export; only its real embedding row is used")
    parser.add_argument("--model-allowed-host", action="append", default=[])
    parser.add_argument("--direct-network", action="store_true")
    parser.add_argument("--runtime-seconds", type=int, default=480, help="Allocated total Go actor budget including compilation; 300-540 seconds")
    args = parser.parse_args()
    if not 300 <= args.runtime_seconds <= 540:
        parser.error("--runtime-seconds must be between 300 and 540")
    if not args.model_allowed_host:
        parser.error("At least one approved --model-allowed-host is required")
    os.umask(0o077)
    source = args.source_repo.resolve(strict=True)
    if not (source / "internal/router/nextcloud_scoped_first_login_live_test.go").is_file():
        raise RuntimeError("Apply the WK private test patch before running")
    ldap_path = private_file(args.ldap_private_config)
    model_path = private_file(args.model_config)
    ldap = json.loads(ldap_path.read_bytes())
    expected = ("ServerURL", "ServerName", "BindDN", "BindPassword", "BaseDN", "UserBaseDN", "GroupBaseDN", "LoginIdentifier", "LoginPassword")
    if any(not isinstance(ldap.get(key), str) or not ldap[key] or len(ldap[key]) > 4096 for key in expected):
        raise ValueError("Incomplete actual LDAP descriptor")
    endpoint = urlparse(ldap["ServerURL"])
    if endpoint.scheme != "ldaps" or endpoint.port != 636 or endpoint.hostname != ldap["ServerName"] or endpoint.username or endpoint.password:
        raise ValueError("Verified actual LDAPS hostname on port 636 required")
    if ldap["LoginIdentifier"] != "prd-alice":
        raise ValueError("This fixture requires the actual existing prd-alice reader")
    nc, ad = inspect(NC), inspect(AD)
    require_owned(nc, NC); require_owned(ad, AD)
    nc_networks, ad_networks = nc["NetworkSettings"]["Networks"], ad["NetworkSettings"]["Networks"]
    nc_choices = [name for name, value in nc_networks.items()
                  if "nextcloud" in (value.get("Aliases") or [])]
    ad_choices = [name for name, value in ad_networks.items()
                  if endpoint.hostname in (value.get("Aliases") or [])]
    if not nc_choices or not ad_choices:
        raise RuntimeError("Existing owned NC/AD DNS aliases are unavailable")
    primary = "weknora-prd-live-processing" if "weknora-prd-live-processing" in nc_choices else sorted(nc_choices)[0]
    ad_network = primary if primary in ad_choices else sorted(ad_choices)[0]
    nonce = secrets.token_hex(6)
    actor = "weknora-prd-scoped-first-login-" + nonce
    nc_transport = "/tmp/weknora-scoped-first-login-" + nonce + ".json"
    nc_script = "/tmp/scoped_first_login_live_runtime_" + nonce + ".php"
    script = Path(__file__).with_name("scoped_first_login_live_runtime.php")
    private = Path(tempfile.mkdtemp(prefix="prd-scoped-first-login-"))
    fixture = private / "fixture.json"
    evidence = args.evidence.resolve(); evidence.mkdir(parents=True, exist_ok=True)
    command = ["docker", "exec", "--user", "www-data", "-e", "WEKNORA_PRD_TEST_RUNTIME=1",
               "-e", "WEKNORA_DEV_ALLOW_UNVERIFIED_IDENTITY=0",
               "-e", "WEKNORA_SCOPED_FIRST_LOGIN_ACTOR=" + actor,
               "-e", "WEKNORA_SCOPED_FIRST_LOGIN_TRANSPORT=" + nc_transport,
               NC, "php", nc_script]
    log_path = private / "go-runtime.log"
    process = None
    prepared = False
    success = False
    runtime_log = None
    try:
        for suffix, path in ((".ldap", ldap_path), (".models", model_path)):
            shutil.copyfile(path, Path(str(fixture) + suffix)); Path(str(fixture) + suffix).chmod(0o600)
        run(["docker", "cp", AD + ":/var/lib/samba/private/tls/ca.pem", str(fixture) + ".ca"])
        Path(str(fixture) + ".ca").chmod(0o600)
        run(["docker", "cp", str(script), NC + ":" + nc_script])
        # A partial prepare may have created an owned descriptor, so cleanup
        # is attempted even if this command fails.
        prepared = True
        with (evidence / "nc-prepare.jsonl").open("wb") as output:
            run(command + ["prepare"], timeout=60, output=output)
        run(["docker", "cp", NC + ":" + nc_transport, str(fixture)]); fixture.chmod(0o600)
        initial = json.loads(fixture.read_bytes())
        if initial["Actor"] != actor or initial["Directory"] != "prd-samba-ad":
            raise RuntimeError("Actual fixture identity scope mismatch")
        env = []
        if args.direct_network:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env += ["-e", key + "="]
        # Network attachment happens before the binary starts. Only this newly
        # created actor is connected; existing NC/AD surfaces are unchanged.
        test_timeout = min(args.runtime_seconds - 30, 420)
        shell = ('while [ ! -f /fixture/network-ready ]; do sleep 0.1; done; '
                 'exec timeout ' + str(args.runtime_seconds) + 's go test -p 1 -tags=sqlite,sqlite_fts5 '
                 './internal/router -run "^' + TEST + '$" -count=1 -v -timeout=' + str(test_timeout) + 's')
        runtime_log = log_path.open("wb")
        process = subprocess.Popen(["docker", "run", "--rm", "--name", actor, "--label", "codex.task=" + OWNER,
            "--cpus=1", "--memory=2g", "--network", primary,
            "-v", str(source) + ":/app:ro", "-v", str(private) + ":/fixture",
            "-v", "weknora-go-mod:/go/pkg/mod", "-v", "weknora-go-build:/root/.cache/go-build",
            "-w", "/app", "-e", "GOMAXPROCS=1", "-e", "RETRIEVE_DRIVER=sqlite",
            "-e", "SSRF_WHITELIST=" + ",".join(args.model_allowed_host),
            "-e", "WEKNORA_SCOPED_FIRST_LOGIN_FIXTURE=/fixture/fixture.json", *env,
            "weknora-go-test:1.26-sqlite", "sh", "-c", shell], stdout=runtime_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + args.runtime_seconds
        started = None
        while started is None:
            if process.poll() is not None: raise RuntimeError("Owned Go actor failed to start")
            try: started = inspect(actor)
            except subprocess.CalledProcessError: pass
            if time.monotonic() > deadline: raise TimeoutError("Owned actor startup budget exhausted")
            if started is None: time.sleep(0.2)
        require_owned(started, actor)
        if ad_network != primary: run(["docker", "network", "connect", ad_network, actor])
        (private / "network-ready").touch(mode=0o600)

        def wait_marker(suffix):
            path = Path(str(fixture) + suffix)
            while not path.exists():
                if process.poll() is not None: raise RuntimeError("Actual WK stage failed; private Go log retained")
                if time.monotonic() > deadline: raise TimeoutError("Allocated actual WK actor budget exhausted")
                time.sleep(0.2)
            return path

        ready = json.loads(wait_marker(".ready").read_bytes())
        if not ready.get("actual_ldaps_snapshot") or ready["reader_guid"] != initial["ReaderGUID"]:
            raise RuntimeError("Actual default LDAP adapter synchronization not proven")
        with (evidence / "nc-commit.jsonl").open("wb") as output:
            run(command + ["commit"], timeout=min(90, max(1, deadline-time.monotonic())), output=output)
        run(["docker", "cp", NC + ":" + nc_transport, str(fixture)]); fixture.chmod(0o600)
        Path(str(fixture) + ".committed").touch(mode=0o600)
        positive = json.loads(wait_marker(".positive").read_bytes())
        if not positive.get("actual_ldaps_login") or positive.get("ordinary_membership_count") != 0 or not positive.get("scoped_only"):
            raise RuntimeError("Actual first-login assertions missing")
        with (evidence / "nc-revoke.jsonl").open("wb") as output:
            run(command + ["revoke"], timeout=min(60, max(1, deadline-time.monotonic())), output=output)
        Path(str(fixture) + ".revoked").touch(mode=0o600)
        result = json.loads(wait_marker(".result").read_bytes())
        if process.wait(timeout=max(1, deadline-time.monotonic())) != 0:
            raise RuntimeError("Actual WK first-login test failed")
        if result.get("status") != "PASS" or result.get("known_revoke_http") != 403 or result.get("lease_count") != 0:
            raise RuntimeError("Strict known-revocation acceptance failed; 503 is not PASS")
        (evidence / "scoped-first-login-result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        success = True
    finally:
        cleanup_error = None
        try:
            if process is not None and process.poll() is None:
                try:
                    details = inspect(actor)
                except subprocess.CalledProcessError:
                    # --rm may have finished between poll and inspect.
                    process.wait(timeout=15)
                else:
                    require_owned(details, actor, running=False)
                    if details["State"]["Running"]: run(["docker", "stop", "-t", "3", actor], timeout=15)
                    process.wait(timeout=15)
        except Exception as error:
            cleanup_error = error
        finally:
            if runtime_log is not None: runtime_log.close()
            try:
                if prepared:
                    with (evidence / "nc-cleanup.jsonl").open("wb") as output:
                        run(command + ["cleanup"], timeout=60, output=output)
                run(["docker", "exec", NC, "rm", "-f", nc_script], timeout=15)
            except Exception as error:
                cleanup_error = cleanup_error or error
            finally:
                # Always erase copied passwords, model keys and machine tokens,
                # including when NC cleanup itself fails. Raw application logs
                # remain private; they never become repository evidence.
                for name in private.iterdir():
                    if name != log_path:
                        if name.is_dir(): shutil.rmtree(name)
                        else: name.unlink()
                if success and cleanup_error is None: shutil.rmtree(private)
                else: print(json.dumps({"status":"FAILED", "private_log":str(log_path)}, sort_keys=True))
        if cleanup_error is not None: raise cleanup_error
    print(json.dumps({"status":"PASS", "operation_id":initial["Operation"], "reader_guid":result["reader_guid"], "user_id":result["user_id"], "body_sha256":result["body_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except Exception as error:
        # Do not stringify subprocesses/descriptors: stderr and argv may grow
        # sensitive in future edits. Retained private diagnostics are explicit.
        print(json.dumps({"status":"FAILED", "kind":type(error).__name__}, sort_keys=True))
        raise SystemExit(1)
