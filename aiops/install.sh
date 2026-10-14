#!/usr/bin/env bash
# Install the agent-review kit into a project, and copy the curated reference files.
#
# Usage:
#   ./install.sh /path/to/your/project [--force]
#
# What it does:
#   1. Copies .kiro/ (steering, agent, hooks, workflows), docs/agent-review-method/ and
#      tools/agent_review/ into the project. Existing files are skipped unless --force is given.
#   2. Copies the reference files listed in reference-files.txt into REFERENCE_ROOT, outside the
#      project (default: ~/.kiro-agent-review/reference; override with AGENT_REVIEW_REFERENCE=...).
#   3. Writes REFERENCE_ROOT into docs/agent-review-method/inputs.md.
#   4. Creates docs/agent-review/ and the review-mode flag docs/agent-review/.review-mode.
#   5. Runs the self-tests and checks every reference citation in the method.
# It never deletes anything and never touches git or Kiro settings.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$KIT_DIR/.." && pwd)"
TARGET=""
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

if [ -z "$TARGET" ]; then
  echo "usage: $0 /path/to/your/project [--force]" >&2
  exit 2
fi
if [ ! -d "$TARGET" ]; then
  echo "error: $TARGET is not a directory" >&2
  exit 2
fi
TARGET="$(cd "$TARGET" && pwd)"
if [ "$TARGET" = "$REPO_DIR" ]; then
  echo "error: install into your observability project, not into the reference repository" >&2
  exit 2
fi
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 2; }

REF_ROOT="${AGENT_REVIEW_REFERENCE:-$HOME/.kiro-agent-review/reference}"
mkdir -p "$REF_ROOT"
REF_ROOT="$(cd "$REF_ROOT" && pwd)"

echo "== Project files -> $TARGET"
copied=0; skipped=0
while IFS= read -r -d '' src; do
  rel="${src#"$KIT_DIR/project/"}"
  dst="$TARGET/$rel"
  if [ -e "$dst" ] && [ "$FORCE" -ne 1 ]; then
    echo "  skip (exists): $rel"; skipped=$((skipped + 1)); continue
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "  copied: $rel"; copied=$((copied + 1))
done < <(find "$KIT_DIR/project" -type f -print0 | sort -z)
echo "  $copied copied, $skipped skipped"

echo "== Reference files -> $REF_ROOT"
missing=0; count=0
while IFS= read -r rel || [ -n "$rel" ]; do
  case "$rel" in ''|'#'*) continue ;; esac
  if [ ! -f "$REPO_DIR/$rel" ]; then
    echo "  missing in source repo: $rel" >&2; missing=$((missing + 1)); continue
  fi
  mkdir -p "$REF_ROOT/$(dirname "$rel")"
  cp "$REPO_DIR/$rel" "$REF_ROOT/$rel"
  count=$((count + 1))
done < "$KIT_DIR/reference-files.txt"
echo "  $count files copied, $missing missing"

INPUTS="$TARGET/docs/agent-review-method/inputs.md"
python3 -I - "$INPUTS" "$REF_ROOT" <<'PY'
import re, sys
path, root = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    text = handle.read()
new = re.sub(r"(?m)^(REFERENCE_ROOT:\s*)\S+", lambda m: m.group(1) + root, text, count=1)
with open(path, "w", encoding="utf-8") as handle:
    handle.write(new)
print(f"== REFERENCE_ROOT set in docs/agent-review-method/inputs.md: {root}")
PY

mkdir -p "$TARGET/docs/agent-review"
if [ ! -e "$TARGET/docs/agent-review/.review-mode" ]; then
  echo "Review mode is on: the agent-review guard keeps the project read-only. Delete this file before Phase 6." > "$TARGET/docs/agent-review/.review-mode"
fi
echo "== Review mode flag: docs/agent-review/.review-mode"

echo "== Checks"
(cd "$TARGET" && python3 -I tools/agent_review/check_citations.py --self-test)
(cd "$TARGET" && python3 -I tools/agent_review/guard.py --self-test)
(cd "$TARGET" && python3 -I tools/agent_review/check_citations.py --catalog)

cat <<EOF

Installed. Next steps (details in $KIT_DIR/README.md):
  1. Open $TARGET in Kiro and trust the workspace.
  2. Add these rules to ~/.kiro/settings/permissions.yaml (create it if needed; the agent cannot write it):

rules:
  - capability: fs_read
    match: ["$REF_ROOT/**"]
    effect: allow
  - capability: fs_write
    match: ["docs/agent-review/**"]
    effect: allow
  - capability: shell
    match: ["ls", "ls *", "grep *", "rg *", "cat *", "head *", "tail *", "sed -n *", "wc *", "sort *", "uniq *", "cut *", "find *", "git log*", "git show*", "git blame*", "git diff*", "git ls-files*", "git grep*", "git rev-parse*", "mkdir -p docs/agent-review*", "python3 -I tools/agent_review/check_citations.py*"]
    effect: allow
  - capability: shell
    match: ["sed -i*", "find * -delete*", "find * -exec*", "cat *.env*"]
    effect: deny

  3. Run the smoke tests in the README, then start Phase 1 with:  #agent-review Run Phase 1. Stop when Phase 1 is done.
EOF
