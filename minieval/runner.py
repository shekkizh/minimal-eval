"""Prepare a task, run an agent, verify, and preserve artifacts."""
from __future__ import annotations
import datetime
import json
import time
from pathlib import Path
from urllib.parse import quote

from .agents import ARTIFACTS_DIR, Agent
from .dependencies import MANIFEST_NAME, SETUP_TIMEOUT, install_dependencies
from .sandbox import DEFAULT_IMAGE, Sandbox, create_sandbox
from .task import Task
from .types import RunResult

VERIFIER_DIR = "/verifier"


def _slug(text: str) -> str:
    return quote(text, safe="").replace(".", "%2E") or "_"


def run_attempt(
    task: Task,
    agent: Agent,
    run_number: int,
    time_limit: float,
    output_dir: Path,
    sandbox_kwargs: dict | None = None,
) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    sandbox: Sandbox | None = None
    artifacts_dir = None
    agent_stdout = agent_stderr = verifier_output = ""
    result = RunResult(task=task.name, agent=agent.name, model=agent.model,
                       run=run_number, passed=False)
    try:
        (output_dir / MANIFEST_NAME).write_text(json.dumps(agent.dependencies, indent=2) + "\n")
        sandbox = create_sandbox(timeout_s=time_limit + SETUP_TIMEOUT, **(sandbox_kwargs or {}))
        install_dependencies(sandbox, agent.dependencies, output_dir / "setup_output.txt")
        if task.environment_files:
            sandbox.write_files(task.environment_files)
        agent.setup(sandbox)
        env = agent.env()
        artifacts_dir = ARTIFACTS_DIR
        agent.protect(sandbox)

        started = time.monotonic()
        try:
            proc = sandbox.run_agent(agent.command(task.prompt), env=env, timeout=time_limit)
        finally:
            result.duration = time.monotonic() - started
        result.agent_exit_code = proc.exit_code
        agent_stdout, agent_stderr = proc.stdout, proc.stderr
        if proc.exit_code == 124:
            result.failure_type = "timeout"
            result.error = f"agent exceeded {time_limit:.0f}s wall clock"
        else:
            # Withhold the verifier and keep it unreadable to the agent user.
            protected = sandbox.exec(["mkdir", "-m", "0700", VERIFIER_DIR])
            if not protected.ok:
                raise RuntimeError(f"verifier directory setup failed: {protected.stderr}")
            sandbox.write_files({f"{VERIFIER_DIR}/verifier.py": task.verifier or ""}, extract_dir="/")
            verify = sandbox.run_verifier(VERIFIER_DIR)
            result.verifier_exit_code = verify.exit_code
            result.passed = verify.ok
            verifier_output = verify.stdout + verify.stderr
            if not result.passed:
                result.failure_type = "verification"
                result.error = f"verifier exit {verify.exit_code}"
    except Exception as err:
        result.failure_type = "infra"
        result.error = f"{type(err).__name__}: {err}"
    finally:
        if sandbox is not None:
            exports = [(sandbox.workdir, "workspace.tar.gz")]
            if artifacts_dir:
                exports.append((artifacts_dir, "agent-artifacts.tar.gz"))
            for path, name in exports:
                try:
                    sandbox.export_directory(path, output_dir / name)
                except Exception as err:
                    result.error = f"{result.error + '; ' if result.error else ''}{name}: {err}"
            try:
                sandbox.stop()
            except Exception as err:
                result.passed = False
                result.failure_type = "infra"
                result.error = f"{result.error + '; ' if result.error else ''}cleanup failed: {err}"

    (output_dir / "result.json").write_text(json.dumps(result.as_dict(), indent=2) + "\n")
    (output_dir / "verifier_output.txt").write_text(verifier_output)
    (output_dir / "agent_output.txt").write_text(agent_stdout + agent_stderr)
    return result


def run_suite(
    tasks: list[Task],
    agents: list[Agent],
    runs: int,
    time_limit: float,
    results_dir: Path,
    run_name: str | None = None,
    sandbox_kwargs: dict | None = None,
) -> Path:
    """Run every (task, agent, run) combination; write artifacts; print summary."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    name = run_name or stamp
    suite_dir = results_dir / name
    suite_dir.mkdir(parents=True, exist_ok=False)

    total = len(tasks) * len(agents) * runs
    done = 0
    results: list[RunResult] = []
    for task in tasks:
        for agent in agents:
            for run_number in range(1, runs + 1):
                done += 1
                label = f"[{done}/{total}] {task.name} | {agent.name}:{agent.model} | run {run_number}"
                print(f"{label} ... ", flush=True)
                result = run_attempt(
                    task=task,
                    agent=agent,
                    run_number=run_number,
                    time_limit=time_limit,
                    output_dir=suite_dir / _slug(task.name) / _slug(agent.name) / _slug(agent.model) / f"run-{run_number}",
                    sandbox_kwargs=sandbox_kwargs,
                )
                results.append(result)
                status = "PASS" if result.passed else f"FAIL ({result.failure_type})"
                print(f"  {status} in {result.duration:.0f}s", flush=True)

    summary = {
        "name": name,
        "started": stamp,
        "backend": "vercel",
        "image": (sandbox_kwargs or {}).get("image", DEFAULT_IMAGE),
        "platform": "linux/amd64",
        "runs_per_combination": runs,
        "results": [r.as_dict() for r in results],
        "totals": _totals(results),
    }
    (suite_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    _print_summary(results)
    print(f"\nArtifacts: {suite_dir}")
    return suite_dir


def _totals(results: list[RunResult]) -> dict:
    passed = sum(1 for r in results if r.passed)
    evaluated = sum(1 for r in results if r.failure_type != "infra")
    return {
        "total": len(results),
        "passed": passed,
        "evaluated": evaluated,
        "pass_rate": round(passed / evaluated, 3) if evaluated else None,
        "by_failure_type": {
            k: sum(1 for r in results if not r.passed and r.failure_type == k)
            for k in ("verification", "timeout", "infra")
        },
    }


def _print_summary(results: list[RunResult]) -> None:
    groups: dict[tuple[str, str, str], list[RunResult]] = {}
    for r in results:
        groups.setdefault((r.task, r.agent, r.model), []).append(r)
    print("\n=== Summary ===")
    for (task, agent, model), rs in sorted(groups.items()):
        totals = _totals(rs)
        print(f"{task:22s} {agent:18s} {model:38s} "
              f"{totals['passed']}/{totals['evaluated']} passed; "
              f"{totals['by_failure_type']['infra']} infra errors")
