#!/usr/bin/env bash
# Produce the three coverage JSONs for the residency metric.
# Run from the repo/worktree root. Sequential (one suite at a time -> CPU-safe).
# NOTE: pytest may exit nonzero (failing/diverging cases) -- irrelevant to
# coverage capture, so we do NOT set -e / pipefail.
REPO="${1:?repo root}"
OUT=/tmp/agents
cd "$REPO" || exit 1
export PYTHONPATH=/tmp/agents

run() {
  local label="$1" json="$2" mode="$3"; shift 3
  echo "=== [$label] $* ==="
  RESID_ONLY="$mode" uv run --with coverage --with pytest-cov pytest \
    tests/test_case.py -q -p no:cacheprovider -p thir_resid_plugin \
    --no-exec --cov=tpyc/codegen_cpp "--cov-report=json:$OUT/$json" "$@" \
    > "$OUT/${label}.runlog" 2>&1
  echo "  exit=$? json=$OUT/$json ($(wc -c < "$OUT/$json" 2>/dev/null || echo MISSING) bytes)"
  tail -4 "$OUT/${label}.runlog"
}

run U U.json unmarked --thir-codegen
run A A.json all      --thir-codegen
run D D.json all      --no-thir
echo "=== DONE ==="
