#!/bin/bash
# ==============================================================================
# Konstruktor runner entrypoint
#
# Expects environment variables:
#   KONSTRUKTOR_TASK       task description (required)
#   KONSTRUKTOR_DIR        working directory (default: /workspace)
#   KONSTRUKTOR_MODE       run | plan (default: plan)
#   KONSTRUKTOR_MODEL      LLM model (default from config)
#   KONSTRUKTOR_TIMEOUT    timeout per step in seconds (default: 1800)
#   KONSTRUKTOR_MAX_ITERS  max plan iterations (default: 3)
#   KONSTRUKTOR_JSON       JSON output (default: false)
#   LLM_API_KEY            API key (required)
#   LLM_BASE_URL           custom base URL (optional)
# ==============================================================================

set -euo pipefail

TASK="${KONSTRUKTOR_TASK:?KONSTRUKTOR_TASK is required}"
MODE="${KONSTRUKTOR_MODE:-plan}"
WORKDIR="${KONSTRUKTOR_DIR:-/workspace}"
MODEL="${KONSTRUKTOR_MODEL:-}"
TIMEOUT="${KONSTRUKTOR_TIMEOUT:-1800}"
MAX_ITERS="${KONSTRUKTOR_MAX_ITERS:-3}"
JSON_FLAG=""

if [ "${KONSTRUKTOR_JSON:-false}" = "true" ]; then
    JSON_FLAG="--json"
fi

# Clone repo if GIT_REPO is set
if [ -n "${GIT_REPO:-}" ]; then
    echo "📦 Cloning ${GIT_REPO}..."
    git clone --depth 1 "${GIT_REPO}" /workspace/repo
    WORKDIR="/workspace/repo"
    if [ -n "${GIT_REF:-}" ]; then
        cd "$WORKDIR" && git fetch --depth 1 origin "${GIT_REF}" && git checkout "${GIT_REF}"
    fi
fi

mkdir -p "$WORKDIR"

echo "══════════════════════════════════════════"
echo "  Konstruktor Runner"
echo "  Mode:      $MODE"
echo "  Task:      $TASK"
echo "  Directory: $WORKDIR"
echo "  Model:     ${MODEL:-<from config>}"
echo "  Timeout:   ${TIMEOUT}s"
echo "══════════════════════════════════════════"

cd "$WORKDIR"

MODEL_ARG=()
if [ -n "$MODEL" ]; then
    MODEL_ARG=(-m "$MODEL")
fi

case "$MODE" in
    plan)
        exec konstruktor plan \
            -t "$TASK" \
            -d "$WORKDIR" \
            "${MODEL_ARG[@]}" \
            --timeout "$TIMEOUT" \
            --max-iterations "$MAX_ITERS" \
            $JSON_FLAG \
            --approve auto
        ;;
    run)
        exec konstruktor run \
            -t "$TASK" \
            -d "$WORKDIR" \
            "${MODEL_ARG[@]}" \
            --timeout "$TIMEOUT" \
            $JSON_FLAG \
            --approve auto
        ;;
    *)
        echo "Unknown mode: $MODE (use 'run' or 'plan')" >&2
        exit 1
        ;;
esac
