#!/usr/bin/env python3
"""Root-reviewed source template. No resources exist until --execute.

Precompile is a separate bounded phase. Runtime's one coordinated window starts
before any runtime resource preparation. Failure retains owned actors/disk.
Shared caches are observed/mounted only; this driver never creates or deletes them.
"""
import argparse
import ast
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

IMAGE = "sha256:5a74e8b5137496d3e22c72c01d862c4cbb1fe6ce476ddf5616fd9a807d6ae9ac"
LABEL = "com.nextcloud.oskill.owner"
STAGE = "com.nextcloud.oskill.stage"
MEMORY = 3 * 1024**3
CPU = 1000000000
MOD = "weknora-go-mod"
BUILD = "weknora-go-build"
BINARY = "/owned-temp/bin/service-oskill.test"
WORKDIR = "/src/internal/application/service"
HELPER = "internal/application/service/model_original_download_os_kill_actual_test.go"
ENTRIES = {"TestOriginalLocalDownloadCrashChild", "TestOriginalLocalDownloadOSKillActualSQLite"}
GO_ENV = {"GOMEMLIMIT": "2GiB", "GOMAXPROCS": "1", "GOTOOLCHAIN": "local",
          "GOPROXY": "off", "GOCACHE": "/root/.cache/go-build", "GOMODCACHE": "/go/pkg/mod",
          "TMPDIR": "/owned-temp/tmp", "GOTMPDIR": "/owned-temp/go-tmp", "CGO_ENABLED": "1", "GOGC": "50"}
ENTRYPOINT = ["/bin/sh"]
COMMAND = ["-c", "exec sleep infinity"]
MAX_RAW = 256 * 1024**2


class Refused(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def private_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with open(path, "xb") as output:
        output.write(raw)
    os.chmod(path, 0o600)
    return digest(raw)


def private_input(path, expected):
    path = pathlib.Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_mode & 0o077 == 0,
            "unsafe bound private input file")
    require(sha_file(path) == expected, "bound private input SHA mismatch")
    return path


class Clock:
    def __init__(self, seconds, deadline_unix=None):
        self.wall_started = time.time()
        self.mono_started = time.monotonic()
        require(deadline_unix is None or math.isfinite(deadline_unix), "invalid coordinated UNIX deadline")
        self.unix = min(self.wall_started + seconds, deadline_unix if deadline_unix is not None else math.inf)
        require(math.isfinite(self.unix) and self.unix > self.wall_started, "coordinated deadline exhausted")
        self.mono_end = self.mono_started + min(seconds, self.unix - self.wall_started)

    def remaining(self):
        value = min(self.unix - time.time(), self.mono_end - time.monotonic())
        require(value > 0, "fixed coordinated window exhausted")
        return value

    def timeout(self, maximum=30):
        return min(maximum, self.remaining())


def command(argv, clock=None, maximum=30):
    environment = os.environ.copy()
    if argv[0] == "git":
        for key in ["GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"]:
            environment.pop(key, None)
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=clock.timeout(maximum) if clock else maximum, check=False, env=environment)
    require(result.returncode == 0, "private command failed: " + pathlib.Path(argv[0]).name)
    return result.stdout


def inspect(kind, identity, clock=None):
    value = json.loads(command(["docker", kind, "inspect", identity], clock))
    require(len(value) == 1, "ambiguous owned resource inspection")
    return value[0]


def env_map(obj):
    pairs = (item.split("=", 1) for item in obj["Config"].get("Env", []))
    return {key: value for key, value in pairs}


def guard_volume(obj, owner):
    require(obj["Name"] == owner["volume"] and obj.get("Labels", {}).get(LABEL) == owner["nonce"],
            "owned volume name/nonce mismatch")
    require(obj.get("Driver") == "local" and obj.get("Scope") == "local" and not obj.get("Options"),
            "owned workspace is not plain local disk volume")
    if "volume_identity" in owner:
        require({k: obj.get(k) for k in ["CreatedAt", "Mountpoint", "Driver", "Scope"]} == owner["volume_identity"],
                "owned disk identity drift")


def guard_actor(obj, owner, actor, running=None):
    require(obj["Id"] == actor["id"] and obj["Name"].lstrip("/") == actor["name"], "exact actor identity mismatch")
    labels = obj["Config"].get("Labels", {})
    require(labels.get(LABEL) == owner["nonce"] and labels.get(STAGE) == actor["stage"] and
            labels.get("com.nextcloud.oskill.source-head") == owner["head"] and
            labels.get("com.nextcloud.oskill.source-tree") == owner["tree"], "actor source/nonce labels mismatch")
    require(obj["Image"] == IMAGE, "pinned Go image mismatch")
    cfg = obj["HostConfig"]
    require(cfg.get("Memory") == MEMORY and cfg.get("MemorySwap") == MEMORY and
            cfg.get("NanoCpus") == CPU and cfg.get("NetworkMode") == "none" and not cfg.get("Tmpfs"), "actor budget/network drift")
    require(obj["Config"].get("Entrypoint") == ENTRYPOINT and obj["Config"].get("Cmd") == COMMAND,
            "actor entrypoint/command mismatch")
    expected = {"/src": ("bind", owner["source"], False),
                "/go/pkg/mod": ("volume", MOD, False),
                "/root/.cache/go-build": ("volume", BUILD, True),
                "/owned-temp": ("volume", owner["volume"], True)}
    mounts = {entry["Destination"]: entry for entry in obj["Mounts"]}
    require(set(mounts) == set(expected), "unexpected actor mount scope")
    for destination, (kind, source, rw) in expected.items():
        mount = mounts[destination]
        require(mount["Type"] == kind and (mount.get("Name") if kind == "volume" else mount.get("Source", "").removeprefix("/host_mnt")) == source and
                mount["RW"] is rw, "actor source/cache/temp mount drift")
    values = env_map(obj)
    require(all(values.get(key) == value for key, value in GO_ENV.items()), "Go environment budget/cache drift")
    if "fingerprint" in actor:
        require(digest(json.dumps({"env": obj["Config"].get("Env"), "mounts": obj["Mounts"],
                                  "entry": obj["Config"].get("Entrypoint"), "cmd": obj["Config"].get("Cmd")},
                                 sort_keys=True).encode()) == actor["fingerprint"], "actor immutable environment/mount drift")
    if running is not None:
        require(obj["State"]["Running"] is running, "actor running state mismatch")
    return obj


def linux_identity(identity, clock):
    # PID 1 is the actual container init; no host/guest PID is guessed. Capture
    # executable and proc start fields from Linux, not docker exec's own PID.
    cmdline = command(["docker", "exec", identity, "cat", "/proc/1/cmdline"], clock)
    procstat = command(["docker", "exec", identity, "cat", "/proc/1/stat"], clock).decode()
    fields = procstat.rsplit(")", 1)[1].split()
    require(cmdline.split(b"\0")[:2] == [b"sleep", b"infinity"], "init is not the owned idle process")
    return {"pid": 1, "start_ticks": fields[19], "process_group": fields[2],
            "argv_sha256": digest(cmdline),
            "exe_sha256": command(["docker", "exec", identity, "sha256sum", "/proc/1/exe"], clock).decode().split()[0]}


def create_actor(owner, stage, clock):
    actor = {"stage": stage, "name": "nc-oskill-" + owner["nonce"][:12] + "-" + stage}
    argv = ["docker", "create", "--name", actor["name"], "--label", LABEL + "=" + owner["nonce"],
            "--label", STAGE + "=" + stage, "--label", "com.nextcloud.oskill.source-head=" + owner["head"],
            "--label", "com.nextcloud.oskill.source-tree=" + owner["tree"], "--memory", str(MEMORY),
            "--memory-swap", str(MEMORY), "--cpus", "1", "--network", "none", "--workdir", "/src",
            "--entrypoint", "/bin/sh", "--mount", "type=bind,src=" + owner["source"] + ",dst=/src,readonly",
            "--mount", "type=volume,src=" + MOD + ",dst=/go/pkg/mod,readonly",
            "--mount", "type=volume,src=" + BUILD + ",dst=/root/.cache/go-build",
            "--mount", "type=volume,src=" + owner["volume"] + ",dst=/owned-temp"]
    for key, value in GO_ENV.items():
        argv += ["--env", key + "=" + value]
    argv += [IMAGE, *COMMAND]
    actor["id"] = command(argv, clock).decode().strip()
    require(re.fullmatch(r"[0-9a-f]{64}", actor["id"]) is not None, "Docker did not return exact actor ID")
    owner["actors"].append(actor)  # retain actual returned ID even if later guard refuses
    initial = inspect("container", actor["id"], clock)
    actor["fingerprint"] = digest(json.dumps({"env": initial["Config"].get("Env"), "mounts": initial["Mounts"],
                                             "entry": initial["Config"].get("Entrypoint"), "cmd": initial["Config"].get("Cmd")}, sort_keys=True).encode())
    guard_actor(initial, owner, actor, False)
    command(["docker", "start", actor["id"]], clock)
    guard_actor(inspect("container", actor["id"], clock), owner, actor, True)
    actor["linux_init"] = linux_identity(actor["id"], clock)
    return actor


def guard_live(owner, actor, clock):
    guard_volume(inspect("volume", owner["volume"], clock), owner)
    value = guard_actor(inspect("container", actor["id"], clock), owner, actor, True)
    require(linux_identity(actor["id"], clock) == actor["linux_init"], "owned init PID/start/executable drift")
    return value


def stop_owned(owner):
    # Cleanup has its own bounded stop/join grace. It never continues business
    # outside an exhausted 480/1200 window, and never targets an unverified ID.
    records = []
    if owner.get("volume"):
        guard_volume(inspect("volume", owner["volume"]), owner)
    for actor in owner.get("actors", []):
        current = guard_actor(inspect("container", actor["id"]), owner, actor)
        if current["State"]["Running"]:
            if "linux_init" in actor:
                cleanup_clock = Clock(15)
                require(linux_identity(actor["id"], cleanup_clock) == actor["linux_init"],
                        "owned init generation changed before cleanup; refuse signal")
            command(["docker", "stop", "--time", "10", actor["id"]], maximum=20)
        terminal = guard_actor(inspect("container", actor["id"]), owner, actor, False)
        records.append({"id": actor["id"], "running": False, "exit_code": terminal["State"]["ExitCode"],
                        "oom_killed": terminal["State"].get("OOMKilled", False)})
    return records


def missing(kind, identity):
    proc = subprocess.run(["docker", kind, "inspect", identity], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    message = proc.stderr.decode(errors="replace").strip()
    recognized = (["Error: No such container: " + identity, "Error response from daemon: No such container: " + identity]
                  if kind == "container" else ["Error response from daemon: get " + identity + ": no such volume", "Error: No such volume: " + identity])
    return proc.returncode != 0 and message in recognized


def remove_stopped(owner):
    for actor in owner["actors"]:
        guard_actor(inspect("container", actor["id"]), owner, actor, False)
        command(["docker", "rm", actor["id"]])
        require(missing("container", actor["id"]), "removed exact actor absence not independently verified")
    guard_volume(inspect("volume", owner["volume"]), owner)
    command(["docker", "volume", "rm", owner["volume"]])
    require(missing("volume", owner["volume"]), "removed exact volume absence not independently verified")


def run_private(argv, log, clock, owner, actor):
    with open(log, "xb") as output:
        child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        # This is the actual owned Popen handle. Never signal a PID reconstructed
        # from a guessed ps record, and do not mistake fast terminal for missing.
        try:
            host = {"pid": child.pid, "group": None, "start": None}
            if child.poll() is None:
                try:
                    host["group"] = os.getpgid(child.pid)
                    host["start"] = command(["ps", "-p", str(child.pid), "-o", "lstart="], clock, maximum=5).decode().strip()
                    require(host["group"] == child.pid and host["start"], "live owned child identity unavailable")
                except (ProcessLookupError, Refused):
                    require(child.poll() is not None, "live owned child identity capture refused")
                    host["actual_fast_terminal"] = child.wait()
            private_json(log.with_suffix(".host-process.json"), host)
            while child.poll() is None:
                clock.remaining()
                guard_live(owner, actor, clock)
                require(log.stat().st_size <= MAX_RAW, "private raw log cap exceeded")
                time.sleep(min(2, clock.timeout(2)))
            status = child.wait()
            clock.remaining()  # late terminal cannot be accepted after the deadline
            return status
        except BaseException:
            # Stop entire exact owned container before joining dockerexec. This
            # cannot leave a Go/child producer running after the driver fails.
            try:
                stop_owned(owner)
            except BaseException:
                # Ownership drift refuses a container signal, but we must still
                # join our known host Popen handle and preserve that refusal.
                pass
            if child.poll() is None:
                child.terminate()  # actual still-owned Popen handle only
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=10)
            raise
        finally:
            os.chmod(log, 0o600)


def binding(args):
    require(re.fullmatch(r"[0-9a-f]{40}", args.source_head) is not None and
            re.fullmatch(r"[0-9a-f]{40}", args.source_tree) is not None, "require full actual source HEAD/tree")
    require(re.fullmatch(r"[0-9a-f]{64}", args.runner_sha256) is not None, "require full final runner SHA")
    source = pathlib.Path(args.source).resolve(strict=True)
    require(source.is_dir() and "," not in str(source), "unsafe source bind path")
    runner = private_input(args.runner, args.runner_sha256).resolve()
    raw = runner.read_text()
    ast.parse(raw)
    require("--deadline-unix" in raw and "NC_POLICY_COORDINATED_DEADLINE_UNIX" in raw,
            "final runner coordinated deadline ABI missing")
    require(command(["git", "-C", str(source), "rev-parse", "HEAD"], maximum=15).decode().strip() == args.source_head,
            "source HEAD mismatch")
    require(command(["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], maximum=15).decode().strip() == args.source_tree,
            "source tree mismatch")
    require(not command(["git", "-C", str(source), "status", "--porcelain=v1", "--untracked-files=all"], maximum=15),
            "source checkout is dirty")
    helper = (source / HELPER).read_bytes()
    require(b"NC_POLICY_COORDINATED_DEADLINE_UNIX" in helper, "compiled helper coordinated deadline ABI missing")
    return source, runner, digest(helper)


def actual_binary(owner, actor, clock):
    guard_live(owner, actor, clock)
    value = command(["docker", "exec", actor["id"], "sha256sum", BINARY], clock).decode().split()[0]
    info = command(["docker", "exec", actor["id"], "stat", "-c", "%s %a %h", BINARY], clock).decode().split()
    require(re.fullmatch(r"[0-9a-f]{64}", value) is not None and 0 < int(info[0]) <= 1024**3 and
            int(info[2]) == 1 and int(info[1], 8) & 0o077 == 0, "actual binary SHA/stat guard failed")
    listing = command(["docker", "exec", "-w", WORKDIR, actor["id"], BINARY,
                       "-test.list=^TestOriginalLocalDownload(CrashChild|OSKillActualSQLite)$"], clock).decode().splitlines()
    require(ENTRIES.issubset(set(listing)), "precompiled Linux binary helper entries missing")
    return {"sha256": value, "bytes": int(info[0]), "mode": info[1], "nlink": int(info[2]), "test_entries": sorted(ENTRIES)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["precompile", "runtime"], required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--compile-receipt")
    parser.add_argument("--compile-receipt-sha256")
    parser.add_argument("--deadline-unix", type=float)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"classification": "source-only; no commands/resources", "pinned_go_image": IMAGE,
                          "phase": args.phase, "precompile_seconds": 480, "runtime_seconds": 1200,
                          "runtime_deadline_starts_before_resource_prepare": True}))
        return 0
    os.umask(0o077)
    if args.phase == "runtime":
        require(args.deadline_unix is not None, "runtime requires Root deadline before prepare")
    # Monotonic watchdog starts before source/preflight/resource preparation.
    clock = Clock(480 if args.phase == "precompile" else 1200, args.deadline_unix)
    source, runner, helper_sha = binding(args)
    out = pathlib.Path(args.evidence)
    require(out.is_absolute() and not out.exists(), "require fresh absolute private evidence directory")
    out.mkdir(mode=0o700, parents=False)
    owner = {"nonce": uuid.uuid4().hex, "head": args.source_head, "tree": args.source_tree,
             "source": str(source), "actors": []}
    result = {"phase": args.phase, "source_head": args.source_head, "source_tree": args.source_tree,
              "helper_sha256": helper_sha, "runner_sha256": args.runner_sha256, "accepted": False}
    try:
        running_go = command(["docker", "ps", "-q", "--no-trunc", "--filter", "ancestor="+IMAGE], clock)
        require(not running_go.strip(), "foreign pinned Go actor is live; serialize the window")
        if args.phase == "precompile":
            require(shutil.disk_usage(source).free >= 12*1024**3, "precompile disk headroom below12GiB")
            image = inspect("image", IMAGE, clock)
            require(image["Id"] == IMAGE and image.get("Os") == "linux", "pinned Linux Go image absent")
            for shared in [MOD, BUILD]:
                require(inspect("volume", shared, clock)["Name"] == shared, "shared cache absent")
            owner["volume"] = "nc-oskill-" + owner["nonce"][:12] + "-temp"
            command(["docker", "volume", "create", "--label", LABEL + "=" + owner["nonce"], owner["volume"]], clock)
            volume = inspect("volume", owner["volume"], clock); guard_volume(volume, owner)
            owner["volume_identity"] = {k: volume.get(k) for k in ["CreatedAt", "Mountpoint", "Driver", "Scope"]}
            actor = create_actor(owner, "compile", clock)
            command(["docker", "exec", actor["id"], "mkdir", "-m", "700", "-p", "/owned-temp/bin", "/owned-temp/tmp", "/owned-temp/go-tmp"], clock)
            private_json(out / "owner.initial.private.json", owner)
            compile_argv = ["docker", "exec", "-w", "/src", actor["id"], "go", "test", "-buildvcs=false",
                            "-tags", "sqlite_fts5", "-p", "1", "-c", "-o", BINARY, "./internal/application/service"]
            status = run_private(compile_argv, out / "precompile.private.log", clock, owner, actor)
            require(status == 0, "precompile actual nonzero terminal")
            command(["docker", "exec", actor["id"], "chmod", "700", BINARY], clock)
            binary = actual_binary(owner, actor, clock)
            command(["docker", "cp", actor["id"] + ":" + BINARY, str(out / "service-oskill.test")], clock)
            os.chmod(out / "service-oskill.test", 0o600)
            require(sha_file(out / "service-oskill.test") == binary["sha256"], "retained precompile binary mismatch")
            stops = stop_owned(owner)
            clock.remaining()
            require(not any(row["oom_killed"] for row in stops), "precompile actor OOM")
            receipt = {"classification": "ACTUAL_PRECOMPILE_ONLY_NO_BUSINESS", "owner": owner, "binary": binary,
                       "source_head": args.source_head, "source_tree": args.source_tree, "helper_sha256": helper_sha,
                       "runner_sha256": args.runner_sha256, "raw_sha256": sha_file(out / "precompile.private.log"),
                       "stopped": stops, "elapsed_seconds": time.monotonic()-clock.mono_started, "terminal": 0}
            receipt_sha = private_json(out / "compile-receipt.metadata.json", receipt)
            result.update(terminal=0, compile_receipt_sha256=receipt_sha, runtime_accepted=False,
                          workspace_retained_for_runtime=True, accepted=False)
        else:
            require(args.deadline_unix is not None, "runtime requires Root deadline set before resource prepare")
            require(args.compile_receipt and args.compile_receipt_sha256, "runtime requires exact actual precompile receipt")
            receipt_path = private_input(args.compile_receipt, args.compile_receipt_sha256)
            receipt = json.loads(receipt_path.read_text())
            require(receipt["terminal"] == 0 and receipt["source_head"] == args.source_head and
                    receipt["source_tree"] == args.source_tree and receipt["helper_sha256"] == helper_sha and
                    receipt["runner_sha256"] == args.runner_sha256, "precompile/source/helper/runner binding mismatch")
            owner = receipt["owner"]
            require(owner["source"] == str(source), "precompile source path differs")
            for previous in owner["actors"]:
                guard_actor(inspect("container", previous["id"], clock), owner, previous, False)
            guard_volume(inspect("volume", owner["volume"], clock), owner)
            actor = create_actor(owner, "runtime", clock)
            binary = actual_binary(owner, actor, clock)
            require(binary == receipt["binary"], "runtime binary SHA/stat differs from precompile")
            private_json(out / "owner.initial.private.json", owner)
            run_dir = "/owned-temp/run/" + owner["nonce"]
            runner_out = out / "actual-parent"
            argv = [sys.executable, str(runner), "--container-id", actor["id"], "--nonce", owner["nonce"],
                    "--owner-label-key", LABEL, "--source-head", args.source_head, "--source-tree", args.source_tree,
                    "--deadline-unix", format(clock.unix, ".9f"), "--binary-path", BINARY,
                    "--binary-sha256", binary["sha256"], "--workdir", WORKDIR,
                    "--run-dir", run_dir, "--private-output", str(runner_out)]
            status = run_private(argv, out / "owned-runner.private.log", clock, owner, actor)
            require(status == 0, "actual OSKill parent/runner failed")
            terminal = json.loads((runner_out / "runner.result.metadata.json").read_text())
            require(terminal["exit_code"] == 0 and terminal["binary_sha256"] == binary["sha256"] and
                    not terminal.get("owner_oom_killed"), "runtime terminal evidence failed")
            killed = json.loads((runner_out / "kill.terminal.private.json").read_text())
            recovered = json.loads((runner_out / "result.verified.private.json").read_text())
            before = json.loads((runner_out / "before-kill.facts.private.json").read_text())
            require(killed["signal"] == 9 and killed["process_terminal"] and recovered["ResultID"] and
                    recovered["Attempts"] == 2 and len(recovered["Owners"]) == 4 and len(recovered["Leases"]) == 4 and
                    all(row["ReleasedAtMS"] is not None for row in recovered["Leases"]) and
                    recovered["OriginalFactsSHA"] == before["OriginalFactsSHA"] and
                    recovered["OriginalBodiesSHA"] == before["OriginalBodiesSHA"], "actual crash/recovery/lease proof incomplete")
            clock.remaining(); guard_live(owner, actor, clock)
            live_helpers = command(["docker", "exec", actor["id"], "/bin/sh", "-c",
                                    'for p in /proc/[0-9]*; do if [ "$(readlink "$p/exe")" = "/owned-temp/bin/service-oskill.test" ]; then echo "${p##*/}"; fi; done'], clock)
            require(not live_helpers.strip(), "actual helper process remains after parent terminal")
            # Preserve complete owned disk bytes before stopping/removing it.
            command(["docker", "cp", actor["id"] + ":/owned-temp", str(out / "owned-temp.retained")], clock, maximum=60)
            os.chmod(out / "owned-temp.retained", 0o700)
            for entry in (out / "owned-temp.retained").rglob("*"):
                require(not entry.is_symlink(), "unexpected symlink in owned retained artifacts")
                os.chmod(entry, 0o700 if entry.is_dir() else 0o600)
            stops = stop_owned(owner)
            require(not any(row["oom_killed"] for row in stops), "runtime owned actor OOM")
            remove_stopped(owner)
            clock.remaining()
            result.update(terminal=0, accepted=True, scope="actual SQLite OSKill only",
                          source_head=args.source_head, source_tree=args.source_tree, binary=binary,
                          coordinated_deadline_unix=clock.unix, elapsed_seconds=time.monotonic()-clock.mono_started,
                          all_exact_owned_resources_removed_and_absence_verified=True, shared_caches_untouched=True)
        private_json(out / "wrapper.result.metadata.json", result)
        print(json.dumps({"phase": args.phase, "terminal": 0, "accepted": result["accepted"],
                          "result_sha256": sha_file(out / "wrapper.result.metadata.json")}))
        return 0
    except BaseException as error:
        result.update(terminal=1, accepted=False, reason=type(error).__name__, failure_retained=True)
        result["actual_owned_state"] = owner
        if owner.get("actors"):
            try:
                result["owned_stop_only"] = stop_owned(owner)
            except BaseException as stop_error:
                result["stop_refused"] = type(stop_error).__name__
        private_json(out / "wrapper.failure.metadata.json", result)
        print(json.dumps({"phase": args.phase, "terminal": 1, "accepted": False,
                          "reason": type(error).__name__, "failure_retained": True}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
