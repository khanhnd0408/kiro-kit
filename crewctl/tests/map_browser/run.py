#!/usr/bin/env python3
"""Run the browser checks of `crewctl map`: build the page, then click through it with Playwright.

    python3 tests/map_browser/run.py                 (from the crewctl folder)
    CREWCTL_MAP_CHANNEL=chrome python3 tests/map_browser/run.py     (use the installed Google Chrome)

Needs Playwright for Python and a Chromium (`pip install playwright && playwright install chromium`). Screenshots are
kept in a temp folder whose path is printed at the end. Exit code 0 when every check passed.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def main():
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Playwright for Python is not installed: pip install playwright && playwright install chromium")
        return 2
    from crewctl import cli, cmdmap
    work = tempfile.mkdtemp(prefix="crewctl-map-check-")
    page = os.path.join(work, "crewctl-map.html")
    with open(page, "w", encoding="utf-8") as handle:
        handle.write(cmdmap.render(cmdmap.build_data(cli.build_parser())))
    shots = os.path.join(work, "shots")
    os.makedirs(shots)
    env = dict(os.environ, CREWCTL_MAP_URL="file://" + page, CREWCTL_MAP_SHOTS=shots)
    failed = 0
    for name, args in (("check_map.py", []), ("check_glass.py", []), ("check_remove.py", []), ("check_esc.py", ["7", "80"])):
        print(f"== {name}")
        result = subprocess.run([sys.executable, os.path.join(HERE, name)] + args, env=env, capture_output=True, text=True, timeout=900)
        lines = (result.stdout + result.stderr).strip().splitlines()
        for line in lines:
            if line.startswith(("FAIL", "Traceback", "  step")) or "passed" in line or "seed" in line or "Error" in line:
                print("  " + line)
        bad = result.returncode != 0 or any(l.startswith("FAIL") for l in lines) or any(" 0 times" not in l for l in lines if l.startswith("seed"))
        failed += bad
    print(f"\nScreenshots: {shots}")
    print("All browser checks passed." if not failed else f"{failed} check file(s) had failures.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
