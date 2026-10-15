#!/usr/bin/env python3
"""Kiro Crew hook helpers for the agent-review method.

install.sh copies this file to ~/.kiro-agent-review/bin/. Crew hooks are global
(~/.kiro/crew/hooks.json), so every command does nothing unless the session belongs
to a project where the agent-review kit is installed. That means a project containing
docs/agent-review-method/inputs.md.

  crew_hooks.py inject     UserPromptSubmit hook. Prints the review rules and inputs.
                           Crew adds a UserPromptSubmit hook's stdout to the model context.
  crew_hooks.py stop       Stop hook. Checks citations in recently changed deliverables.
                           If there are problems, it prints {"decision": "block", "reason": ...}
                           so the agent keeps working. It blocks at most twice for the same problems.
  crew_hooks.py guard      PreToolUse hook. Runs the project's tools/agent_review/guard.py.
                           Exit 2 blocks the call, and Crew sends stderr to the model.
  crew_hooks.py self-test --project PATH [--verbose]

Everything fails open: on any internal error it exits 0 with no output. A PreToolUse hook
that crashes or times out blocks every tool call in Crew, so this script never raises.
Standard library only. No network.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

MARKER = os.path.join("docs", "agent-review-method", "inputs.md")
RULES = os.path.join("docs", "agent-review-method", "00-rules.md")
INPUTS = MARKER
CHECKER = os.path.join("tools", "agent_review", "check_citations.py")
GUARD = os.path.join("tools", "agent_review", "guard.py")
NOTE = os.path.join("docs", "agent-review", "tmp", "unresolved-citations.md")
DIR_KEYS = ("cwd", "project_dir", "projectDir", "workspace", "workspace_dir", "working_directory",
            "workingDirectory", "workdir", "root")
TOOL_NAME_KEYS = ("tool_name", "toolName", "tool", "name")
TOOL_INPUT_KEYS = ("tool_input", "toolInput", "input", "arguments", "args", "params", "parameters")
RECENT_MINUTES = 120
MAX_BLOCKS = 2
BLOCK_WINDOW_SECS = 1800


def home():
    return os.environ.get("AGENT_REVIEW_HOME") or os.path.join(os.path.expanduser("~"), ".kiro-agent-review")


def read_payload():
    try:
        raw = sys.stdin.read(1024 * 1024)
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def candidate_dirs(payload):
    found = []

    def collect(value, depth=0):
        if depth > 2 or not isinstance(value, dict):
            return
        for key in DIR_KEYS:
            if isinstance(value.get(key), str):
                found.append(value[key])
        for item in value.values():
            if isinstance(item, dict):
                collect(item, depth + 1)

    collect(payload)
    context = os.environ.get("KIROCREW_HOOK_CONTEXT", "")
    if context:
        try:
            collect(json.loads(context))
        except ValueError:
            if os.path.isdir(context):
                found.append(context)
    return [d for d in found if d and os.path.isdir(os.path.expanduser(d))]


def find_root(start):
    current = os.path.abspath(os.path.expanduser(start))
    while True:
        if os.path.isfile(os.path.join(current, MARKER)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def registered_projects():
    path = os.path.join(home(), "projects.txt")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    except OSError:
        return []
    return [p for p in lines if os.path.isfile(os.path.join(p, MARKER))]


def project_root(payload, allow_registry):
    for directory in candidate_dirs(payload):
        root = find_root(directory)
        if root:
            return root
    if allow_registry:
        projects = registered_projects()
        if len(projects) == 1:
            return projects[0]
    return None


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def cmd_inject(payload):
    root = project_root(payload, allow_registry=True)
    if not root or not os.path.isfile(os.path.join(root, RULES)):
        return 0
    print(f"The user is running the agent-review method in {root}. Follow these rules for this session, "
          "then read the phase file named in the user's message before starting.\n")
    print(read_text(os.path.join(root, RULES)))
    print("\n# Project inputs\n")
    print(read_text(os.path.join(root, INPUTS)))
    return 0


def load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
    except OSError:
        pass


def cmd_stop(payload):
    root = project_root(payload, allow_registry=True)
    checker = os.path.join(root, CHECKER) if root else ""
    if not root or not os.path.isfile(checker):
        return 0
    command = [sys.executable, "-I", checker, "--root", root]
    try:
        proc = subprocess.run(command + ["--recent", str(RECENT_MINUTES)], capture_output=True, text=True, timeout=60)
        if proc.returncode == 2 and "--recent" in proc.stderr:
            # An older checker without --recent: check every deliverable instead.
            proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 0
    state_path = os.path.join(home(), "state", "stop.json")
    state = load_state(state_path)
    if proc.returncode != 1:
        if proc.returncode == 0 and root in state:
            state.pop(root, None)
            save_state(state_path, state)
        return 0
    problems = proc.stderr.strip()
    digest = hashlib.sha256((root + "\n" + problems).encode("utf-8")).hexdigest()
    entry = state.get(root, {})
    now = time.time()
    same = entry.get("digest") == digest and now - entry.get("time", 0) < BLOCK_WINDOW_SECS
    count = entry.get("count", 0) + 1 if same else 1
    if same and entry.get("count", 0) >= MAX_BLOCKS:
        try:
            note = os.path.join(root, NOTE)
            os.makedirs(os.path.dirname(note), exist_ok=True)
            with open(note, "w", encoding="utf-8") as handle:
                handle.write(f"# Unresolved citation problems ({time.strftime('%Y-%m-%d %H:%M:%S %Z')})\n\n"
                             "The Stop hook stopped blocking after repeated attempts. Fix these by hand or ask the agent again.\n\n"
                             f"```text\n{problems}\n```\n")
        except OSError:
            pass
        return 0
    state[root] = {"digest": digest, "count": count, "time": now}
    save_state(state_path, state)
    lines = problems.splitlines()
    shown = "\n".join(lines[:40]) + (f"\n... and {len(lines) - 40} more" if len(lines) > 40 else "")
    reason = ("The agent-review citation check found problems in deliverables changed recently. Fix each one: "
              "re-open the cited file and correct the path or line, or add the missing field. Then run "
              "`python3 -I tools/agent_review/check_citations.py` and end your reply with the agent-review status line.\n\n"
              + shown)
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def normalized(payload):
    data = dict(payload)
    if "tool_name" not in data:
        for key in TOOL_NAME_KEYS:
            if isinstance(data.get(key), str):
                data["tool_name"] = data[key]
                break
    if "tool_input" not in data:
        for key in TOOL_INPUT_KEYS:
            if key in data:
                data["tool_input"] = data[key]
                break
    return data


def cmd_guard(payload):
    root = project_root(payload, allow_registry=False)
    guard = os.path.join(root, GUARD) if root else ""
    if not root or not os.path.isfile(guard):
        return 0
    data = normalized(payload)
    data["cwd"] = root
    try:
        proc = subprocess.run([sys.executable, "-I", guard], input=json.dumps(data),
                              capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return 0
    if proc.returncode == 2:
        sys.stderr.write(proc.stderr)
        return 2
    return 0


def outside_temp_base():
    """Return a writable temp folder that isn't inside a review project.

    Sandboxes, Crew's agent sessions among them, sometimes point TMPDIR into the workspace.
    """
    for base in (tempfile.gettempdir(), "/tmp", "/var/tmp", os.path.expanduser("~")):
        if base and os.path.isdir(base) and os.access(base, os.W_OK) and not find_root(base):
            return base
    return None


def write_now(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    now = time.time()
    os.utime(path, (now, now))  # explicit times: some network filesystems keep their own clock


def self_test(project, verbose=False):
    if not project or not os.path.isfile(os.path.join(project, MARKER)):
        print("self-test needs --project PATH pointing at a project with the kit installed", file=sys.stderr)
        return 2
    project = os.path.abspath(project)
    me = os.path.abspath(__file__)
    try:
        usage = subprocess.run([sys.executable, "-I", os.path.join(project, CHECKER), "--help"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        usage = ""
    if "--recent" not in usage:
        print("warning: the project's tools/agent_review/check_citations.py is from an older kit. The stop hook "
              "will check every deliverable instead of recent ones. Rerun the latest install.sh to update it.",
              file=sys.stderr)
    base = outside_temp_base()
    system_tmp = tempfile.gettempdir()
    info = [
        f"python: {sys.version.split()[0]} ({sys.executable}) on {platform.platform()}",
        f"hook helper: {me}",
        f"project: {project} (checker supports --recent: {'yes' if '--recent' in usage else 'no'})",
        f"TMPDIR: {system_tmp} (inside a review project: {'yes' if find_root(system_tmp) else 'no'}); "
        f"self-test temp base: {base or system_tmp}",
        "KIROCREW_* variables: " + (", ".join(sorted(k for k in os.environ if k.startswith("KIROCREW"))) or "none"),
        f"stdout encoding: {sys.stdout.encoding}",
    ]
    failures = []

    def check(name, ok, result=None):
        if ok:
            return
        detail = ""
        if result is not None:
            code, out, err = result
            detail = f"\n      exit={code} stdout={out.strip()[:300]!r} stderr={err.strip()[:300]!r}"
        failures.append(name + detail)

    with tempfile.TemporaryDirectory(dir=base) as tmp:
        root, fake_home = os.path.join(tmp, "proj"), os.path.join(tmp, "home")
        for rel in (RULES, INPUTS, CHECKER, GUARD):
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            shutil.copy(os.path.join(project, rel), os.path.join(root, rel))
        os.makedirs(os.path.join(root, "src"))
        write_now(os.path.join(root, "src", "app.py"), "a = 1\nb = 2\n")
        cards = os.path.join(root, "docs", "agent-review", "scorecards")
        os.makedirs(cards)
        os.makedirs(fake_home)
        env = dict(os.environ, AGENT_REVIEW_HOME=fake_home)
        env.pop("KIROCREW_HOOK_CONTEXT", None)

        def run(sub, payload, raw=None):
            try:
                proc = subprocess.run([sys.executable, "-I", me, sub],
                                      input=raw if raw is not None else json.dumps(payload), capture_output=True,
                                      encoding="utf-8", errors="replace", env=env, timeout=60)
                return proc.returncode, proc.stdout or "", proc.stderr or ""
            except (OSError, subprocess.SubprocessError) as error:
                return -1, "", f"could not run '{sub}': {error}"

        result = run("inject", {"cwd": os.path.join(root, "src")})
        check("inject should print the rules when cwd is inside the project",
              result[0] == 0 and "Agent review method: rules" in result[1], result)
        if base:
            result = run("inject", {"cwd": tmp})
            check("inject should print nothing outside a review project", not result[1].strip(), result)
        else:
            info.append("note: skipped the outside-a-project check, because every temp folder found is inside a review project")

        bad = os.path.join(cards, "logs.md")
        write_now(bad, "# Scorecard: logs\nSee `src/app.py:99`.\n")
        results = [run("stop", {"cwd": root}) for _ in range(3)]
        first = results[0][1].strip()
        try:
            blocked = first.startswith("{") and json.loads(first).get("decision") == "block"
        except ValueError:
            blocked = False
        check("stop should block when a recent deliverable has a bad citation", blocked, results[0])
        check("stop should block a second time for the same problems", results[1][1].strip().startswith("{"), results[1])
        check("stop should allow the third time and write the unresolved note",
              not results[2][1].strip() and os.path.isfile(os.path.join(root, NOTE)), results[2])
        write_now(bad, "# Scorecard: logs\nSee `src/app.py:2`.\n")
        result = run("stop", {"cwd": root})
        check("stop should print nothing when citations are fine", not result[1].strip(), result)

        write_now(os.path.join(fake_home, "projects.txt"), root + "\n")
        result = run("inject", {})
        check("inject should fall back to the single registered project when no cwd is given",
              "Agent review method: rules" in result[1], result)

        write_out = {"cwd": root, "tool_name": "fs_write", "tool_input": {"path": "src/app.py"}}
        result = run("guard", write_out)
        check("guard should allow everything when review mode is off", result[0] == 0, result)
        write_now(os.path.join(root, "docs", "agent-review", ".review-mode"), "on\n")
        result = run("guard", write_out)
        check("guard should block a write outside docs/agent-review in review mode",
              result[0] == 2 and "agent-review guard" in result[2], result)
        result = run("guard", {"cwd": root, "tool": "fs_write", "arguments": {"path": "docs/agent-review/x.md"}})
        check("guard should allow a deliverable write given alternative key names", result[0] == 0, result)
        result = run("guard", {"cwd": root, "tool": "execute_bash", "arguments": {"command": "curl http://x"}})
        check("guard should block curl given alternative key names", result[0] == 2, result)
        result = run("guard", {}, raw="not json")
        check("guard should allow input it can't parse", result[0] == 0, result)
        result = run("guard", {"tool_name": "fs_write"})
        check("guard should allow when it can't tell which project the call belongs to", result[0] == 0, result)
    if failures:
        print("crew_hooks self-test FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print("  environment:", file=sys.stderr)
        for line in info:
            print(f"    {line}", file=sys.stderr)
        print("  Paste everything from 'crew_hooks self-test FAILED' down when you ask for help.", file=sys.stderr)
        return 1
    if verbose:
        for line in info:
            print(f"  {line}")
    print("crew_hooks self-test ok")
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    command = argv[0] if argv else ""
    if command == "self-test":
        project = argv[argv.index("--project") + 1] if "--project" in argv and argv.index("--project") + 1 < len(argv) else ""
        return self_test(project, verbose="--verbose" in argv)
    handlers = {"inject": cmd_inject, "stop": cmd_stop, "guard": cmd_guard}
    if command not in handlers:
        print(__doc__, file=sys.stderr)
        return 0
    try:
        return handlers[command](read_payload())
    except Exception:  # never let a hook crash; see module docstring
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
