# minieval

A small agent eval harness: prepare a disposable sandbox, give an agent a task,
wait for it to finish, run a hidden verifier, and save artifacts for review.
The agent owns its tool loop, transcripts, and metrics. The harness does not
parse or replay them. Python 3.10+, no Python dependencies.

## Run

```bash
cp .env.example .env
python3 -m minieval list
python3 -m minieval run --task fix-median --model anthropic/claude-haiku-4.5
```

Docker must be running. The bundled example agent uses `API_KEY`
and optionally `BASE_URL`. Other agents use their own model IDs
and credentials.

Repeat `--task`, `--agent`, or `--model` to compare combinations. `--runs 10`
repeats each combination. Use `--env KEY=VALUE` for agent configuration,
`--time-limit 600` for the agent command timeout, and `--name experiment`
for a new results directory. Setup, verification, and artifact collection
are additional to the agent timeout.

## Tasks

```
tasks/<name>/
├── PROMPT.md       task instruction
├── verifier.py     exit 0 = pass; runs after the agent finishes
├── environment/    optional starter files
└── task.json       optional JSON metadata
```

Hidden files and cache directories in `environment/` are excluded from upload.
The prompt is passed through the environment. The verifier is uploaded only
after the agent finishes. Verification uses the same agent-modifiable sandbox;
this is test withholding for cooperative experiments, not tamper-proof grading.

## Agents

```
agents/<name>/
├── run.sh          configures and launches the coding agent
└── ...             skills, tools, configuration, or custom agent code
```

The entire directory is copied to `/agent`. The harness runs:

```bash
bash /agent/run.sh "$prompt" "$model"
```

The working directory is the task workspace. Inside `run.sh`, `$1` is the task
prompt and `$2` is the selected model. There is no `entry` setting, required
`agent.py`, or `MINIEVAL_*` environment variable.

`API_KEY` and `BASE_URL` are forwarded from the host; `--env KEY=VALUE` supplies
additional settings. The launch script maps these generic settings to the CLI's
own environment variables, flags, or configuration. The provider must support
the API protocol that the chosen agent uses.

The launch script places bundled skills and tool configuration where its agent
expects them. Use `/agent/...` for bundled files, since the current directory is
the task workspace. Install the CLI and system dependencies in the Docker image
selected by `--image`, including Python 3, bash, and `setpriv` (util-linux).
The launch script runs without root privileges.

For review, the harness provides `/artifacts`. Configure the agent to write its
native session logs there, or have `run.sh` copy them there before exiting.
Files are archived unchanged, with no required transcript or metrics schema.
Writing logs there during execution also permits collection after interruption.

The bundled example's `run.sh` launches its own Python implementation:

```bash
set -e
exec python3 /agent/agent.py "$1" "$2"
```

An installed coding agent's launch script invokes that CLI instead. All agent
behavior stays in its directory; the eval only launches `run.sh` and grades the
resulting workspace.

## File permissions

Agent execution uses UID/GID 10000, no supplementary groups or Linux capabilities,
and `no_new_privs` to prevent privilege escalation through setuid programs.
The workspace, `/home/worker`, and `/artifacts` are writable by this identity.
Uploaded `/agent` files are root-owned: directories and executable files use
`0555`, other files use `0444`. Git metadata, bytecode caches, and symlinks are
excluded from agent uploads. The verifier is withheld until completion and
placed in a private `0700` directory. Grading runs as a second unprivileged
identity (UID/GID 10001), which takes ownership of the workspace and verifier.
It cannot modify root-owned agent code either.

These permissions protect uploaded agent code from modification. Code installed
into the agent's writable home remains mutable; keep protected CLI installations
root-owned in the image. Readable runtime code and skills can reveal the setup,
so this does not guarantee eval unawareness or adversarial grading isolation.

## Results

```
results/<suite>/<task>/<agent>/<model>/run-N/
├── result.json             verdict, duration, exit codes, errors
├── agent_output.txt        agent process stdout/stderr
├── verifier_output.txt     verifier stdout/stderr
├── agent-artifacts.tar.gz  agent-produced files, unchanged
└── workspace.tar.gz        final task workspace
results/<suite>/summary.json
```

Artifacts are collected before sandbox cleanup, including on failed attempts
when the sandbox remains accessible. Collection errors appear in `result.json`.
Suite names must be new; existing suites are never overwritten.

The verifier determines pass/fail for completed agent commands, including nonzero
agent exits. Exit 124 is reserved for timeout and skips verification. Failure types
are `verification`, `timeout`, and `infra` (harness/sandbox errors). Exit codes are
recorded directly; the harness does not infer whether a model or agent caused a failure.

## Backends and checks

`--sandbox docker` uses a fresh local Docker container per attempt, defaulting to
`python:3.11-slim`. `--sandbox vercel` uses the Vercel Sandbox REST API and requires
`VERCEL_TOKEN`, with optional `VERCEL_TEAM_ID` and `VERCEL_PROJECT_ID`. The Vercel
integration is experimental and has not been live-verified. `auto` selects Vercel
when its token is set, otherwise Docker.

```bash
python3 -m unittest discover -s tests
TEST_DOCKER=1 python3 -m unittest discover -s tests -p test_permissions.py
```
