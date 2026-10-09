#!/usr/bin/env python3
"""Observe one marker-owned fixture without changing its services or budgets.

Docker CLI working-set values are rounded; CPU and /proc reads cover different
instants. A 5-second sample can miss short peaks. No historical RSS is invented,
and a watcher terminal never certifies application success or quiescence.
"""
import argparse
import datetime as dt
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import shlex
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
OWNER = runpy.run_path(str(HERE / "synthetic-ldap-fixture.py"))
MARKER_NAME = "resource-watch.json"
ROLES = ("wk-app", "nextcloud")
HEX_ID = re.compile(r"[0-9a-f]{64}\Z")


class WatchError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise WatchError(code)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def private(path, directory=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, "noncanonical_private_path")
    info = path.lstat()
    require(info.st_uid == os.getuid(), "private_path_owner")
    require(stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600), "private_path_mode")
    require(stat.S_ISDIR(info.st_mode) if directory else
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "private_path_type")
    return path


def exclusive_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return raw


def remove_own_marker(path, raw, identity):
    """A replacement, symlink, hard link or changed marker belongs to review."""
    try:
        private(path)
        info = path.lstat()
        if (info.st_dev, info.st_ino) != identity or path.read_bytes() != raw:
            return False
        path.unlink()
        return True
    except (FileNotFoundError, WatchError):
        return False


def size_bytes(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB|kB|MB|GB|TB)", value.strip())
    require(match is not None, "invalid_docker_memory_unit")
    powers = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
              "TiB": 1024**4, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    return int(Decimal(match[1]) * powers[match[2]])


def stats_value(value, container_id):
    short = value.get("ID", "")
    require(re.fullmatch(r"[0-9a-f]{12,64}", short) is not None and
            container_id.startswith(short), "docker_stats_container_identity")
    parts = value.get("MemUsage", "").split("/")
    require(len(parts) == 2, "invalid_docker_memory_usage")
    cpu = value.get("CPUPerc", "")
    require(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%", cpu) is not None, "invalid_docker_cpu_percent")
    return {"working_set_bytes_rounded": size_bytes(parts[0]),
            "memory_limit_bytes_rounded": size_bytes(parts[1]),
            "memory_usage_display": value["MemUsage"],
            "cpu_percent_of_one_core": float(cpu[:-1]), "cpu_percent_display": cpu,
            "docker_cli_values_rounded": True}


def validate_container(value, state, role, image_id, captured_id=None):
    labels = (value.get("Config") or {}).get("Labels") or {}
    identity = value.get("Id", "")
    require(HEX_ID.fullmatch(identity) is not None, "invalid_container_id")
    require(labels.get(OWNER["OWNER_LABEL"]) == state["owner_token"] and
            labels.get("com.docker.compose.project") == state["project"] and
            labels.get("com.docker.compose.service") == role and
            value.get("Name") == "/" + state["project"] + "-" + role + "-1", "container_owner_role_changed")
    require(value.get("Image") == image_id, "container_image_changed")
    require(captured_id is None or identity == captured_id, "container_id_changed")
    return identity


def probe_script(proc_root="/proc", cgroup_root="/sys/fs/cgroup"):
    # No cmdline, environ or content files are read. Name is checked again from
    # status so a PID reuse between comm and status cannot count an exec helper.
    return r'''proc_root=%s
cgroup_root=%s
for directory in "$proc_root"/[0-9]*; do
  test -f "$directory/comm" || continue
  IFS= read -r comm < "$directory/comm" || continue
  test "$comm" = WeKnora || continue
  pid=${directory##*/}
  start=$(awk '{print $22}' "$directory/stat" 2>/dev/null)
  awk -v pid="$pid" -v start="$start" '
    BEGIN {name="";rss="NA";hwm="NA";threads="NA"}
    /^Name:/ {name=$2} /^VmRSS:/ {rss=$2} /^VmHWM:/ {hwm=$2} /^Threads:/ {threads=$2}
    END {if(name=="WeKnora") printf "PROCESS\t%%s\t%%s\t%%s\t%%s\t%%s\t%%s\n",pid,name,rss,hwm,threads,start}
  ' "$directory/status" 2>/dev/null
done
if test -r "$cgroup_root/memory.events"; then
  awk '{printf "CGROUP\t%%s\t%%s\n",$1,$2}' "$cgroup_root/memory.events"
else
  printf 'CGROUP_UNAVAILABLE\n'
fi
if test -r "$proc_root/meminfo"; then
  awk '/^(MemTotal|MemAvailable|MemFree|SwapTotal|SwapFree):/ {gsub(":","",$1);printf "VM_MEMORY\t%%s\t%%s\n",$1,$2}' "$proc_root/meminfo"
fi
if test -r "$proc_root/stat"; then
  awk '/^cpu / {printf "VM_CPU";for(i=2;i<=NF;i++) printf "\t%%s",$i;printf "\n";exit}' "$proc_root/stat"
fi
''' % (shlex.quote(str(proc_root)), shlex.quote(str(cgroup_root)))


def parse_probe(raw):
    processes, events, memory, ticks = {}, None, {}, None
    for line in raw.decode("ascii").splitlines():
        parts = line.split("\t")
        kind = parts[0]
        if kind == "PROCESS":
            require(len(parts) == 7 and parts[1].isdigit() and parts[2] == "WeKnora", "invalid_proc_process")
            require(parts[1] not in processes, "duplicate_proc_process")
            require(all(x == "NA" or x.isdigit() for x in parts[3:]), "invalid_proc_counter")
            nullable = lambda value: None if value == "NA" else int(value)
            processes[parts[1]] = {"pid": int(parts[1]), "comm": parts[2],
                "vm_rss_bytes": None if parts[3] == "NA" else int(parts[3]) * 1024,
                "vm_hwm_bytes": None if parts[4] == "NA" else int(parts[4]) * 1024,
                "threads": nullable(parts[5]), "start_time_ticks": nullable(parts[6])}
        elif kind == "CGROUP":
            require(len(parts) == 3 and re.fullmatch(r"[a-z_]+", parts[1]) and parts[2].isdigit(), "invalid_cgroup_counter")
            if events is None:
                events = {}
            require(parts[1] not in events, "duplicate_cgroup_counter")
            events[parts[1]] = int(parts[2])
        elif kind == "CGROUP_UNAVAILABLE":
            require(len(parts) == 1, "invalid_cgroup_unavailable")
        elif kind == "VM_MEMORY":
            require(len(parts) == 3 and parts[1] in {"MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"}
                    and parts[2].isdigit(), "invalid_vm_memory")
            memory[parts[1] + "_bytes"] = int(parts[2]) * 1024
        elif kind == "VM_CPU":
            require(len(parts) >= 5 and all(x.isdigit() for x in parts[1:]), "invalid_vm_cpu")
            ticks = [int(x) for x in parts[1:]]
        else:
            raise WatchError("unexpected_proc_probe_record")
    values = list(processes.values())
    return {"weknora_processes": values, "weknora_process_count": len(values),
            "weknora_rss_sum_bytes": sum(x["vm_rss_bytes"] for x in values)
                if values and all(x["vm_rss_bytes"] is not None for x in values) else None,
            "cgroup_memory_events": events, "vm_memory": memory or None, "vm_cpu_ticks": ticks}


def vm_cpu_percent(previous, current):
    if previous is None or current is None or len(previous) != len(current):
        return None
    # Linux guest fields are already included in user/nice; exclude them here.
    changes = [b - a for a, b in zip(previous[:8], current[:8])]
    if any(x < 0 for x in changes) or sum(changes) <= 0:
        return None
    idle = changes[3] + (changes[4] if len(changes) > 4 else 0)
    return 100.0 * (sum(changes) - idle) / sum(changes)


class Observer:
    def __init__(self, scratch, output, interval, duration, stop_on_app_exit):
        self.directory, self.state = OWNER["owned_state"](scratch)
        private(self.directory, True)
        OWNER["assert_owned_resources"](self.directory, self.state)
        self.identity = {k: self.state[k] for k in ("project", "owner_token", "compose_fingerprint",
                                                  "weknora_image", "weknora_image_id")}
        self.config = json.loads((self.directory / "compose.yaml").read_text())
        self.image_ids = {"wk-app": self.state["weknora_image_id"]}
        nc = self.command(["docker", "image", "inspect", self.config["services"]["nextcloud"]["image"]])
        nc = json.loads(nc)
        require(len(nc) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", nc[0]["Id"]), "nextcloud_image_identity")
        self.image_ids["nextcloud"] = nc[0]["Id"]
        self.ids = {}
        self.interval, self.duration = interval, duration
        self.stop_on_app_exit = stop_on_app_exit
        self.stop = threading.Event()
        self.signal_number = None
        self.output = Path(output).resolve() if output else Path(tempfile.mkdtemp(prefix="nc-resource-watch-")).resolve()
        if not self.output.exists():
            self.output.mkdir(mode=0o700)
        private(self.output, True)
        require(self.output != self.directory and self.directory not in self.output.parents,
                "watch_output_inside_fixture_controls")
        require(not list(self.output.iterdir()), "watch_output_not_empty")
        self.marker = self.directory / MARKER_NAME
        self.marker_raw = None
        self.previous_vm_cpu = None
        self.previous_states = {}
        self.vm_info = json.loads(self.command(["docker", "info", "--format", "{{json .}}"] ))
        self.vm_info = {k: self.vm_info.get(k) for k in ("MemTotal", "NCPU", "OSType", "Architecture")}

    def command(self, args, timeout=15):
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
        require(result.returncode == 0, "readonly_docker_command_failed")
        return result.stdout

    def verify(self):
        directory, state = OWNER["owned_state"](self.directory)
        require(directory == self.directory and all(state.get(k) == v for k, v in self.identity.items()),
                "fixture_identity_changed")
        OWNER["assert_owned_resources"](directory, state)

    def container(self, role):
        result = subprocess.run(["docker", "inspect", "--type=container", self.state["project"] + "-" + role + "-1"],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
        if result.returncode:
            # Absence is acceptable before first creation, not after a captured
            # identity disappears. Do not reinterpret an API failure as stopped.
            if role in self.ids:
                raise WatchError("captured_container_missing")
            require(b"No such" in result.stderr, "container_inspection_failed")
            return None
        values = json.loads(result.stdout)
        require(len(values) == 1, "ambiguous_container_identity")
        value = values[0]
        self.ids[role] = validate_container(value, self.state, role, self.image_ids[role], self.ids.get(role))
        return value

    def sample(self):
        started = time.monotonic()
        self.verify()
        inspected = {role: self.container(role) for role in ROLES}
        running = {role: value for role, value in inspected.items() if value and value["State"]["Running"]}
        statistics = {}
        if running:
            raw = self.command(["docker", "stats", "--no-stream", "--format", "{{json .}}",
                                *[v["Id"] for v in running.values()]], timeout=20)
            for line in raw.decode().splitlines():
                row = json.loads(line)
                matching = [role for role in running if self.ids[role].startswith(row.get("ID", "missing"))]
                require(len(matching) == 1, "unexpected_stats_container")
                role = matching[0]
                statistics[role] = stats_value(row, self.ids[role])
        roles, transitions, vm = {}, [], None
        for role, value in inspected.items():
            if value is None:
                roles[role] = {"availability": "not_created_yet"}
                continue
            state, host = value["State"], value["HostConfig"]
            fact = {"container_id": value["Id"], "image_id": value["Image"],
                    "running": state["Running"], "status": state["Status"],
                    "oom_killed": state["OOMKilled"], "exit_code": state["ExitCode"],
                    "started_at": state["StartedAt"], "finished_at": state["FinishedAt"],
                    "health": (state.get("Health") or {}).get("Status"),
                    "restart_count": value.get("RestartCount"),
                    "memory_limit_bytes": host.get("Memory"), "memory_swap_bytes": host.get("MemorySwap"),
                    "nano_cpus": host.get("NanoCpus"), "docker_stats": statistics.get(role)}
            signature = {k: fact[k] for k in ("container_id", "running", "status", "oom_killed", "exit_code", "health", "restart_count")}
            if signature != self.previous_states.get(role):
                transitions.append({"role": role, "previous": self.previous_states.get(role), "current": signature})
            self.previous_states[role] = signature
            if state["Running"]:
                try:
                    probe = parse_probe(self.command(["docker", "exec", value["Id"], "/bin/sh", "-c", probe_script()]))
                    fact["proc_sample"] = probe
                    if vm is None:
                        vm = {"source_owned_container_id": value["Id"], "memory": probe["vm_memory"],
                              "cpu_ticks": probe["vm_cpu_ticks"],
                              "cpu_busy_percent_since_previous_sample": vm_cpu_percent(self.previous_vm_cpu, probe["vm_cpu_ticks"])}
                        self.previous_vm_cpu = probe["vm_cpu_ticks"]
                except (WatchError, subprocess.TimeoutExpired):
                    # Stop/exit can race an exec; retain the exact new state.
                    latest = self.container(role)
                    fact["proc_sample"] = {"availability": "read_failed_or_exit_race"}
                    fact["state_after_proc_probe"] = {k: latest["State"][k] for k in ("Running", "OOMKilled", "ExitCode")}
            else:
                fact["proc_sample"] = {"availability": "container_stopped_no_historical_rss"}
            roles[role] = fact
        self.verify()
        return {"type": "sample", "sampled_at_utc": utc(), "sample_elapsed_seconds": time.monotonic() - started,
                "project": self.state["project"], "roles": roles, "state_transitions": transitions,
                "docker_vm_capacity": self.vm_info, "vm_observation": vm,
                "vm_observation_unavailable_reason": None if vm else "no_running_owned_app_or_nextcloud"}

    def signalled(self, number, _frame):
        self.signal_number = number
        self.stop.set()

    def run(self):
        metadata = {"schema_version": 1, "observer_only": True, "pid": os.getpid(), "started_at_utc": utc(),
                    "project": self.state["project"], "image_ids": self.image_ids,
                    "requested_duration_seconds": self.duration, "interval_seconds": self.interval,
                    "stop_on_app_exit": self.stop_on_app_exit, "business_quiescence_or_success_attested": False,
                    "sampling_limits": ["short peaks can be missed", "Docker CLI working-set values are rounded",
                        "stats, process RSS and state reads occur at different instants", "Go/native memory cannot be separated by RSS",
                        "no RSS history before the watcher starts or after process exit is reconstructed"]}
        token = {"marker": "read_only_resource_watch_v1", "pid": os.getpid(), "started_at_utc": metadata["started_at_utc"],
                 "watcher_id": str(uuid.uuid4()), "project": self.state["project"], "output": str(self.output)}
        self.marker_raw = exclusive_json(self.marker, token)
        info = self.marker.lstat()
        marker_identity = (info.st_dev, info.st_ino)
        jsonl = self.output / "samples.jsonl"
        previous_handlers = {n: signal.signal(n, self.signalled) for n in (signal.SIGINT, signal.SIGTERM)}
        terminal, reason, error, count, maximum_rss = 0, "duration_elapsed", None, 0, None
        started = time.monotonic()
        print(json.dumps({"stage": "resource_watch_started", "project": self.state["project"],
                          "pid": os.getpid(), "output": str(self.output)}), flush=True)
        try:
            fd = os.open(jsonl, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                while True:
                    if self.stop.is_set():
                        reason = "signal_received"
                        break
                    sample = self.sample()
                    stream.write(json.dumps(sample, separators=(",", ":")) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                    count += 1
                    app = sample["roles"]["wk-app"]
                    rss = app.get("proc_sample", {}).get("weknora_rss_sum_bytes")
                    if rss is not None:
                        maximum_rss = rss if maximum_rss is None else max(rss, maximum_rss)
                    if self.stop_on_app_exit and "running" in app and not app["running"]:
                        reason = "app_exit_observed"
                        break
                    if self.stop.is_set():
                        reason = "signal_received"
                        break
                    remaining = self.duration - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    self.stop.wait(min(self.interval, remaining))
                metadata["observed_application_state"] = self.previous_states.get("wk-app")
        except Exception as exc:
            terminal, reason = 1, "observer_failed"
            error = str(exc) if isinstance(exc, WatchError) else type(exc).__name__
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
            metadata.update(terminal=terminal, terminal_reason=reason, error_code=error,
                            finished_at_utc=utc(), elapsed_seconds=time.monotonic() - started,
                            sample_count=count, sampled_max_weknora_rss_bytes=maximum_rss,
                            captured_container_ids=self.ids, received_signal=self.signal_number,
                            observed_application_state=self.previous_states.get("wk-app"),
                            own_marker_removed=remove_own_marker(self.marker, self.marker_raw, marker_identity))
            if jsonl.exists():
                metadata["samples_sha256"] = hashlib.sha256(jsonl.read_bytes()).hexdigest()
            exclusive_json(self.output / "metadata.json", metadata)
        print(json.dumps({"stage": "resource_watch_finished", "project": self.state["project"],
                          "terminal": terminal, "reason": reason, "samples": count, "output": str(self.output)}), flush=True)
        return terminal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--stop-on-app-exit", action="store_true")
    args = parser.parse_args()
    require(math.isfinite(args.interval) and 5 <= args.interval <= 300, "invalid_sampling_interval")
    require(math.isfinite(args.duration) and 0 < args.duration <= 43200, "invalid_sampling_duration")
    return Observer(args.scratch, args.output, args.interval, args.duration, args.stop_on_app_exit).run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (WatchError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        code = str(error) if isinstance(error, WatchError) else type(error).__name__
        print(json.dumps({"stage": "resource_watch_refused", "terminal": 1, "error_code": code}), flush=True)
        raise SystemExit(1)
