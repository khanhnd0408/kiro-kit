#!/usr/bin/env bash
# Put the `crewctl` command on your PATH.
#
# Usage:
#   ./install.sh              link bin/crewctl as ~/.local/bin/crewctl (safe to run again)
#   ./install.sh --uninstall  remove that link
#
# crewctl uses only the Python standard library, so there is nothing to pip-install and no virtual
# environment. The command runs the files in this repository with whichever `python3` is on your PATH
# when you run it (a conda environment, a venv, the system Python), or the one in CREWCTL_PYTHON.
# Keep this repository where it is. The script changes nothing except the one link.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$REPO/bin/crewctl"
BIN_DIR="${CREWCTL_BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/crewctl"

if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$LAUNCHER" ]; then rm "$LINK"; echo "removed $LINK"; else echo "nothing to remove at $LINK"; fi
  exit 0
fi
if [ $# -gt 0 ]; then echo "usage: ./install.sh [--uninstall]" >&2; exit 2; fi

[ -x "$LAUNCHER" ] || { echo "error: $LAUNCHER is missing or not executable" >&2; exit 2; }
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 2; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { echo "error: Python 3.8 or newer is required (this one: $(python3 --version 2>&1))" >&2; exit 2; }

mkdir -p "$BIN_DIR"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "error: $LINK exists and is not a link made by this script; move it away and run again" >&2
  exit 2
fi
ln -sfn "$LAUNCHER" "$LINK"

echo "installed: $("$LINK" --version)   (python3 = $(command -v python3))"
case ":$PATH:" in
  *":$BIN_DIR:"*) echo "ready: run  crewctl" ;;
  *) echo "add this line to your shell profile, then open a new terminal:"
     echo "  export PATH=\"$BIN_DIR:\$PATH\""
     echo "or run it now with: $LINK" ;;
esac
