"""Install and launch agents inside the sandbox."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from .sandbox import Sandbox

AGENT_INSTALL_DIR = "/agent"
ARTIFACTS_DIR = "/artifacts"

class Agent(ABC):
    """An agent-under-test that runs inside the sandbox."""

    def __init__(self, name: str, model: str, extra_env: dict[str, str] | None = None):
        self.name = name
        self.model = model
        self.extra_env = extra_env or {}

    @abstractmethod
    def setup(self, sandbox: Sandbox) -> None: ...

    def protect(self, sandbox: Sandbox) -> None:
        sandbox.prepare_agent(AGENT_INSTALL_DIR, ARTIFACTS_DIR, [])

    @abstractmethod
    def command(self, prompt: str) -> list[str]: ...

    def env(self) -> dict[str, str]:
        injected = {key: os.environ[key] for key in ("API_KEY", "BASE_URL") if key in os.environ}
        injected.update(self.extra_env)
        return injected


class ScriptAgent(Agent):
    """Upload agent files and invoke run.sh with the prompt and model."""

    def __init__(self, name: str, path: Path, model: str, extra_env: dict[str, str] | None = None):
        super().__init__(name, model, extra_env)
        self.path = path
        self.files: dict[str, str | bytes] = {
            str(f.relative_to(path)): f.read_bytes()
            for f in sorted(path.rglob("*"))
            if f.is_file() and not f.is_symlink() and not any(part in (".git", "__pycache__") for part in f.relative_to(path).parts)
        }
        self.executables = [name for name in self.files if (path / name).stat().st_mode & 0o111]
        if "run.sh" not in self.files:
            raise ValueError(f"agent requires {path / 'run.sh'}")

    def setup(self, sandbox: Sandbox) -> None:
        sandbox.exec(["mkdir", "-p", AGENT_INSTALL_DIR])
        sandbox.write_files(self.files, extract_dir=AGENT_INSTALL_DIR)

    def protect(self, sandbox: Sandbox) -> None:
        sandbox.prepare_agent(AGENT_INSTALL_DIR, ARTIFACTS_DIR, self.executables)

    def command(self, prompt: str) -> list[str]:
        return ["bash", f"{AGENT_INSTALL_DIR}/run.sh", prompt, self.model]


def discover_agent(agents_dir: Path, name: str, model: str, extra_env: dict[str, str] | None = None) -> Agent:
    path = agents_dir / name
    if not path.is_dir():
        raise SystemExit(f"unknown agent '{name}': no directory {path}")
    return ScriptAgent(name, path, model, extra_env)


def list_agents(agents_dir: Path) -> list[str]:
    return sorted(d.name for d in agents_dir.iterdir() if (d / "run.sh").is_file()) if agents_dir.is_dir() else []
