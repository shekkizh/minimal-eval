"""Core dataclasses shared across the harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    """Result of a command executed in a sandbox."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class Task:
    """A discovered eval task directory."""

    name: str
    path: str
    prompt: str
    environment_files: dict[str, str|bytes] = field(default_factory=dict)
    verifier: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """Outcome of a single (task, agent, model, run) attempt."""

    task: str
    agent: str
    model: str
    run: int
    passed: bool
    duration: float = 0.0  # agent wall-clock seconds
    agent_exit_code: int | None = None
    verifier_exit_code: int | None = None
    failure_type: str | None = None  # 'verification' | 'timeout' | 'infra'
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "agent": self.agent,
            "model": self.model,
            "run": self.run,
            "passed": self.passed,
            "duration_sec": round(self.duration, 1),
            "agent_exit_code": self.agent_exit_code,
            "verifier_exit_code": self.verifier_exit_code,
            "failure_type": self.failure_type,
            "error": self.error,
        }
