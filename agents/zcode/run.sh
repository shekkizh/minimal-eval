# Launch the bundled ZCode CLI (zcode.cjs) on the task prompt.
# $1 is the task prompt and $2 is the selected model id, which is passed
# through verbatim to the provider endpoint. The harness --time-limit bounds
# the run; exit code 124 is reserved for its timeout.
set -eu

prompt=$1
model=$2

: "${API_KEY:?API_KEY must be set (minieval forwards it from the host)}"
export BASE_URL="${BASE_URL:-https://ai-gateway.vercel.sh/v1}"

# Headless ZCode config through the environment: ZCODE_MODEL is a
# provider/model ref, so the gateway model id is prefixed with a provider id.
# The env-configured provider speaks the anthropic-messages protocol at
# ZCODE_BASE_URL (normalized to a single /v1 suffix). --prompt runs in yolo
# mode by default, so tool calls are auto-approved.
export ZCODE_MODEL="vercel/$model"
export ZCODE_BASE_URL="$BASE_URL"
export ZCODE_API_KEY="$API_KEY"
export ANTHROPIC_API_KEY="$API_KEY"

status=0
node /agent/zcode.cjs --prompt "$prompt" --no-color || status=$?

# Preserve native session logs for review (written under HOME during the run).
cp -r "$HOME/.zcode" /artifacts/zcode-session 2>/dev/null || true
exit "$status"
