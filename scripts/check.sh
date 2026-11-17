#!/usr/bin/env bash
# Run every check; print one line per passing step and full output only for failures.
# Later tasks append steps to STEPS as "label|directory|command".
set -u
cd "$(dirname "$0")/.."

STEPS=(
  "backend: ruff check|backend|uv run ruff check"
  "backend: ruff format --check|backend|uv run ruff format --check"
  "backend: mypy|backend|uv run mypy"
  "backend: pytest|backend|uv run pytest -q"
  "frontend: lint|frontend|npm run lint"
  "frontend: typecheck|frontend|npm run typecheck"
  "frontend: test|frontend|npm test"
)

failed=0
for step in "${STEPS[@]}"; do
  IFS='|' read -r label dir cmd <<<"$step"
  if out=$(cd "$dir" && eval "$cmd" 2>&1); then
    echo "✓ $label"
  else
    echo "✗ $label"
    echo "$out"
    failed=1
  fi
done
exit "$failed"
