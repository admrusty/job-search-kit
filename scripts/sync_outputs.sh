#!/usr/bin/env bash
#
# sync_outputs.sh — commit and push one application package to the
# output directory's git repo (if it is one).
#
# The output directory defaults to ~/Documents/Resumes and can be overridden
# with the RESUME_OUTPUTS_DIR environment variable. This script only does
# anything useful if that directory is itself a git repository with a remote;
# it is optional and safe to skip entirely.
#
# Usage:
#   scripts/sync_outputs.sh "<Company - Role Title>"
#   scripts/sync_outputs.sh "$HOME/Documents/Resumes/<Company - Role Title>"
#
# Idempotent: if the folder has no changes, it prints a notice and exits 0.
# Invoked automatically at the end of the /draft workflow (Step 13), and
# usable on its own to sync any folder under the Resumes output directory.

set -euo pipefail

REPO="${RESUME_OUTPUTS_DIR:-$HOME/Documents/Resumes}"

folder="${1:-}"
if [ -z "$folder" ]; then
  echo "usage: sync_outputs.sh '<Company - Role Title>'" >&2
  exit 1
fi

# Accept either a bare folder name or a full path under $REPO.
case "$folder" in
  "$REPO"/*) rel="${folder#"$REPO"/}" ;;
  /*)        echo "error: path is not under $REPO: $folder" >&2; exit 1 ;;
  *)         rel="$folder" ;;
esac
rel="${rel%/}"  # strip any trailing slash

if [ ! -d "$REPO/$rel" ]; then
  echo "error: folder not found: $REPO/$rel" >&2
  exit 1
fi

git -C "$REPO" add -- "$rel"

if git -C "$REPO" diff --cached --quiet -- "$rel"; then
  echo "No changes to sync for: $rel"
  exit 0
fi

git -C "$REPO" commit -q -m "Add/update application package: $rel"
git -C "$REPO" push -q origin main
echo "Synced '$rel' to the output repo."
