"""Task discovery and loading.

A task is a directory under `tasks/`:

    tasks/<name>/
      PROMPT.md        required — instruction given to the agent
      environment/     optional — files copied into the sandbox before the run
      verifier.py      required — uploaded AFTER the agent finishes and executed
                       in the sandbox; exit 0 = pass. Stdlib-only.
    task.json        optional metadata (description, tags)
"""
from __future__ import annotations
import json
from pathlib import Path

from .types import Task


def load_task(path: Path) -> Task:
    prompt_path = path / "PROMPT.md"
    verifier_path = path / "verifier.py"
    if not prompt_path.is_file():
        raise ValueError(f"{path}: missing PROMPT.md")
    if not verifier_path.is_file():
        raise ValueError(f"{path}: missing verifier.py")

    environment_files: dict[str, str | bytes] = {}
    env_dir = path / "environment"
    if env_dir.is_dir():
        for file in sorted(env_dir.rglob("*")):
            if file.is_file() and not file.is_symlink() and not any(
                part.startswith(".") or part == "__pycache__"
                for part in file.relative_to(env_dir).parts
            ):
                rel = file.relative_to(env_dir).as_posix()
                environment_files[rel] = file.read_text()

    metadata: dict[str, object] = {}
    meta_path = path / "task.json"
    if meta_path.is_file():
        parsed = json.loads(meta_path.read_text())
        if not isinstance(parsed, dict):
            raise ValueError(f"{meta_path}: expected a JSON object")
        metadata = parsed

    return Task(
        name=path.name,
        path=str(path),
        prompt=prompt_path.read_text(),
        environment_files=environment_files,
        verifier=verifier_path.read_text(),
        metadata=metadata,
    )


def discover_tasks(tasks_dir: Path, selected: list[str] | None = None) -> list[Task]:
    """Load tasks from `tasks_dir`. `selected` filters by name (None = all)."""
    if not tasks_dir.is_dir():
        return []
    tasks = [load_task(d) for d in sorted(tasks_dir.iterdir()) if d.is_dir()]
    if selected:
        wanted = set(selected)
        unknown = wanted - {t.name for t in tasks}
        if unknown:
            raise SystemExit(f"unknown task(s): {', '.join(sorted(unknown))}")
        tasks = [t for t in tasks if t.name in wanted]
    return tasks
