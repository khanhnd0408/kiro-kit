"""Optional check: do the agent files crewctl writes load in Kiro Crew's own code?

It applies the shipped examples into a temp folder, then asks the Crew Python installed on this
machine (~/.kiro/crew-venv) to list the agents in that folder and to validate each crewmate name.
Crew's code runs with HOME and KIROCREW_HOME pointed at temp folders, so it cannot read or
change your real Crew data. It never starts the gateway and never runs kiro-cli or kirocrew.

Run:  python3 tests/crosscheck_with_crew.py
Exit: 0 ok, 1 mismatch, 3 Crew not installed here (skipped).
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crewctl import cli  # noqa: E402

CREW_PYTHON = os.path.join(os.path.expanduser("~"), ".kiro", "crew-venv", "bin", "python")

CHECK = r"""
import json, sys
from pathlib import Path
from kiro_crew.agent_discovery import list_agents
from kiro_crew.members import validate_member_name
from kiro_crew.validation import TEMPLATE_NAME_RE
agents_dir = Path(sys.argv[1])
names = json.loads(sys.argv[2])
found = {a.name: a for a in list_agents(agents_dir)}
out = {"listed": sorted(found), "name_errors": {}, "file_name_errors": []}
for n in names:
    try:
        validate_member_name(n)
    except Exception as exc:
        out["name_errors"][n] = str(exc)
    if not TEMPLATE_NAME_RE.fullmatch(n):
        out["file_name_errors"].append(n)
print(json.dumps(out))
"""


def main():
    if not os.path.exists(CREW_PYTHON):
        print("Crew is not installed here (no ~/.kiro/crew-venv); skipped.")
        return 3
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = os.path.join(tmp, "crew")
        shutil.copytree(os.path.join(ROOT, "examples", "crew"), config_dir)
        agents_dir = os.path.join(tmp, "agents")
        crew_home = os.path.join(tmp, "crewhome")
        os.makedirs(agents_dir)
        os.makedirs(crew_home)
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["-c", config_dir, "--agents-dir", agents_dir, "--crew-home", crew_home, "apply", "--yes"])
        if code != 0:
            print("crewctl apply failed")
            return 1
        names = sorted(f[:-5] for f in os.listdir(agents_dir) if f.endswith(".json"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.path.join(tmp, "home"), "KIROCREW_HOME": crew_home,
               "PYTHONDONTWRITEBYTECODE": "1"}
        os.makedirs(env["HOME"])
        result = subprocess.run([CREW_PYTHON, "-c", CHECK, agents_dir, json.dumps(names)], capture_output=True,
                                text=True, env=env, timeout=120)
        if result.returncode != 0:
            print("Crew's code failed to run the check:")
            print(result.stderr[-1500:])
            return 1
        data = json.loads(result.stdout.strip().splitlines()[-1])
        print(f"agents crewctl wrote:   {names}")
        print(f"agents Crew lists:      {data['listed']}")
        problems = []
        missing = sorted(set(names) - set(data["listed"]))
        if missing:
            problems.append(f"Crew does not list: {missing}")
        if data["name_errors"]:
            problems.append(f"Crew rejects names: {data['name_errors']}")
        if data["file_name_errors"]:
            problems.append(f"Crew rejects agent file names: {data['file_name_errors']}")
        if problems:
            print("MISMATCH")
            for line in problems:
                print("  " + line)
            return 1
        print("OK: Crew's own code accepts every file and name crewctl wrote.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
