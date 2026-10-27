"""Colored help: the overview screen, and the colors applied to argparse's own --help output.

Colors (when the terminal supports them):
    command      bold cyan     crewctl apply
    --option     green         --target, -y
    PARAM        yellow        NAME, PROJECT, new|plan|apply
    description  plain         what the command or option does
Color is off when output is not a terminal, when NO_COLOR is set, or with --no-color. FORCE_COLOR turns it on.
"""

import argparse
import os
import re
import sys

ESC = "\033"
CODES = {"cmd": "1;36", "opt": "32", "param": "33", "head": "1", "dim": "2"}

# (title, [(command words, usage, description), ...])
JOBS = [
    ("START HERE. Not sure which command? Let crewctl suggest it", [
        ("crewctl guide", "", "describe what you want, then build the command one choice at a time; each choice shows the command and its effect"),
        ("crewctl completion", "bash|zsh|fish", "print a Tab-completion script (commands, options with descriptions, and your crewmate/workspace/server names)"),
    ]),
    ("JOB 1. Build and manage the crew (a team of Kiro Crew agents, from JSON files)", [
        ("crewctl init", "[--force] [--team devteam|example [--update]]", "start a config folder, optionally with a ready-made team; --update refreshes an existing copy"),
        ("crewctl validate", "", "check the config, change nothing"),
        ("crewctl models", "", "model names, credits, and which ones this machine's Crew has"),
        ("crewctl plan", "[--target NAME]", "show what apply would do; writes nothing"),
        ("crewctl apply", "[--target NAME] [-y] [--force]", "write the agent files; backs up what it replaces"),
        ("crewctl register", "[--target NAME] [--run]", "print (or with --run, run) the kirocrew commands that create the crew records"),
        ("crewctl status", "[--target NAME]", "one line per crewmate"),
        ("crewctl destroy", "[--target NAME] [--delete-records] [-y]", "remove the files crewctl created (and with the option, the records)"),
        ("crewctl workspace list", "", "the workspaces Crew has, and which crewmates use them"),
        ("crewctl workspace check", "[--yes]", "find workspaces the config uses but Crew lacks, and create them (asks for the details)"),
        ("crewctl workspace assign", "WORKSPACE [CREWMATE...] [--all] [-y]", "move existing crewmates to a workspace (edits their files, updates Crew)"),
        ("crewctl workspace restore", "[-y]", "put crewmates back on the workspace an earlier backup of their file names"),
        ("crewctl workspace create", "NAME [--dir FOLDER] [--copy-from NAME] [-y]", "create one workspace"),
        ("crewctl import", "NAME", "adopt an agent file that already exists"),
        ("crewctl state", "list|show|rm|mv [NAME]", "look at or edit the state file"),
        ("crewctl skills", "new|plan|apply|destroy [NAME] [--show] [-y]", "build SKILL.md files from skills/*.skill.json"),
        ("crewctl lint-skills", "PATH...", "check SKILL.md files against Kiro's and Crew's rules"),
        ("crewctl mcp list", "", "the MCP servers in mcp.json and which crewmates use them"),
        ("crewctl mcp add", "SERVER [CREWMATE...] [--all] [--tools A,B] [-y]", "give crewmates a server from mcp.json (edits their files)"),
        ("crewctl mcp remove", "SERVER [CREWMATE...] [--all] [-y]", "take a server away from crewmates"),
        ("crewctl mcp tools", "NAME [--timeout N] [-y]", "connect to one server from mcp.json and list its tool names"),
        ("crewctl crew-servers", "", "check why Crew's own tool servers are missing"),
    ]),
    ("JOB 2. Review a project's prompts and agents against the Codex / Claude Code patterns", [
        ("crewctl review install", "PROJECT [--surface crew|ide] [--reference-source PATH] [--force]", "install the review method into a project"),
        ("crewctl review check", "[PROJECT]", "re-check every file:line citation in the review output"),
        ("crewctl review where", "", "show where the review kit is"),
    ]),
    ("JOB 3. Watch credit usage: send Kiro Crew's usage to Grafana or any OTLP backend", [
        ("crewctl usage init", "[--force]", "create ~/.config/crewctl/usage.json; export stays off until it has an endpoint"),
        ("crewctl usage push", "[--dry-run] [--quiet] [--max-rows N]", "send what Crew recorded since the last run, then exit"),
        ("crewctl usage status", "", "settings, last run, last error, and what is not sent yet"),
        ("crewctl usage schedule", "[cron|systemd|launchd] [--every MINUTES]", "print the lines that run push every few minutes"),
    ]),
]

GLOBAL_OPTIONS = [
    ("-c, --config-dir", "FOLDER", "the config folder (or export CREWCTL_DIR=FOLDER once)"),
    ("--agents-dir", "FOLDER", "where Kiro agent files go (default ~/.kiro/agents)"),
    ("--crew-home", "FOLDER", "Crew's data folder, read-only (default ~/.kiro/crew)"),
    ("--no-color", "", "turn colors off (NO_COLOR does the same)"),
    ("-v, --verbose", "", "show notes as well as warnings and errors"),
]

TOKEN = re.compile(r"(?P<opt>(?<![\w-])--?[A-Za-z][\w-]*)|(?P<choice>\b[a-z][\w-]*(?:\|[\w-]+)+)|(?P<param>\{[^}\s]*\}|<[^>]+>|\b[A-Z][A-Z_]+\b(?:\.\.\.)?)")


def use_color(argv=None, stream=None):
    argv = sys.argv[1:] if argv is None else argv
    stream = stream or sys.stdout
    if "--no-color" in argv or "NO_COLOR" in os.environ:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(kind, text, color):
    return f"{ESC}[{CODES[kind]}m{text}{ESC}[0m" if color else text


def color_usage(text, color):
    """Color the options and parameters inside a usage string. Plain words are left alone."""
    if not color:
        return text

    def repl(match):
        if match.group("opt"):
            return paint("opt", match.group(0), True)
        return paint("param", match.group(0), True)

    return TOKEN.sub(repl, text)


def color_usage_words(text, color):
    """Like color_usage, and also colors bare lowercase words outside [...] (positional arguments such as `project`)."""
    if not color:
        return text
    out, depth, word = [], 0, ""

    def flush():
        nonlocal word
        if word:
            plain = re.fullmatch(r"[a-z][\w-]*", word) is not None and depth == 0
            out.append(paint("param", word, True) if plain else color_usage(word, True))
            word = ""

    for ch in text:
        if ch in " \t":
            flush()
            out.append(ch)
            continue
        if ch == "[" and not word:
            depth += 1
        word += ch
        if ch == "]":
            flush()
            depth = max(0, depth - 1)
    flush()
    return "".join(out)


def visible_len(text):
    return len(re.sub(r"\033\[[0-9;]*m", "", text))


def render_overview(color, width=58):
    lines = []
    lines.append(paint("head", "crewctl", color) + " does three jobs. Pick the job first, then the command.")
    lines.append("Colors: " + paint("cmd", "command", color) + "  " + paint("opt", "--option", color) + "  "
                 + paint("param", "PARAM", color) + "  description")
    for title, entries in JOBS:
        lines.append("")
        lines.append(paint("head", title, color))
        for words, usage, description in entries:
            left = "  " + paint("cmd", words, color) + (" " + color_usage(usage, color) if usage else "")
            gap = width - visible_len(left)
            if gap >= 2:
                lines.append(left + " " * gap + description)
            else:
                lines.append(left)
                lines.append("      " + description)
    lines.append("")
    lines.append(paint("head", "Global options (write them before the command)", color))
    for flags, param, description in GLOBAL_OPTIONS:
        left = "  " + color_usage(flags, color) + (" " + paint("param", param, color) if param else "")
        lines.append(left + " " * max(2, width - visible_len(left)) + description)
    lines.append("")
    lines.append("The ready-made team is " + paint("param", "crews/devteam", color) + ". Run "
                 + paint("cmd", "crewctl", color) + " " + paint("param", "COMMAND", color) + " "
                 + paint("opt", "--help", color) + " for the options of one command.")
    return "\n".join(lines)


def _color_left(left, indent, color):
    if left.lstrip().startswith("-"):
        return color_usage(left, color)
    if indent >= 4:
        return paint("cmd", left, color)
    return paint("param", left, color)


def colorize_help(text, color):
    """Add colors to argparse's help text. Lines that already carry colors are left alone."""
    if not color:
        return text
    out = []
    in_usage = False
    for line in text.split("\n"):
        if ESC in line:
            out.append(line)
            continue
        if line.startswith("usage:"):
            in_usage = True
            body = line[len("usage:"):]
            words = body.split()
            command_words = []
            for word in words:
                if word[:1] in "[{-" or word[:1].isupper() or word.startswith("<"):
                    break
                command_words.append(word)
            head = " ".join(command_words)
            rest = body.strip()[len(head):] if head else body.strip()
            out.append(paint("head", "usage:", True) + " " + (paint("cmd", head, True) if head else "") + color_usage_words(rest, True))
            continue
        if in_usage and line.startswith(" ") and line.strip():
            out.append(color_usage_words(line, True))
            continue
        in_usage = False
        if line and not line.startswith(" ") and line.rstrip().endswith(":"):
            out.append(paint("head", line, True))
            continue
        match = re.match(r"^(\s+)(\S.*?)(\s{2,})(\S.*)$", line)
        if match:
            indent = len(match.group(1))
            out.append(match.group(1) + _color_left(match.group(2), indent, True) + match.group(3) + match.group(4))
            continue
        match = re.match(r"^(\s+)(\S.*)$", line)
        if match and len(match.group(1)) <= 4 and (match.group(2).startswith(("-", "{")) or match.group(2).replace("_", "").isalpha()):
            out.append(match.group(1) + _color_left(match.group(2), len(match.group(1)), True))
            continue
        out.append(line)
    return "\n".join(out)


class ColorParser(argparse.ArgumentParser):
    """ArgumentParser whose --help output is colored when color is on (decided once, in `enabled`)."""

    enabled = False

    def format_help(self):
        return colorize_help(super().format_help(), type(self).enabled)
