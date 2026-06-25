#!/usr/bin/env bash
# Auto-commit any pending changes in the builder workspace repo.
# Invoked by the Stop hook in .claude/settings.json at the end of each turn.
# Stages everything (tracked edits + new untracked files) and commits. Never pushes.
set -uo pipefail

# Self-locate the workspace root (the parent of this scripts/ directory).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Nothing changed → quiet no-op.
[ -z "$(git status --porcelain)" ] && exit 0

git add -A

# Staging may still leave nothing (e.g. only ignored files) → bail.
git diff --cached --quiet && exit 0

git commit -q -m "Auto-commit: workspace changes $(date '+%Y-%m-%d %H:%M:%S')" || exit 0
exit 0
