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

Configure `VERCEL_TOKEN` and `VERCEL_PROJECT_ID` in `.env`, plus `VERCEL_TEAM_ID`
for a team project. Every eval runs in a disposable Vercel sandbox and requires
network access. The bundled example agent uses `API_KEY`
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
├── run.sh                   configures and launches the coding agent
├── dependencies.json        optional sandbox dependencies for this agent
└── ...             skills, tools, configuration, or custom agent code
```

The directory is copied to `/agent`, except `dependencies.json`, which the harness
reads on the host to prepare the sandbox. The harness runs:

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
the task workspace. Declare CLI and system dependencies in
`agents/<name>/dependencies.json` alongside the agent's `run.sh`.
The launch script runs without root privileges.

Setup checks the image's Node 22+, npm, Python 3, bash, and `setpriv`; it does not
install the baseline. The optional manifest accepts `system` package lists keyed
by `apt` or `dnf`, plus an `npm` package list. Only the selected agent's packages
are installed, before the task clock starts, with a separate 300-second timeout.
No manifest means no package installs. Setup errors are recorded as `infra`.

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
root-owned through dependency setup or the image. Readable runtime code and skills can reveal the setup,
so this does not guarantee eval unawareness or adversarial grading isolation.

## Results

```
results/<suite>/<task>/<agent>/<model>/run-N/
├── result.json             verdict, duration, exit codes, errors
├── agent_output.txt        agent process stdout/stderr
├── setup_output.txt        dependency installation output
├── dependencies.json       dependencies used for this attempt
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
Summary `pass_rate` uses `evaluated` attempts, excluding `infra` errors, which
remain visible in `total` and `by_failure_type`. With no evaluated attempts,
`pass_rate` is null. Agent timeouts count as evaluated failures.

## Sandbox and checks

The sandbox uses a pinned digest of `vcr.vercel.com/vercel/sandbox/universal`:
Vercel's Ubuntu image with Node 24, Python 3.14, coding agents, and common tools.
It uses `/workspace` and `linux/amd64`. Override it with `--image`, preferably a
fully qualified VCR `image@sha256:<digest>` reference. The default digest is declared in
`minieval/sandbox.py`. The requested image and platform are recorded in `summary.json`.

The harness creates managed images through REST v3 and uses REST v2 for session
commands. There is no backend selection. `list` and unit tests work without
Vercel credentials; `run` checks the required configuration before creating a suite.

Validation (2026-09-09): Vercel successfully launched the universal image in the
`minimal-eval` project and returned the digest now pinned by the harness. The
image reports Node 24.19.0 and Python 3.14.4. Both bundled agents passed both
sample tasks on Vercel (4/4); the permission-boundary test also passed using the
pinned image.
See [Vercel's image documentation](https://vercel.com/docs/sandbox/concepts/images).

```bash
python3 -m unittest discover -s tests
TEST_VERCEL=1 python3 -m unittest discover -s tests -p test_permissions.py
```
