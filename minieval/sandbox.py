"""Disposable Vercel sandboxes."""

from __future__ import annotations

import base64
import io
import json
import os
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import cast

from .types import CommandResult

# Resolved from a live Vercel Sandbox on 2026-09-09 (Node 24.19.0 / Python 3.14.4).
DEFAULT_IMAGE = (
    "vcr.vercel.com/vercel/sandbox/universal@"
    "sha256:0e3e3617e824397f170fc7c43ccaa565dd7ac36518e83ead3d41e077cd9f6ec7"
)


class Sandbox(ABC):
    """A disposable Linux environment with a working directory."""

    workdir: str

    @abstractmethod
    def exec(
        self,
        cmd: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...

    @abstractmethod
    def write_files(self, files: dict[str, str | bytes], extract_dir: str | None = None) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def sandbox_id(self) -> str: ...

    def prepare_agent(self, code_dir: str, artifacts_dir: str, executables: list[str]) -> None:
        script = """
set -eu
code=$1; workspace=$2; artifacts=$3
shift 3
mkdir -p /home/worker "$artifacts"
chown -hR 10000:10000 "$workspace" /home/worker "$artifacts"
chmod -R u+rwX,go-rwx "$workspace" /home/worker "$artifacts"
chown -hR 0:0 "$code"
find "$code" -type d -exec chmod 0555 {} +
find "$code" -type f -exec chmod 0444 {} +
for file do chmod 0555 "$code/$file"; done
"""
        result = self.exec(["sh", "-c", script, "sh", code_dir, self.workdir, artifacts_dir, *executables])
        if not result.ok:
            raise RuntimeError(f"agent permissions failed: {result.stderr}")

    def run_agent(self, cmd: list[str], env: dict[str, str], timeout: float, uid: int = 10000) -> CommandResult:
        script = (
            f'umask 077; exec setpriv --reuid={uid} --regid={uid} --clear-groups '
            '--no-new-privs --bounding-set=-all --inh-caps=-all --ambient-caps=-all '
            f'env -i "PATH=$PATH" HOME={"/home/worker" if uid == 10000 else "/tmp"} "$@"'
        )
        return self.exec(["sh", "-c", script, "sh", *[f"{key}={value}" for key, value in env.items()], *cmd],
                         cwd=self.workdir, timeout=timeout)

    def run_verifier(self, directory: str) -> CommandResult:
        result = self.exec(["chown", "-hR", "10001:10001", self.workdir, directory])
        if not result.ok:
            raise RuntimeError(f"verifier permissions failed: {result.stderr}")
        return self.run_agent(["python3", f"{directory}/verifier.py"], {}, 120, uid=10001)

    def export_directory(self, path: str, destination: Path) -> None:
        result = self.exec([
            "python3", "-c",
            "import base64, io, sys, tarfile; "
            "buffer = io.BytesIO(); "
            "archive = tarfile.open(fileobj=buffer, mode='w:gz'); "
            "archive.add(sys.argv[1], arcname='.'); archive.close(); "
            "print(base64.b64encode(buffer.getvalue()).decode())",
            path,
        ], timeout=120)
        if not result.ok:
            raise RuntimeError(f"export {path} failed: {result.stderr}")
        destination.write_bytes(base64.b64decode(result.stdout.strip(), validate=True))


class VercelSandbox(Sandbox):
    """Managed image creation via Vercel REST v3; session operations via v2.

    Requires VERCEL_TOKEN (and optionally VERCEL_TEAM_ID / VERCEL_PROJECT_ID).
    Implements the same contract as @vercel/sandbox 3.x: sessions, cmd
    execution with wait/poll, gzipped-tar file upload, and file read.
    """

    API_BASE = "https://vercel.com/api"

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        timeout_ms: int = 600_000,
        workdir: str = "/workspace",
        token: str | None = None,
        team_id: str | None = None,
        project_id: str | None = None,
    ):
        self._token = token or os.environ.get("VERCEL_TOKEN", "")
        if not self._token:
            raise ValueError("VercelSandbox requires VERCEL_TOKEN")
        self._team_id = team_id or os.environ.get("VERCEL_TEAM_ID")
        self._project_id = project_id or os.environ.get("VERCEL_PROJECT_ID")
        self.workdir = workdir
        self.image = image
        body: dict = {"image": image, "timeout": timeout_ms, "persistent": False}
        if self._project_id:
            body["projectId"] = self._project_id
        data = self._request("POST", "/v3/sandboxes", json_body=body)
        self._session_id = data["session"]["id"]
        try:
            result = self.exec(["mkdir", "-p", workdir], cwd="/")
            if not result.ok:
                raise RuntimeError(f"workspace setup failed: {result.stderr}")
        except Exception:
            self.stop()
            raise

    @property
    def sandbox_id(self) -> str:
        return self._session_id

    # HTTP plumbing ------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        data: bytes | None = None,
        headers: dict | None = None,
        query: dict | None = None,
        raw: bool = False,
        timeout: float = 120.0,
    ):
        params = dict(query or {})
        if self._team_id:
            params.setdefault("teamId", self._team_id)
        url = f"{self.API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request_headers = {"Authorization": f"Bearer {self._token}"}
        if json_body is not None:
            data = json.dumps(json_body).encode()
            request_headers["content-type"] = "application/json"
        request_headers.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return body if raw else (json.loads(body) if body else {})

    # Sandbox interface ----------------------------------------------------

    def exec(
        self,
        cmd: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        sid = self._session_id
        body = {
            "command": cmd[0],
            "args": cmd[1:],
            "cwd": cwd or self.workdir,
            "env": env or {},
            "wait": False,
            "sudo": True,
        }
        started = self._request("POST", f"/v2/sandboxes/sessions/{sid}/cmd", json_body=body)
        cmd_id = started["command"]["id"]

        wait_query = {"wait": "true"}
        deadline = time.monotonic() + (timeout or 120.0)
        while True:
            finished = self._request(
                "GET",
                f"/v2/sandboxes/sessions/{sid}/cmd/{cmd_id}",
                query=wait_query,
            )
            if finished["command"].get("exitCode") is not None:
                exit_code = finished["command"]["exitCode"]
                break
            if time.monotonic() > deadline:
                self._request(
                    "POST",
                    f"/v2/sandboxes/sessions/{sid}/cmd/{cmd_id}/kill",
                    json_body={"signal": "SIGKILL"},
                )
                return CommandResult(exit_code=124, stdout="", stderr=f"timeout after {timeout}s")
            time.sleep(0.5)

        # Logs stream as ndjson lines: {"stream": "stdout"|"stderr", "data": "..."}
        stdout, stderr = [], []
        try:
            raw = self._request(
                "GET",
                f"/v2/sandboxes/sessions/{sid}/cmd/{cmd_id}/logs",
                raw=True,
            )
            for line in cast(bytes, raw).decode("utf-8", "replace").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("stream") == "stdout":
                    stdout.append(entry["data"])
                elif entry.get("stream") == "stderr":
                    stderr.append(entry["data"])
        except urllib.error.HTTPError:
            pass
        return CommandResult(exit_code=exit_code, stdout="".join(stdout), stderr="".join(stderr))

    def write_files(self, files: dict[str, str | bytes], extract_dir: str | None = None) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for relpath, content in files.items():
                payload = content.encode("utf-8") if isinstance(content, str) else content
                info = tarfile.TarInfo(name=relpath.lstrip("/"))
                info.size = len(payload)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(payload))
        staging = f"/tmp/upload-{uuid.uuid4().hex}"
        result = self.exec(["mkdir", "-m", "0777", staging])
        if not result.ok:
            raise RuntimeError(f"upload staging failed: {result.stderr}")
        try:
            self._request(
                "POST", f"/v2/sandboxes/sessions/{self._session_id}/fs/write",
                data=buf.getvalue(),
                headers={"content-type": "application/gzip", "x-cwd": staging},
            )
            result = self.exec(["sh", "-ec", 'mkdir -p "$2"; cp -R "$1/." "$2"',
                                "sh", staging, extract_dir or self.workdir])
            if not result.ok:
                raise RuntimeError(f"upload failed: {result.stderr}")
        finally:
            self.exec(["rm", "-rf", staging])

    def stop(self) -> None:
        try:
            self._request(
                "POST",
                f"/v2/sandboxes/sessions/{self._session_id}/stop",
                json_body={},
            )
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass


def create_sandbox(timeout_s: float = 600.0, **kwargs) -> Sandbox:
    """Create a Vercel sandbox, reserving additional time for grading."""
    return VercelSandbox(timeout_ms=int((timeout_s + 180) * 1000), **kwargs)
