#!/usr/bin/env python3
"""Check on running subagents every N seconds for up to M rounds, in one command. Read-only.

    watch_subagents.py [--every 30] [--rounds 10] ID [ID ...]
    watch_subagents.py --probe            check that this shell can reach Crew (prints what `kirocrew spawn list` says)

It runs `kirocrew spawn list` once per round, so a round costs no model tokens. The loop and the sleeping are
done here, not by the `wait` tool. It stops as soon as every watched run is done.

One line is printed per round (the shell tool shows them when the command ends):
    round 3/10 +60s  a1b2c3d4 running  e5f6a7b8 done
Exit codes: 0 every run is done, 3 rounds used up and some run is still going, 4 a run waits for spawn approval
in the dashboard, 5 an id is not in the list, 2 Crew could not be reached or bad arguments.
"""
import argparse
import os
import re
import subprocess
import sys
import time

LINE = re.compile(r"^\s*(?P<icon>✅|⏳|\U0001f510)\s+(?P<id>[0-9A-Za-z]+)\b")
STATES = {"✅": "done", "⏳": "running", "\U0001f510": "needs-approval"}


def parse_list(text):
    """{id: state} from the output of `kirocrew spawn list`."""
    found = {}
    for line in text.splitlines():
        match = LINE.match(line)
        if match:
            found[match.group("id")] = STATES[match.group("icon")]
    return found


def read_list(binary):
    try:
        result = subprocess.run([binary, "spawn", "list"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"cannot run {binary}: {error}")
    output = (result.stdout or "").strip()
    if result.returncode != 0 or output.startswith("Error:"):
        raise RuntimeError((output or result.stderr.strip() or f"exit {result.returncode}")[:300])
    return parse_list(output), output


def main(argv):
    parser = argparse.ArgumentParser(description="Check subagents every N seconds, M rounds.")
    parser.add_argument("ids", nargs="*", help="run ids from spawn_run")
    parser.add_argument("--every", type=int, default=30, help="seconds between checks (default 30)")
    parser.add_argument("--rounds", type=int, default=10, help="checks before giving up (default 10)")
    parser.add_argument("--kirocrew", default=os.environ.get("KIROCREW_BIN", "kirocrew"), help="the kirocrew program")
    parser.add_argument("--probe", action="store_true", help="only check that Crew can be reached from this shell")
    args = parser.parse_args(argv)
    if not (1 <= args.every <= 3600 and 1 <= args.rounds <= 1000):
        print("error: --every must be 1 to 3600 and --rounds 1 to 1000", file=sys.stderr)
        return 2
    for ident in args.ids:
        if not re.fullmatch(r"[0-9A-Za-z]{1,64}", ident):
            print(f"error: '{ident}' is not a run id", file=sys.stderr)
            return 2
    try:
        if args.probe:
            found, output = read_list(args.kirocrew)
            print(f"ok: reached Crew; {len(found)} subagent(s) listed")
            return 0
        if not args.ids:
            print("error: give at least one run id", file=sys.stderr)
            return 2
        started = time.monotonic()
        for round_number in range(1, args.rounds + 1):
            found, _ = read_list(args.kirocrew)
            states = {ident: found.get(ident, "not-listed") for ident in args.ids}
            elapsed = int(time.monotonic() - started)
            print(f"round {round_number}/{args.rounds} +{elapsed}s  " + "  ".join(f"{i} {s}" for i, s in states.items()), flush=True)
            if any(s == "not-listed" for s in states.values()):
                print("error: a run id is not in `kirocrew spawn list`; check the id", file=sys.stderr)
                return 5
            if all(s == "done" for s in states.values()):
                return 0
            if any(s == "needs-approval" for s in states.values()):
                print("a run is waiting for spawn approval in the dashboard (Approvals)", flush=True)
                return 4
            if round_number < args.rounds:
                time.sleep(args.every)
        print(f"rounds used up after {int(time.monotonic() - started)}s; still running: "
              + ", ".join(i for i, s in states.items() if s != "done"), flush=True)
        return 3
    except RuntimeError as error:
        print(f"error: cannot reach Crew from this shell: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
