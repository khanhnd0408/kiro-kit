#!/usr/bin/env bash
# Install the agent-review kit into a project.
#
# Usage:
#   ./install.sh /path/to/your/project [--surface crew|ide] [--reference-source PATH] [--force]
#
#   --surface crew            (default) Kiro Crew: always-on pointer steering, Task Runner specs,
#                             and hook helpers in ~/.kiro-agent-review/bin/.
#   --surface ide             Kiro IDE: manual steering (#agent-review), custom agent, v1 hooks, workflows.
#   --reference-source PATH   a clone of https://github.com/asgeirtj/system_prompts_leaks.
#                             Default: $SYSTEM_PROMPTS_LEAKS, then ../system_prompts_leaks next to this
#                             repository, then ~/Projects/system_prompts_leaks.
#   --force                   also replace docs/agent-review-method/inputs.md (normally kept).
#
# What it does:
#   1. Copies docs/agent-review-method/ and tools/agent_review/ into the project, plus the files for
#      the chosen surface. Kit files that differ from this version are updated, and the old copies
#      are saved under ~/.kiro-agent-review/backups/. Your inputs.md is kept unless --force is given.
#   2. Copies the files in reference-files.txt into REFERENCE_ROOT, outside the project
#      (default ~/.kiro-agent-review/reference; override with AGENT_REVIEW_REFERENCE=...). When the
#      pinned commit the catalog was checked against exists in the clone, it copies from that commit,
#      so every line number matches.
#   3. Writes REFERENCE_ROOT into docs/agent-review-method/inputs.md.
#   4. Creates docs/agent-review/ and the review-mode flag docs/agent-review/.review-mode.
#   5. Crew only: installs ~/.kiro-agent-review/bin/crew_hooks.py and registers the project in
#      ~/.kiro-agent-review/projects.txt.
#   6. Runs the self-tests and checks every reference citation in the method.
# It never deletes anything and never changes git, Kiro or Crew settings.
set -euo pipefail

PIN="b64ee93"
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_REPO="$(cd "$KIT_DIR/.." && pwd)"
TARGET=""
FORCE=0
SURFACE="crew"
REF_SRC="${SYSTEM_PROMPTS_LEAKS:-}"

usage() { sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --surface) SURFACE="${2:-}"; shift 2 ;;
    --surface=*) SURFACE="${1#*=}"; shift ;;
    --reference-source) REF_SRC="${2:-}"; shift 2 ;;
    --reference-source=*) REF_SRC="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    *) TARGET="$1"; shift ;;
  esac
done

case "$SURFACE" in crew|ide) ;; *) echo "error: --surface must be crew or ide" >&2; exit 2 ;; esac
if [ -z "$TARGET" ]; then usage >&2; exit 2; fi
if [ ! -d "$TARGET" ]; then echo "error: $TARGET is not a directory" >&2; exit 2; fi
TARGET="$(cd "$TARGET" && pwd)"
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 2; }

if [ -z "$REF_SRC" ]; then
  for candidate in "$KIT_REPO/../system_prompts_leaks" "$HOME/Projects/system_prompts_leaks"; do
    if [ -d "$candidate/OpenAI/Codex" ]; then REF_SRC="$candidate"; break; fi
  done
fi
if [ -z "$REF_SRC" ] || [ ! -d "$REF_SRC/OpenAI/Codex" ]; then
  echo "error: can't find a system_prompts_leaks clone." >&2
  echo "  git clone https://github.com/asgeirtj/system_prompts_leaks.git" >&2
  echo "  then rerun with --reference-source /path/to/system_prompts_leaks" >&2
  exit 2
fi
REF_SRC="$(cd "$REF_SRC" && pwd)"
if [ "$TARGET" = "$REF_SRC" ] || [ "$TARGET" = "$KIT_REPO" ]; then
  echo "error: install into your observability project, not into the kit or the reference repository" >&2
  exit 2
fi

AR_HOME="${AGENT_REVIEW_HOME:-$HOME/.kiro-agent-review}"
REF_ROOT="${AGENT_REVIEW_REFERENCE:-$AR_HOME/reference}"
mkdir -p "$REF_ROOT"
REF_ROOT="$(cd "$REF_ROOT" && pwd)"

USER_FILES="docs/agent-review-method/inputs.md"
BACKUP_DIR="$AR_HOME/backups/$(date +%Y%m%d-%H%M%S)-$(basename "$TARGET")"
copied=0; updated=0; unchanged=0; kept=0
copy_tree() {
  local src_root="$1" src rel dst
  while IFS= read -r -d '' src; do
    rel="${src#"$src_root/"}"
    dst="$TARGET/$rel"
    if [ -e "$dst" ]; then
      if cmp -s "$src" "$dst"; then unchanged=$((unchanged + 1)); continue; fi
      if [ "$rel" = "$USER_FILES" ] && [ "$FORCE" -ne 1 ]; then
        echo "  kept (your copy): $rel"; kept=$((kept + 1)); continue
      fi
      mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
      cp "$dst" "$BACKUP_DIR/$rel"
      cp "$src" "$dst"
      echo "  updated: $rel"; updated=$((updated + 1)); continue
    fi
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    echo "  copied: $rel"; copied=$((copied + 1))
  done < <(find "$src_root" -type f -print0 | sort -z)
}

echo "== Project files ($SURFACE) -> $TARGET"
copy_tree "$KIT_DIR/project"
copy_tree "$KIT_DIR/adapters/$SURFACE/project"
echo "  $copied new, $updated updated, $unchanged unchanged, $kept kept"
if [ "$updated" -gt 0 ]; then echo "  old copies of updated files: $BACKUP_DIR"; fi

OTHER="ide"; [ "$SURFACE" = "ide" ] && OTHER="crew"
leftovers=""
while IFS= read -r -d '' src; do
  rel="${src#"$KIT_DIR/adapters/$OTHER/project/"}"
  if [ -e "$TARGET/$rel" ] && [ ! -e "$KIT_DIR/adapters/$SURFACE/project/$rel" ]; then leftovers="$leftovers $rel"; fi
done < <(find "$KIT_DIR/adapters/$OTHER/project" -type f -print0 | sort -z)
if [ -n "$leftovers" ]; then
  echo "  note: files from an earlier $OTHER install are still here and aren't used by $SURFACE:"
  for rel in $leftovers; do echo "    $rel"; done
  echo "  Delete them if you don't use $OTHER on this project."
fi

echo "== Reference files -> $REF_ROOT"
from_pin=0
if git -C "$REF_SRC" cat-file -e "$PIN^{commit}" 2>/dev/null; then from_pin=1; fi
count=0; missing=0
while IFS= read -r rel || [ -n "$rel" ]; do
  case "$rel" in ''|'#'*) continue ;; esac
  mkdir -p "$REF_ROOT/$(dirname "$rel")"
  if [ "$from_pin" -eq 1 ]; then
    if git -C "$REF_SRC" show "$PIN:$rel" > "$REF_ROOT/$rel" 2>/dev/null; then count=$((count + 1)); continue; fi
    rm -f "$REF_ROOT/$rel"
  elif [ -f "$REF_SRC/$rel" ]; then
    cp "$REF_SRC/$rel" "$REF_ROOT/$rel"; count=$((count + 1)); continue
  fi
  echo "  missing: $rel" >&2; missing=$((missing + 1))
done < "$KIT_DIR/reference-files.txt"
if [ "$from_pin" -eq 1 ]; then
  echo "  $count files copied from commit $PIN of $REF_SRC, $missing missing"
else
  echo "  $count files copied from the working tree of $REF_SRC, $missing missing"
  echo "  warning: commit $PIN isn't in that clone, so some line numbers may have drifted." >&2
  echo "  Run 'git -C $REF_SRC fetch' and install again to get exact matches." >&2
fi

INPUTS="$TARGET/docs/agent-review-method/inputs.md"
python3 -I - "$INPUTS" "$REF_ROOT" <<'PY'
import re, sys
path, root = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    text = handle.read()
with open(path, "w", encoding="utf-8") as handle:
    handle.write(re.sub(r"(?m)^(REFERENCE_ROOT:\s*)\S+", lambda m: m.group(1) + root, text, count=1))
print(f"== REFERENCE_ROOT set in docs/agent-review-method/inputs.md: {root}")
PY

mkdir -p "$TARGET/docs/agent-review"
if [ ! -e "$TARGET/docs/agent-review/.review-mode" ]; then
  echo "Review mode is on: the agent-review guard keeps the project read-only. Delete this file before Phase 6." > "$TARGET/docs/agent-review/.review-mode"
fi
echo "== Review mode flag: docs/agent-review/.review-mode"

PY="$(command -v python3)"
if [ "$SURFACE" = "crew" ]; then
  mkdir -p "$AR_HOME/bin"
  cp "$KIT_DIR/adapters/crew/home/bin/crew_hooks.py" "$AR_HOME/bin/crew_hooks.py"
  touch "$AR_HOME/projects.txt"
  grep -qxF "$TARGET" "$AR_HOME/projects.txt" || echo "$TARGET" >> "$AR_HOME/projects.txt"
  echo "== Crew hook helper: $AR_HOME/bin/crew_hooks.py (project registered in $AR_HOME/projects.txt)"
fi

echo "== Checks"
check_failed() {
  echo "" >&2
  echo "A check failed: $1" >&2
  echo "The lines above say which part. Things to try:" >&2
  echo "  - Rerun this installer from the latest kit (git pull first). It updates outdated kit files in the project." >&2
  echo "  - Check that python3 is 3.7 or newer: python3 --version" >&2
  echo "  - If it still fails, open an issue with the full output of this installer." >&2
  exit 1
}
(cd "$TARGET" && python3 -I tools/agent_review/check_citations.py --self-test) || check_failed "citation checker self-test"
(cd "$TARGET" && python3 -I tools/agent_review/guard.py --self-test) || check_failed "guard self-test"
(cd "$TARGET" && python3 -I tools/agent_review/check_citations.py --catalog) || check_failed "catalog citations against REFERENCE_ROOT"
if [ "$SURFACE" = "crew" ]; then
  python3 -I "$AR_HOME/bin/crew_hooks.py" self-test --project "$TARGET" || check_failed "Crew hook self-test"
fi

if [ "$SURFACE" = "crew" ]; then
cat <<EOF

Installed for Kiro Crew. Next steps (details in $KIT_DIR/adapters/crew/README.md):
  1. Point Crew at the project: set it in the dashboard session header, or run
       kirocrew config set project_dir "$TARGET"
  2. Commit the kit on a review branch. Task Runner worktrees only contain committed files:
       cd "$TARGET" && git checkout -b agent-review
       git add docs/agent-review-method tools/agent_review .kiro/steering/agent-review.md docs/agent-review/.review-mode
       git commit -m "Add agent-review kit"
  3. Smoke test, from the project folder:
       kirocrew run docs/agent-review-method/crew/TASK-smoke.md --no-test
  4. Start Phase 1 in a Crew chat:
       agent-review: Run Phase 1. Stop when Phase 1 is done.
  5. Optional hooks (dashboard: Agent Capabilities -> Hooks). Use these exact commands:
       UserPromptSubmit, contains "agent-review:"   $PY -I $AR_HOME/bin/crew_hooks.py inject
       Stop, contains "agent-review:"               $PY -I $AR_HOME/bin/crew_hooks.py stop
       PreToolUse, "*" (create disabled, Test, then enable)   $PY -I $AR_HOME/bin/crew_hooks.py guard
EOF
else
cat <<EOF

Installed for Kiro IDE. Next steps (details in $KIT_DIR/adapters/ide/README.md):
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

  3. Run the smoke tests in the IDE guide, then start Phase 1 with:  #agent-review Run Phase 1. Stop when Phase 1 is done.
EOF
fi
