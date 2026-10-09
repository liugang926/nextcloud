#!/usr/bin/env python3
"""Own a disposable LDAP/Nextcloud/WeKnora stack for a real browser ask probe."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "synthetic-ldap-fixture.py"
BOOTSTRAP = HERE / "synthetic-ldap-e2e.py"
BROWSER = HERE / "synthetic-ldap-browser-ask.js"


def run(command, step, timeout):
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Compose and browser logs can contain credentials or session material.
        raise RuntimeError(f"{step} failed with exit code {result.returncode}")
    return result.stdout.strip()


def preflight(args):
    if args.browser_executable and not Path(args.browser_executable).is_file():
        raise RuntimeError("browser executable does not exist")
    module = args.playwright_module or "playwright"
    run(["node", "-e", "require(process.argv[1]).chromium",
         module], "Playwright module preflight", 15)
    for image in (args.weknora_image, args.weknora_ui_image):
        run(["docker", "image", "inspect", image,
             "--format", "{{.Id}}"], "local image preflight", 15)


def check_cleanup(project):
    if not re.fullmatch(r"nc-synldap-[0-9a-f]{8}", project):
        raise RuntimeError("invalid owned Compose project")
    for kind, command in (
        ("containers", ["docker", "ps", "-a", "--filter",
                        f"label=com.docker.compose.project={project}", "--format", "{{.ID}}"]),
        ("volumes", ["docker", "volume", "ls", "--filter",
                     f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"]),
        ("networks", ["docker", "network", "ls", "--filter",
                      f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"]),
    ):
        if run(command, f"{kind} cleanup check", 15):
            raise RuntimeError(f"owned {kind} remain after cleanup")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weknora-image", required=True,
                        help="locally built backend image tag")
    parser.add_argument("--weknora-ui-image", required=True,
                        help="locally built frontend image tag")
    parser.add_argument("--playwright-module",
                        help="absolute path to an installed Playwright Node module")
    parser.add_argument("--browser-executable",
                        help="optional isolated Chromium headless executable")
    parser.add_argument('--candidate-profile', choices=('c6', 'rag'),
                        help='require exact candidate app/UI source labels before the owned fixture')
    parser.add_argument('--candidate-manifest',
                        help='optional frozen manifest snapshot for this exact candidate build')
    args = parser.parse_args()
    provenance = None
    if args.candidate_profile:
        command = [sys.executable, str(HERE / 'inspect-weknora-candidate-images.py'),
                   '--profile', args.candidate_profile, '--app-image', args.weknora_image,
                   '--ui-image', args.weknora_ui_image]
        if args.candidate_manifest:
            command.extend(['--manifest', args.candidate_manifest])
        provenance = json.loads(run(command, 'exact candidate image preflight', 35))
    elif args.candidate_manifest:
        raise ValueError('candidate manifest requires a candidate profile')
    preflight(args)
    evidence = Path(tempfile.mkdtemp(prefix="nc-browser-ask-evidence-"))
    evidence.chmod(0o700)
    scratch = None
    project = None
    primary_error = None
    result = None
    try:
        prepared = run([sys.executable, str(FIXTURE), "prepare",
                        "--weknora-image", args.weknora_image,
                        "--weknora-ui-image", args.weknora_ui_image,
                        "--mode", "direct"], "prepare isolated fixture", 60)
        state = json.loads(prepared)
        scratch = Path(state["scratch"])
        project = state["project"]
        if provenance and (state['weknora_image_id'] != provenance['app']['image_id'] or
                           state['weknora_ui_image_id'] != provenance['ui']['image_id']):
            raise RuntimeError('candidate image changed between preflight and fixture preparation')
        if not re.fullmatch(r"nc-synldap-[0-9a-f]{8}", project):
            raise RuntimeError("prepared fixture returned an invalid project")
        run([sys.executable, str(FIXTURE), "up", "--scratch", str(scratch)],
            "start isolated fixture", 750)
        run([sys.executable, str(BOOTSTRAP), "bootstrap", "--scratch", str(scratch)],
            "bootstrap isolated fixture", 300)
        browser_command = ["node", str(BROWSER), "--scratch", str(scratch),
                           "--evidence-dir", str(evidence)]
        if args.playwright_module:
            browser_command.extend(["--playwright-module", args.playwright_module])
        if args.browser_executable:
            browser_command.extend(["--browser-executable", args.browser_executable])
        result = json.loads(run(browser_command, "browser ask flow", 180))
    except (KeyError, OSError, ValueError, RuntimeError,
            subprocess.TimeoutExpired) as error:
        primary_error = error
    cleanup_error = None
    if scratch is not None:
        try:
            run([sys.executable, str(FIXTURE), "destroy", "--scratch", str(scratch)],
                "destroy isolated fixture", 120)
            check_cleanup(project)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            cleanup_error = error
    if primary_error or cleanup_error:
        parts = [str(error) for error in (primary_error, cleanup_error) if error]
        raise RuntimeError("; ".join(parts) + f"; private evidence: {evidence}")
    print(json.dumps({**result, "fixture_cleaned": True,
                      "evidence": str(evidence), "candidate_image_provenance": provenance}, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("Isolated browser ask smoke failed: " + str(error), file=sys.stderr)
        sys.exit(1)
