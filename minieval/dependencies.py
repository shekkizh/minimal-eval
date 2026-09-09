"""Install dependencies needed to run agents in the sandbox."""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from .sandbox import Sandbox

MANIFEST_NAME = "dependencies.json"
SETUP_TIMEOUT = 300.0


def load_dependencies(path: Path) -> dict:
    """An absent manifest means only the harness baseline is required."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) - {"system", "npm"}:
        raise ValueError(f"{path}: expected an object with only system and npm keys")
    system = data.get("system", {})
    if not isinstance(system, dict) or set(system) - {"apt", "dnf"}:
        raise ValueError(f"{path}: system must map apt/dnf to package lists")
    for packages in [*system.values(), data.get("npm", [])]:
        if not isinstance(packages, list) or any(
            not isinstance(package, str) or not package.strip() or package.startswith("-")
            for package in packages
        ):
            raise ValueError(f"{path}: packages must be nonempty strings, not command options")
    return data


def install_dependencies(sandbox: Sandbox, dependencies: dict, log_path: Path) -> None:
    """Run trusted setup as root, with one total deadline and persistent logs."""
    deadline = time.monotonic() + SETUP_TIMEOUT
    with log_path.open("w") as log:
        def run(cmd: list[str]) -> str:
            log.write(f"$ {shlex.join(cmd)}\n")
            log.flush()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("dependency setup exceeded its 300s timeout")
            result = sandbox.exec(cmd, cwd="/", timeout=remaining)
            log.write(result.stdout + result.stderr + "\n")
            log.flush()
            if not result.ok:
                raise RuntimeError(
                    f"dependency setup failed (exit {result.exit_code}): {shlex.join(cmd)}; "
                    "see setup_output.txt"
                )
            return result.stdout.strip()

        # The shared image supplies the baseline. Fail clearly instead of changing it.
        run(["sh", "-ec", "for tool in node npm python3 bash setpriv; do command -v \"$tool\"; done; "
             "node -e 'if (Number(process.versions.node.split(\".\")[0]) < 22) "
             "throw new Error(\"Node 22+ required\")'; npm --version; python3 --version"])
        system = dependencies.get("system", {})
        if system:
            manager = run(["sh", "-ec",
                       "if command -v apt-get >/dev/null; then echo apt; "
                       "elif command -v dnf >/dev/null; then echo dnf; "
                       "else echo 'setup requires apt-get or dnf' >&2; exit 1; fi"])
            if manager not in system:
                raise RuntimeError(f"{MANIFEST_NAME}: no system package list for {manager}")
            packages = system[manager]
            if packages:
                if manager == "apt":
                    run(["apt-get", "update"])
                    run(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                         "--no-install-recommends", "--", *packages])
                else:
                    run(["dnf", "install", "-y", "--", *packages])
        if dependencies.get("npm"):
            run(["npm", "install", "--global", "--prefix", "/usr/local", "--", *dependencies["npm"]])
