#!/usr/bin/env python3
"""PreToolUse guard for the agent-review method (Kiro hook).

While docs/agent-review/.review-mode exists, the review is read-only for the
project. This guard blocks:
- file writes outside docs/agent-review/;
- shell commands that reach the network, install packages, change git state,
  modify files outside docs/agent-review/, or run interpreters (except the
  citation checker);
- MCP and web tools (they reach live systems).

Kiro passes the tool call as JSON on stdin (hook_event_name, cwd, session_id,
tool_name, tool_input). Exit 0 allows the call. Exit 2 blocks it, and Kiro
returns stderr to the agent. The guard fails open: if it can't understand a
call, it allows it, so permissions remain the primary control.

Self-test: python3 -I tools/agent_review/guard.py --self-test
"""

import json
import os
import re
import shlex
import sys

FLAG = os.path.join("docs", "agent-review", ".review-mode")
DELIVERABLES = os.path.join("docs", "agent-review")

PATH_KEYS = {"path", "paths", "file", "files", "file_path", "filepath", "filePath", "target_file",
             "targetFile", "target", "filename", "destination", "dest", "new_path", "old_path",
             "source", "src", "dir", "directory"}
COMMAND_KEYS = ("command", "cmd", "script", "commandLine", "command_line")
WRITE_WORDS = ("write", "replace", "append", "delete", "remove", "create", "edit", "patch", "move", "rename", "mkdir")
SHELL_WORDS = ("bash", "shell", "cmd", "command", "exec", "terminal", "run")

WRAPPERS = {"sudo", "env", "nohup", "time", "command", "builtin", "exec", "xargs", "nice", "timeout"}
NETWORK = {"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "rsync", "ftp"}
INFRA = {"kubectl", "helm", "terraform", "tofu", "aws", "gcloud", "az", "docker", "podman", "systemctl",
         "service", "crontab", "kill", "pkill", "killall", "reboot", "shutdown"}
INTERPRETERS = {"python", "python2", "python3", "node", "deno", "bun", "ruby", "perl", "php", "bash", "sh",
                "zsh", "fish", "pwsh", "powershell"}
ALWAYS_BLOCK = {"npx", "pnpx", "bunx", "chmod", "chown", "chgrp", "ln", "dd", "truncate", "shred", "install"}
PKG_MANAGERS = {"pip", "pip3", "conda", "apt", "apt-get", "brew", "gem", "npm", "pnpm", "yarn", "poetry", "uv"}
PKG_SUBCOMMANDS = {"install", "i", "add", "ci", "update", "upgrade", "remove", "uninstall", "publish", "run",
                   "exec", "dlx", "sync", "tool", "pip"}
GIT_MUTATING = {"commit", "push", "pull", "fetch", "reset", "checkout", "switch", "merge", "rebase", "stash",
                "clean", "add", "rm", "mv", "tag", "cherry-pick", "restore", "revert", "apply", "am", "init",
                "clone", "submodule", "worktree", "gc", "prune", "update-ref", "config"}
GIT_BRANCH_MUTATING = {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy", "--force"}
PATH_ONLY_COMMANDS = {"mkdir", "touch", "tee", "rm", "rmdir", "mv", "cp", "unlink"}
FIND_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
ALLOWED_SCRIPTS = ("tools/agent_review/check_citations.py", "tools/agent_review/guard.py")
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
SEPARATORS = re.compile(r"\|\||&&|;|\||\n|\$\(|`")
REDIRECT = re.compile(r"(?<![0-9&<>])(?:[0-9]?>>?|&>>?)\s*(?!&)([^\s;&|<>]+)")


class Blocked(Exception):
    pass


def inside_deliverables(path, cwd):
    if not path or path in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return True
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    base = os.path.normpath(os.path.join(cwd, DELIVERABLES))
    return full == base or full.startswith(base + os.sep)


def collect_paths(value, depth=0):
    found = []
    if depth > 3:
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PATH_KEYS:
                if isinstance(item, str):
                    found.append(item)
                elif isinstance(item, list):
                    found.extend(x for x in item if isinstance(x, str))
            elif isinstance(item, (dict, list)):
                found.extend(collect_paths(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_paths(item, depth + 1))
    return found


def command_text(tool_input):
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, dict):
        for key in COMMAND_KEYS:
            if isinstance(tool_input.get(key), str):
                return tool_input[key]
    return None


def check_segment(tokens, cwd):
    while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or os.path.basename(tokens[0]) in WRAPPERS):
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):
            tokens = tokens[1:]
    if not tokens:
        return
    name = os.path.basename(tokens[0])
    args = tokens[1:]
    if name in NETWORK:
        raise Blocked(f"`{name}` reaches the network")
    if name in INFRA:
        raise Blocked(f"`{name}` acts on live infrastructure")
    if name in ALWAYS_BLOCK:
        raise Blocked(f"`{name}` changes files or runs downloaded code")
    if name in PKG_MANAGERS and any(a in PKG_SUBCOMMANDS for a in args[:2]):
        raise Blocked(f"`{name}` installs or runs packages")
    if name in INTERPRETERS:
        script = next((a for a in args if not a.startswith("-")), "")
        if script.replace("./", "", 1) in ALLOWED_SCRIPTS:
            return
        raise Blocked(f"running `{name}` is off during the review (only the citation checker is allowed)")
    if name == "git":
        rest = list(args)
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
        sub = rest[0] if rest else ""
        if sub in GIT_MUTATING:
            raise Blocked(f"`git {sub}` changes repository state")
        if sub == "branch" and any(a in GIT_BRANCH_MUTATING for a in rest[1:]):
            raise Blocked("`git branch` with that option changes repository state")
    if name == "find" and any(a in FIND_ACTIONS for a in args):
        raise Blocked("`find` with -delete/-exec can change files")
    if name in ("sed", "perl") and any(a.startswith("-i") or a == "--in-place" for a in args):
        raise Blocked(f"`{name} -i` edits files in place; use your file-write tool for deliverables")
    if name in PATH_ONLY_COMMANDS:
        targets = [a for a in args if not a.startswith("-")]
        if not targets or not all(inside_deliverables(t, cwd) for t in targets):
            raise Blocked(f"`{name}` may only touch paths under docs/agent-review/")


def check_shell(command, cwd):
    stripped = QUOTED.sub("''", command)
    for match in REDIRECT.finditer(stripped):
        if not inside_deliverables(match.group(1), cwd):
            raise Blocked(f"redirecting output to `{match.group(1)}` writes outside docs/agent-review/")
    for segment in SEPARATORS.split(stripped):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        check_segment(tokens, cwd)


def decide(payload):
    """Return None to allow, or a reason string to block."""
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.exists(os.path.join(cwd, FLAG)):
        return None
    name = str(payload.get("tool_name", ""))
    lowered = name.lower()
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            pass
    try:
        if lowered.startswith("mcp") or lowered.startswith("@"):
            raise Blocked("MCP tools are off during the review (they can reach live systems)")
        if lowered.startswith("web") or "web_fetch" in lowered or "web_search" in lowered:
            raise Blocked("web tools are off during the review")
        command = command_text(tool_input) if any(w in lowered for w in SHELL_WORDS) else None
        if command is not None:
            check_shell(command, cwd)
            return None
        if any(w in lowered for w in WRITE_WORDS):
            for path in collect_paths(tool_input):
                if not inside_deliverables(path, cwd):
                    raise Blocked(f"writing `{path}` is outside docs/agent-review/")
    except Blocked as reason:
        return str(reason)
    return None


def message(reason):
    return (
        f"agent-review guard blocked this call: {reason}. Phases 1-5 are read-only for the project "
        "(docs/agent-review-method/00-rules.md section 4.2). Put proposed changes in "
        "docs/agent-review/08-drafts/ instead, or ask the user. The user can turn the guard off by "
        "deleting docs/agent-review/.review-mode."
    )


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as cwd:
        os.makedirs(os.path.join(cwd, DELIVERABLES))
        cases = [
            ({"tool_name": "fs_write", "tool_input": {"path": "docs/agent-review/01-system-map.md"}}, False),
            ({"tool_name": "fsWrite", "tool_input": {"path": "src/app.py"}}, True),
            ({"tool_name": "str_replace", "tool_input": {"path": "../outside.md"}}, True),
            ({"tool_name": "execute_bash", "tool_input": {"command": "grep -rn 'git commit' src | head -20"}}, False),
            ({"tool_name": "execute_bash", "tool_input": {"command": "sed -n '1,200p' src/app.py"}}, False),
            ({"tool_name": "shell", "tool_input": {"command": "curl -s http://victorialogs:9428/select/logsql/query"}}, True),
            ({"tool_name": "shell", "tool_input": {"command": "git log --oneline -5 && git diff HEAD~1 --stat"}}, False),
            ({"tool_name": "shell", "tool_input": {"command": "git commit -am wip"}}, True),
            ({"tool_name": "shell", "tool_input": {"command": "cat src/app.py > notes.txt"}}, True),
            ({"tool_name": "shell", "tool_input": {"command": "wc -l src/app.py > docs/agent-review/tmp/x.txt 2>&1"}}, False),
            ({"tool_name": "shell", "tool_input": {"command": "python3 -I tools/agent_review/check_citations.py --only docs/agent-review/01-system-map.md"}}, False),
            ({"tool_name": "shell", "tool_input": {"command": "python3 -c 'import os'"}}, True),
            ({"tool_name": "shell", "tool_input": {"command": "find . -name '*.pyc' -delete"}}, True),
            ({"tool_name": "shell", "tool_input": {"command": "mkdir -p docs/agent-review/scorecards"}}, False),
            ({"tool_name": "shell", "tool_input": {"command": "pip install requests"}}, True),
            ({"tool_name": "mcp_gitlab_list_commits", "tool_input": {}}, True),
            ({"tool_name": "web_fetch", "tool_input": {"url": "https://example.com"}}, True),
            ({"tool_name": "read", "tool_input": {"path": "src/app.py"}}, False),
        ]
        failures = []
        off = {"tool_name": "fs_write", "tool_input": {"path": "src/app.py"}, "cwd": cwd}
        if decide(off) is not None:
            failures.append({"review_mode": "off", **off})
        with open(os.path.join(cwd, FLAG), "w", encoding="utf-8") as handle:
            handle.write("on\n")
        for payload, should_block in cases:
            payload = dict(payload, cwd=cwd)
            blocked = decide(payload) is not None
            if blocked != should_block:
                failures.append(payload)
    if failures:
        print("guard self-test FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("guard self-test ok")
    return 0


def main(argv):
    if argv[:1] == ["--self-test"]:
        return self_test()
    try:
        raw = sys.stdin.read(1024 * 1024)
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    reason = decide(payload)
    if reason is None:
        return 0
    print(message(reason), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
