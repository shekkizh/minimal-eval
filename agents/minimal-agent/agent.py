"""Example agent with its own tool loop and transcript. MAX_TURNS defaults to 30."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

MAX_TOOL_OUTPUT = 8_000
BASE_URL = os.environ.get("BASE_URL", "https://ai-gateway.vercel.sh/v1").rstrip("/")

SYSTEM_PROMPT = """\
You are a coding agent working inside a sandbox.

Working directory: {workdir}
"""

TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a bash command and return its output.", "parameters": {"type": "object", "properties": {
        "command": {"type": "string"}, "timeout_sec": {"type": "integer", "description": "Timeout in seconds (default 120, max 600)."}}, "required": ["command"]}}}]


class Transcript:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def add(self, event: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")


def chat(model: str, messages: list, max_retries: int = 3) -> dict:
    """One gateway chat completion with retries on transient failures."""
    body = json.dumps({"model": model, "messages": messages, "tools": TOOLS}).encode()
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            f"{BASE_URL}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {os.environ['API_KEY']}",
                     "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                completion = json.loads(resp.read())
            choice = completion["choices"][0]
            message = choice["message"]
            message["usage"] = completion.get("usage", {})
            message["finish_reason"] = choice.get("finish_reason")
            if not message.get("content") and not message.get("tool_calls"):
                raise RuntimeError(f"empty response (finish_reason={choice.get('finish_reason')})")
            return message
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:300]
            last_err = RuntimeError(f"HTTP {err.code}: {detail}")
            if err.code not in (408, 429) and err.code < 500:
                raise last_err from err
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, RuntimeError) as err:
            last_err = err
        time.sleep(2**attempt)
    raise last_err or RuntimeError("gateway request failed")


def run_tool(name: str, args: dict) -> str:
    if name == "bash":
        timeout = min(int(args.get("timeout_sec", 120)), 600)
        try:
            proc = subprocess.run(args["command"], shell=True, capture_output=True,
                                  text=True, timeout=timeout)
            out = f"exit_code: {proc.returncode}\n"
            if proc.stdout:
                out += f"stdout:\n{proc.stdout}\n"
            if proc.stderr:
                out += f"stderr:\n{proc.stderr}\n"
            return out
        except subprocess.TimeoutExpired:
            return f"error: command timed out after {timeout}s"

    return f"error: unknown tool {name}"


def main() -> int:
    prompt = sys.argv[1]
    model = sys.argv[2]
    max_turns = int(os.environ.get("MAX_TURNS", "30"))
    workdir = os.getcwd()
    transcript = Transcript("/artifacts/transcript.jsonl")

    transcript.add({"type": "prompt", "system": SYSTEM_PROMPT.format(workdir=workdir), "user": prompt})
    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(workdir=workdir)},
        {"role": "user", "content": prompt},
    ]

    for turn in range(1, max_turns + 1):
        try:
            message = chat(model, messages)
        except Exception as err:
            transcript.add({"type": "error", "turn": turn, "error": str(err)})
            print(f"agent error at turn {turn}: {err}", file=sys.stderr)
            return 1

        messages.append({"role": "assistant", "content": message.get("content"),
                         "tool_calls": message.get("tool_calls")})
        transcript.add({"type": "assistant", "turn": turn, "content": message.get("content"),
                        "tool_calls": message.get("tool_calls"),
                        "finish_reason": message.get("finish_reason"), "usage": message.get("usage", {})})

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            print(message.get("content") or "")
            return 0

        for call in tool_calls:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            output = run_tool(fn["name"], args)
            transcript.add({"type": "tool_result", "turn": turn, "tool": fn["name"], "args": args,
                            "output": output[:MAX_TOOL_OUTPUT], "truncated": len(output) > MAX_TOOL_OUTPUT})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output[:MAX_TOOL_OUTPUT]})

    print("max turns reached", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
