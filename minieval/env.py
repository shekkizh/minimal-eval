"""Tiny .env loader (stdlib only). Loads `<project>/.env` into os.environ
without overriding variables that are already set."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(project_root: Path | None = None) -> None:
    candidates = []
    if project_root is not None:
        candidates.append(project_root / ".env")
    candidates.append(Path.cwd() / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
