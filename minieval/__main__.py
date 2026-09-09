"""CLI entry point: `minieval list` and `minieval run` (argparse self-documents flags)."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

from . import env as env_loader
from .agents import discover_agent, list_agents
from .runner import run_suite
from .sandbox import DEFAULT_DOCKER_IMAGE
from .task import discover_tasks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minieval", description="Minimal agent eval harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List discovered tasks and agents.")
    list_parser.add_argument("--tasks-dir", default="tasks")
    list_parser.add_argument("--agents-dir", default="agents")

    run = sub.add_parser("run", help="Run task/agent/model combinations.")
    run.add_argument("--task", action="append", default=[], help="Task name (repeatable; default all).")
    run.add_argument("--agent", action="append", default=[], help="Agent dir name (repeatable; default minimal-agent).")
    run.add_argument("--model", action="append", default=[], help="Agent model id (repeatable; required).")
    run.add_argument("--runs", type=int, default=1, help="Runs per (task, agent, model).")
    run.add_argument("--sandbox", default="auto", choices=["auto", "docker", "vercel"])
    run.add_argument("--time-limit", type=float, default=600.0, help="Agent wall-clock limit in seconds (setup and verification are additional).")
    run.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="Extra env var for the agent process (repeatable).")
    run.add_argument("--name", default=None, help="Name for this suite run (results dir).")
    run.add_argument("--tasks-dir", default="tasks")
    run.add_argument("--agents-dir", default="agents")
    run.add_argument("--results-dir", default="results")
    run.add_argument("--image", default=DEFAULT_DOCKER_IMAGE, help="Docker image (docker backend).")

    args = parser.parse_args(argv)
    root = Path.cwd()
    env_loader.load_env(root)

    if args.command == "list":
        tasks = discover_tasks(root / args.tasks_dir)
        if not tasks:
            print(f"No tasks found in {root / args.tasks_dir}")
        for task in tasks:
            print(f"task  {task.name:24s} {task.metadata.get('description', '')}")
        for name in list_agents(root / args.agents_dir):
            print(f"agent {name}")
        return

    # run
    if not args.model:
        parser.error("--model is required (e.g. --model anthropic/claude-haiku-4.5)")

    tasks = discover_tasks(root / args.tasks_dir, args.task or None)
    if not tasks:
        raise SystemExit(f"No tasks found in {root / args.tasks_dir}")

    if args.runs < 1:
        parser.error("--runs must be positive")
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        parser.error("--time-limit must be finite and positive")
    if any("=" not in kv or not kv.split("=", 1)[0] for kv in args.env):
        parser.error("--env must be KEY=VALUE with a nonempty key")
    if args.name and (Path(args.name).name != args.name or args.name in (".", "..")):
        parser.error("--name must be a directory name, not a path")
    if args.sandbox == "auto":
        args.sandbox = "vercel" if os.environ.get("VERCEL_TOKEN") else "docker"
    extra_env = dict(kv.split("=", 1) for kv in args.env)
    agents = [
        discover_agent(root / args.agents_dir, name, model, extra_env)
        for name in (args.agent or ["minimal-agent"])
        for model in args.model
    ]

    sandbox_kwargs = {"image": args.image} if args.sandbox in ("docker", "auto") else {}
    print(
        f"Running {len(tasks)} task(s) x {len(agents)} agent/model combos x {args.runs} run(s) "
        f"on backend '{args.sandbox}'"
    )
    run_suite(
        tasks=tasks,
        agents=agents,
        runs=args.runs,
        backend=args.sandbox,
        time_limit=args.time_limit,
        results_dir=root / args.results_dir,
        run_name=args.name,
        sandbox_kwargs=sandbox_kwargs,
    )


if __name__ == "__main__":
    main()
