"""`crewctl map`: a self-contained HTML page to build crewctl commands on a map.

Every command is a node (crewctl > job > group > command > options). Click a node to focus it; an option shows its input
right on the node, `?` explains it, and a floating bar at the bottom shows the command line with a Copy button. Chips in
the bar can be removed (x, Backspace) and every change can be undone (Ctrl/Cmd+Z). Esc returns to the view you came from.

The page is built from the real command parser and the guide's texts each time, so it cannot drift from the commands;
its JavaScript builds a line the same way as `crewctl guide` (the tests check that for every command). It has no
external links, scripts or fonts, and it runs nothing: it only builds the text you copy.
"""

import json
import os

from . import __version__
from . import guide, helptext

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map.html")
PLACEHOLDER = "/*DATA*/null"


def _arg(arg):
    return {"dest": arg.dest, "flags": arg.flags, "positional": arg.positional, "kind": arg.kind, "help": arg.help,
            "effect": guide.EFFECTS.get(arg.dest, ""), "choices": [str(c) for c in arg.choices], "metavar": arg.metavar,
            "required": arg.required}


def build_data(parser):
    """The command tree as plain data: every buildable command with its options, the global options, the jobs."""
    groups = []
    for title, entries in helptext.JOBS:
        summary = {" ".join(words.split()[1:]): description for words, _, description in entries}
        groups.append({"title": title, "summary": summary})
    commands = []
    for command in guide.commands(parser):
        impact, effect, words = guide.INFO[command.path]
        commands.append({"path": list(command.path), "parts": list(command.parts), "preset": command.preset,
                         "impact": impact, "effect": effect, "words": words, "args": [_arg(a) for a in command.args]})
    return {"version": __version__, "impacts": guide.IMPACT, "groups": groups, "commands": commands,
            "globals": [_arg(a) for a in guide.global_args(parser)]}


def render(data):
    with open(TEMPLATE, encoding="utf-8") as handle:
        page = handle.read()
    if page.count(PLACEHOLDER) != 1:
        raise RuntimeError(f"{TEMPLATE} must contain {PLACEHOLDER} exactly once")
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")   # never closes the script tag
    return page.replace(PLACEHOLDER, blob, 1)
