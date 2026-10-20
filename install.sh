#!/usr/bin/env bash
# Install the `crewctl` command from this repository.
#
# Usage:
#   ./install.sh              install (safe to run again)
#   ./install.sh --uninstall  remove what this script created
#
# What it does:
#   1. Makes a private Python environment in ./.venv inside this repository (so it never touches the
#      system Python and works on systems that refuse `pip install`).
#   2. Installs crewctl into it in editable mode, so the command reads the files in this repository.
#   3. Links it as ~/.local/bin/crewctl.
# It needs python3 (3.8 or newer) with the venv module, and network access to fetch pip's build tools.
# It does not touch Kiro, Crew, or anything outside this repository and ~/.local/bin/crewctl.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
BIN_DIR="${CREWCTL_BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/crewctl"

if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$VENV/bin/crewctl" ]; then rm "$LINK"; echo "removed $LINK"; fi
  if [ -d "$VENV" ]; then rm -r "$VENV"; echo "removed $VENV"; fi
  echo "done"
  exit 0
fi
if [ $# -gt 0 ]; then echo "usage: ./install.sh [--uninstall]" >&2; exit 2; fi

command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 2; }
python3 - <<'PY' || { echo "error: Python 3.8 or newer is required" >&2; exit 2; }
import sys
sys.exit(0 if sys.version_info >= (3, 8) else 1)
PY

if [ ! -x "$VENV/bin/python" ]; then
  echo "creating $VENV"
  python3 -m venv "$VENV" || { echo "error: could not create a virtual environment (on Debian or Ubuntu: sudo apt install python3-venv)" >&2; exit 2; }
fi
echo "installing crewctl"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -e "$REPO/crewctl"

mkdir -p "$BIN_DIR"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "error: $LINK exists and is not a link made by this script; move it away and run again" >&2
  exit 2
fi
ln -sfn "$VENV/bin/crewctl" "$LINK"

echo "installed: $("$LINK" --version)"
case ":$PATH:" in
  *":$BIN_DIR:"*) echo "ready: run  crewctl" ;;
  *) echo "add this line to your shell profile, then open a new terminal:"
     echo "  export PATH=\"$BIN_DIR:\$PATH\""
     echo "or run it now with: $LINK" ;;
esac
