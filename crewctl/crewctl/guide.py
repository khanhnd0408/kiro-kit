"""`crewctl guide`: build a command step by step. Every choice shows the command so far and what that choice does.

Two ways in: describe what you want in a few words and get suggestions, or browse the list. Then pick the options one
by one. After each pick you see the full command line, the effect of the choice you just made, and the effects of all
choices so far, with a label that says whether the command only reads, edits your files, or changes Crew.
Nothing runs until you say so. The menu is built from the real command parser, so it cannot drift from the commands.
"""

import argparse
import os
import re
import shlex
import sys
from dataclasses import dataclass, field

from .helptext import paint

# How much a command can change. The label is shown next to the command everywhere in the guide.
IMPACT = {
    "read": ("READ-ONLY", "Only reads and prints. Changes nothing."),
    "config": ("EDITS YOUR CONFIG FILES", "Edits files in your config folder (backups are kept)."),
    "agents": ("WRITES AGENT FILES", "Writes or deletes files in ~/.kiro/agents and the state in <config>/.crewctl (backups are kept)."),
    "skills": ("WRITES SKILL FILES", "Writes or deletes skill folders (default ~/.kiro/crew/skills) and the state in <config>/.crewctl."),
    "crew": ("CHANGES CREW", "Changes Crew itself: it runs `kirocrew` commands that create, update or delete records or workspaces."),
    "program": ("STARTS A PROGRAM", "Starts or calls a program from your mcp.json to read its tool list, then stops it."),
    "install": ("COPIES FILES INTO A PROJECT", "Copies the review kit into a project folder and into ~/.kiro-agent-review."),
}

# command -> (impact, what it does, words people use for it). Keys are the command words; `skills` is split by action.
INFO = {
    ("init",): ("config", "Creates the config folder with starter files. With --team it also copies a ready-made team; with --update it refreshes an earlier copy and keeps your settings.", "start new create folder team copy update refresh devteam example"),
    ("validate",): ("read", "Checks the config files (and that the workspaces exist) and prints problems. Writes nothing.", "check verify lint config errors"),
    ("models",): ("read", "Prints the model names, their credit cost, and which ones this machine's Crew has.", "model credits cost price list"),
    ("plan",): ("read", "Shows what `apply` would change in the agent files, and what is left to register in Crew. Writes nothing.", "preview diff dry run show changes what will change"),
    ("apply",): ("agents", "Writes the agent files from your config. Backs up every file it replaces. Does not touch Crew's records.", "write generate create update deploy install agents"),
    ("destroy",): ("agents", "Removes the agent files crewctl created. With --delete-records it also removes the Crew records.", "delete remove uninstall clean undo"),
    ("status",): ("read", "One line per crewmate: is its file in sync, is its Crew record in sync.", "list show state sync overview crewmates"),
    ("register",): ("crew", "Prints the `kirocrew` commands that create or update the Crew record of each crewmate. Only with --run does it run them.", "crew record create update description triggers workspace sync"),
    ("import",): ("config", "Adopts an agent file that already exists, so the next plan can show what would change.", "adopt existing take over"),
    ("state", "list"): ("read", "Lists the crewmates crewctl has a record of in this config folder.", "state records managed"),
    ("state", "show"): ("read", "Shows crewctl's record of one crewmate.", "state record details"),
    ("state", "rm"): ("config", "Makes crewctl forget one crewmate without touching its file.", "state forget remove record"),
    ("state", "mv"): ("config", "Renames a crewmate in crewctl's record (use after renaming it in the config).", "state rename move"),
    ("skills", "new"): ("config", "Creates skills/NAME.skill.json from the placeholder file, for you to fill in.", "skill create new template placeholder"),
    ("skills", "plan"): ("read", "Shows the SKILL.md files that `skills apply` would write, with lint results.", "skill preview check show"),
    ("skills", "apply"): ("skills", "Builds SKILL.md files from skills/*.skill.json and writes them. Never overwrites a folder it did not create unless you adopt it.", "skill install write build update"),
    ("skills", "destroy"): ("skills", "Deletes the skill files crewctl wrote (only those).", "skill delete remove uninstall"),
    ("lint-skills",): ("read", "Checks SKILL.md files against the Kiro and Crew rules (name, description, size, credentials).", "skill lint check validate"),
    ("workspace", "list"): ("read", "Lists the workspaces Crew has and which crewmates use them.", "workspace folders show"),
    ("workspace", "check"): ("crew", "Finds workspaces the config uses but Crew lacks and creates them, asking for the name, folder and an optional workspace to copy.", "workspace missing create fix"),
    ("workspace", "create"): ("crew", "Creates one workspace in Crew (a folder under ~/.kiro/crew).", "workspace new add"),
    ("workspace", "assign"): ("crew", "Moves existing crewmates to a workspace: edits their config files and updates their Crew records.", "workspace move change crewmates agents assign project folder"),
    ("workspace", "restore"): ("crew", "Puts crewmates back on the workspace an earlier backup of their file names, after an overwrite moved them.", "workspace restore undo lost reset back"),
    ("mcp", "list"): ("read", "Lists the MCP servers in mcp.json and which crewmates use them.", "mcp servers tools list"),
    ("mcp", "tools"): ("program", "Starts (or calls) one server from mcp.json, as Kiro would, and prints its tool names. Asks first.", "mcp tools connect test list"),
    ("mcp", "add"): ("config", "Gives crewmates a server from mcp.json by editing the `mcp_json_servers` line of their config files.", "mcp add attach connect server tool agents"),
    ("mcp", "remove"): ("config", "Takes a server away from crewmates by editing their config files.", "mcp remove detach disconnect server"),
    ("crew-servers",): ("read", "Explains why Crew's own tool servers (kirocrew-core) are missing and prints the setting that supplies them.", "crew server core missing error spawn"),
    ("review", "install"): ("install", "Installs the agent-review kit (method documents and helper scripts) into a project.", "review compare prompts patterns install"),
    ("review", "check"): ("read", "Re-checks every file:line citation in a project's review output.", "review citations check"),
    ("review", "where"): ("read", "Shows where the review kit is.", "review kit location"),
}

# What choosing an option adds. Falls back to the option's own help text. Keyed by option dest.
EFFECTS = {
    "yes": "No confirmation question is asked: the command goes ahead at once. Run it once without this (or `plan` first) to see what it will do.",
    "force": "Overwrites or deletes files even if they were changed outside crewctl. The old versions are saved first.",
    "target": "Only the crewmates (or skills) you name are affected. Repeat the option to name several.",
    "break_lock": "Removes a stale lock left by a crewctl that was cut off. Use it only if no other crewctl is running.",
    "run": "Actually runs the `kirocrew` commands instead of only printing them. THIS CHANGES CREW.",
    "delete_records": "Also deletes each removed crewmate's Crew record and team entry. Its memory store is kept.",
    "adopt": "Takes over an existing skill folder crewctl has no record of (old files saved first). Never for Crew's built-in skills.",
    "all": "Applies to every crewmate in the config instead of only the ones you name.",
    "tools": "Limits the server to these tools. Without it the crewmate gets all of the server's tools.",
    "copy_from": "Fills the new workspace with the files of an existing workspace (credentials are skipped).",
    "dir": "The folder name under ~/.kiro/crew for the workspace. Default workspace-NAME.",
    "team": "Also copies a ready-made team: devteam (ten crewmates, prompts, skills) or example (three small ones).",
    "update": "Refreshes an earlier copy of the team from the repository. Keeps crewctl.json, _defaults.json, your own files and the workspace lines; saves what it overwrites.",
    "surface": "Which Kiro surface the review kit is set up for: crew (default) or ide.",
    "reference_source": "The clone of the public system_prompts_leaks repository the review quotes.",
    "timeout": "Seconds to wait for the server to answer.",
    "detailed_exitcode": "Exit code 2 when there are changes (for scripts and CI).",
    "show": "Prints the SKILL.md that would be written.",
}

# Where the values for an argument can come from.
SOURCES = {"target": "crewmates", "crewmates": "crewmates", "workspace_name": "workspaces", "copy_from": "workspaces",
           "server": "mcp", "team": "teams", "surface": None}
SOURCES_BY_COMMAND = {("import", "name"): "crewmates", ("state", "show", "name"): "crewmates", ("state", "rm", "name"): "crewmates",
                      ("state", "mv", "name"): "crewmates", ("mcp", "tools", "name"): "mcp", ("workspace", "create", "name"): None,
                      ("skills", "plan", "name"): "skills", ("skills", "apply", "name"): "skills", ("skills", "destroy", "name"): "skills"}


@dataclass
class Arg:
    dest: str
    flags: list
    positional: bool
    kind: str                      # flag | value | multi
    help: str = ""
    choices: list = field(default_factory=list)
    metavar: str = ""
    required: bool = False


@dataclass
class Command:
    path: tuple                    # what is shown: ("workspace", "assign")
    parts: tuple                   # the words on the command line: ("workspace", "assign"); skills: ("skills",)
    args: list
    preset: dict = field(default_factory=dict)   # positionals fixed by the command itself (skills action)
    parser: object = None


def _args_of(parser, skip=("extra",)):
    found = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction)) or action.dest in skip:
            continue
        if action.nargs == 0:
            kind = "flag"
        elif isinstance(action, argparse._AppendAction) or action.nargs in ("*", "+"):
            kind = "multi"
        else:
            kind = "value"
        positional = not action.option_strings
        found.append(Arg(dest=action.dest, flags=list(action.option_strings), positional=positional, kind=kind,
                         help=(action.help or "").replace("%(default)s", str(action.default)),
                         choices=list(action.choices or []), metavar=str(action.metavar or action.dest).upper(),
                         required=positional and action.nargs not in ("*", "?")))
    return found


NOT_BUILDABLE = {"overview", "guide", "completion", "values"}      # helpers of crewctl itself, not tasks to build


def _skip_overview(choices):
    return {k: v for k, v in choices.items() if k not in NOT_BUILDABLE}


def commands(parser):
    """Every runnable command, found by walking the real parser. `skills` is split by its action word."""
    out = []

    def walk(p, parts):
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if subs:
            for name, sub in _skip_overview(subs[0].choices).items():
                walk(sub, parts + (name,))
            return
        args = _args_of(p)
        if parts == ("skills",):
            action = next(a for a in args if a.dest == "skills_action")
            rest = [a for a in args if a.dest != "skills_action"]
            for word in action.choices:
                out.append(Command(("skills", word), parts, rest, {"skills_action": word}, p))
            return
        out.append(Command(parts, parts, args, {}, p))
    walk(parser, ())
    return out


def global_args(parser):
    return [a for a in _args_of(parser) if not a.positional and a.dest not in ("no_color",)]


class World:
    """Names the guide can suggest as values. Everything is read lazily and a failure just means no suggestions."""

    def __init__(self, config_dir, overrides):
        self.config_dir, self.overrides, self._cache = config_dir, overrides, {}

    def values(self, kind):
        if kind not in self._cache:
            try:
                self._cache[kind] = self._load(kind)
            except Exception:                      # suggestions are a convenience, never a reason to fail
                self._cache[kind] = []
        return self._cache[kind]

    def _load(self, kind):
        if kind == "teams":
            from .cli import TEAMS
            return sorted(TEAMS)
        if kind == "mcp":
            from . import mcpjson
            return sorted(mcpjson.load(os.environ.get("CREWCTL_MCP_JSON") or mcpjson.default_path())[0])
        if not os.path.isdir(self.config_dir):
            return []
        from .engine import build_context
        ctx = build_context(self.config_dir, self.overrides, check_workspaces=False)
        if kind == "crewmates":
            return [s.name for s in ctx.specs]
        if kind == "workspaces":
            return sorted(set(ctx.view.workspaces) | {ctx.view.default_workspace})
        if kind == "skills":
            folder = os.path.join(self.config_dir, "skills")
            return sorted(f[:-len(".skill.json")] for f in os.listdir(folder) if f.endswith(".skill.json") and not f.startswith("_"))
        return []


def source_for(command, arg):
    return SOURCES_BY_COMMAND.get(command.path + (arg.dest,)) or SOURCES.get(arg.dest)


def info_for(command):
    impact, effect, words = INFO[command.path]
    return impact, effect, words


def rank(query, cmds):
    """Commands that best match a few words, best first. Words match the command name, its keywords and its description."""
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9-]+", query.lower())]
    scored = []
    for command in cmds:
        impact, effect, keywords = info_for(command)
        name = " ".join(command.path).lower()
        keys = set(re.findall(r"[a-z0-9-]+", keywords.lower())) | {k.rstrip("s") for k in keywords.lower().split()}
        text = effect.lower()
        score = 0
        for word in words:
            score += 4 * (word in name.split()) + 3 * (word in keys) + 1 * (word in text)
        if score:
            scored.append((score, command))
    scored.sort(key=lambda item: (-item[0], item[1].path))
    return [c for _, c in scored]


class Guide:
    def __init__(self, parser, config_dir, overrides, color, input_fn=None, print_fn=print, run_fn=None):
        self.parser, self.color = parser, color
        self.world = World(config_dir, overrides)
        self.input = input_fn or (lambda prompt: input(prompt))        # looked up at call time, so tests can replace input
        self.say, self.run_fn = print_fn, run_fn
        self.commands = commands(parser)
        home = os.path.expanduser("~")
        shown = config_dir[len(home):].join(["~", ""]) if config_dir.startswith(home + os.sep) else config_dir
        self.config_label = shown + (" (from CREWCTL_DIR)" if os.environ.get("CREWCTL_DIR") else "")
        self.globals = global_args(parser)
        self.global_values = {}
        self.history = []

    # ---- small helpers
    def ask(self, text):
        try:
            return self.input(text).strip()
        except EOFError:
            raise SystemExit(0)

    def c(self, kind, text):
        return paint(kind, text, self.color)

    def line(self, command, values):
        return " ".join(shlex.quote(t) for t in ["crewctl"] + self.argv(command, values))

    def argv(self, command, values):
        out = []
        for arg in self.globals:
            if arg.dest in self.global_values:
                out += [arg.flags[-1], str(self.global_values[arg.dest])]
        out += list(command.parts)
        out += [str(v) for v in command.preset.values()]            # skills: the action word comes right after `skills`
        merged = {**values, **command.preset}
        for arg in command.args:
            if not arg.positional or arg.dest not in merged:
                continue
            value = merged[arg.dest]
            out += [str(v) for v in value] if isinstance(value, list) else [str(value)]
        for arg in command.args:
            if arg.positional or arg.dest not in merged:
                continue
            flag = arg.flags[-1]
            value = merged[arg.dest]
            if arg.kind == "flag":
                if value:
                    out.append(flag)
            elif isinstance(value, list):
                for item in value:
                    out += [flag, str(item)]
            else:
                out += [flag, str(value)]
        return out

    def colored_line(self, command, values):
        """The command line with the command words, options and values in the same colors as the help screens."""
        if not self.color:
            return self.line(command, values)
        flags = {f for a in command.args + self.globals for f in a.flags}
        out = [self.c("head", "crewctl")]
        words = list(command.parts)
        for token in self.argv(command, values):
            shown = shlex.quote(token)
            if words and token == words[0]:
                words.pop(0)
                out.append(self.c("cmd", shown))
            elif token in flags:
                out.append(self.c("opt", shown))
            else:
                out.append(self.c("param", shown))
        return " ".join(out)

    # ---- choosing a command
    def tag(self, command):
        impact = INFO[command.path][0]
        label = IMPACT[impact][0]
        return self.c("opt" if impact == "read" else "param", f"[{label}]")

    def show_commands(self, cmds, limit=None):
        for number, command in enumerate(cmds[:limit], 1):
            effect = INFO[command.path][1]
            self.say(f"  {number:>2}  {self.c('cmd', 'crewctl ' + ' '.join(command.path)):<{26 + len(self.c('cmd', '')) }} {self.tag(command)}")
            self.say(f"      {effect}")

    def choose_command(self):
        self.say(self.c("head", "crewctl guide") + " - build a command step by step. Nothing runs until you say so.")
        while True:
            answer = self.ask("\nWhat do you want to do? Describe it in a few words (for example: move crewmates to a workspace),\n"
                              "or press Enter to browse all commands, or q to quit: ")
            if answer.lower() in ("q", "quit", "exit"):
                return None
            matches = rank(answer, self.commands) if answer else []
            if answer and not matches:
                self.say("  I found nothing for that. Try other words, or press Enter to browse.")
                continue
            shown = matches[:6] if answer else self.commands
            if answer:
                self.say(f"\nBest matches for '{answer}':")
            else:
                self.say("\nAll commands:")
            self.show_commands(shown)
            pick = self.ask("\nNumber of the command to build (Enter to search again): ")
            if pick.isdigit() and 1 <= int(pick) <= len(shown):
                return shown[int(pick) - 1]

    # ---- values
    def suggestions(self, command, arg):
        if arg.choices:
            return [str(c) for c in arg.choices]
        kind = source_for(command, arg)
        return self.world.values(kind) if kind else []

    def ask_value(self, command, arg, current=None):
        options = self.suggestions(command, arg)
        label = arg.metavar if arg.positional else f"{arg.flags[-1]} {arg.metavar}"
        if options:
            self.say(f"  Choose {label}:  " + "  ".join(f"{i}) {o}" for i, o in enumerate(options, 1)))
        text = f"  {label}" + (f" [{current}]" if current else "") + (" (a number from the list, or type it)" if options else "") + ": "
        answer = self.ask(text)
        if not answer:
            return current
        if options and answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        return answer

    def fill_required(self, command, values):
        for arg in command.args:
            if not arg.positional or arg.dest in command.preset:
                continue
            required = arg.required or (command.path == ("skills", "new") and arg.dest == "name")
            if arg.kind == "multi":
                options = self.suggestions(command, arg)
                if options:
                    self.say(f"  {arg.metavar.title()}s you can pick:  " + "  ".join(f"{i}) {o}" for i, o in enumerate(options, 1)))
                answer = self.ask(f"  {arg.metavar.title()}s (numbers or names separated by spaces; Enter to skip): ")
                picked = []
                for word in re.split(r"[\s,]+", answer):
                    if word.isdigit() and 1 <= int(word) <= len(options):
                        picked.append(options[int(word) - 1])
                    elif word:
                        picked.append(word)
                if picked:
                    values[arg.dest] = picked
            elif required:
                value = None
                while not value:
                    value = self.ask_value(command, arg)
                    if not value:
                        self.say("  This one is required.")
                values[arg.dest] = value

    # ---- the panel
    def effect_of(self, arg, value):
        text = EFFECTS.get(arg.dest) or arg.help
        shown = ", ".join(map(str, value)) if isinstance(value, list) else value
        return f"{text}" + (f"  (now: {shown})" if arg.kind != "flag" and shown not in (None, "", True) else "")

    def panel(self, command, values, note="", full=False):
        impact, effect, _ = info_for(command)
        label, meaning = IMPACT[impact]
        merged = {**values, **command.preset}
        self.say("\n" + "-" * 78)
        self.say(f"Config folder:   {self.config_label}")
        self.say(self.c("head", "Command so far:  ") + self.colored_line(command, values))
        self.say(f"What it does:    {effect}")
        self.say(f"Impact:          {self.tag(command)} {meaning}")
        if note:
            self.say(self.c("head", "Last choice:      ") + note)
        active = [(a, merged[a.dest]) for a in command.args if not a.positional and a.dest in merged and merged[a.dest] not in (False, None, [])]
        if active:
            self.say("Added so far:")
            for arg, value in active:
                self.say(f"  {self.c('opt', arg.flags[-1])}  {self.effect_of(arg, value)}")
        options = [a for a in command.args if not a.positional]
        self.say("\nOptions you can add (type the number; again to change or remove it; ?number for the full effect):")
        for number, arg in enumerate(options, 1):
            mark = "x" if arg.dest in merged and merged[arg.dest] not in (False, None, []) else " "
            name = ", ".join(arg.flags) + ("" if arg.kind == "flag" else f" {arg.metavar}")
            text = EFFECTS.get(arg.dest) or arg.help
            if not full and len(text) > 60:
                text = text[:57].rstrip() + "..."
            self.say(f"  [{mark}] {number:>2}  {self.c('opt', name):<{24 + len(self.c('opt', ''))}} {text}")
        positional = [a for a in command.args if a.positional and a.dest not in command.preset]
        if positional:
            def shown(arg):
                value = merged.get(arg.dest, "-")
                return " ".join(map(str, value)) if isinstance(value, list) else value
            self.say("Arguments:  " + "   ".join(f"{self.c('param', a.metavar)} = {shown(a)}" for a in positional)
                     + "   (type 'a' to change them)")
        self.say("\n  r run it now    p print the command and leave    u undo the last choice    "
                 "x pick another command    g global options    q quit")
        return options

    # ---- the loop
    def edit_option(self, command, values, arg):
        before = dict(values)
        if arg.kind == "flag":
            if values.get(arg.dest):
                values.pop(arg.dest)
                return before, f"removed {arg.flags[-1]}"
            values[arg.dest] = True
            return before, f"added {arg.flags[-1]}: " + (EFFECTS.get(arg.dest) or arg.help)
        if arg.kind == "multi":
            current = values.get(arg.dest, [])
            if current:
                self.say(f"  Already set: {', '.join(map(str, current))}")
            value = self.ask_value(command, arg)
            if value is None or value == "":
                values.pop(arg.dest, None)
                return before, f"removed {arg.flags[-1]}"
            values[arg.dest] = current + [value]
            return before, f"added {arg.flags[-1]} {value}: " + (EFFECTS.get(arg.dest) or arg.help)
        value = self.ask_value(command, arg, values.get(arg.dest))
        if value in (None, ""):
            values.pop(arg.dest, None)
            return before, f"removed {arg.flags[-1]}"
        values[arg.dest] = value
        return before, f"set {arg.flags[-1]} {value}: " + (EFFECTS.get(arg.dest) or arg.help)

    def edit_positionals(self, command, values):
        for arg in command.args:
            if arg.positional and arg.dest not in command.preset:
                value = self.ask_value(command, arg, values.get(arg.dest) if not isinstance(values.get(arg.dest), list) else None)
                if value:
                    values[arg.dest] = [value] if arg.kind == "multi" else value
        return "changed the arguments"

    def edit_globals(self):
        self.say("\nGlobal options (they go before the command):")
        for number, arg in enumerate(self.globals, 1):
            current = self.global_values.get(arg.dest)
            self.say(f"  {number}  {self.c('opt', ', '.join(arg.flags))} {arg.metavar if arg.kind != 'flag' else ''}  "
                     f"{arg.help}" + (f"   [now: {current}]" if current else ""))
        pick = self.ask("Number to set (Enter for none): ")
        if pick.isdigit() and 1 <= int(pick) <= len(self.globals):
            arg = self.globals[int(pick) - 1]
            value = self.ask(f"  {arg.flags[-1]} {arg.metavar} (Enter to remove): ")
            if value:
                self.global_values[arg.dest] = value
            else:
                self.global_values.pop(arg.dest, None)

    def finish(self, command, values):
        argv = self.argv(command, values)
        line = self.line(command, values)
        label = IMPACT[INFO[command.path][0]][0]
        self.say(f"\n{self.colored_line(command, values)}   {self.tag(command)}")
        if self.run_fn is None:
            return 0
        if self.ask(f"Run it now? ({label}) Type 'yes': ").lower() != "yes":
            self.say("Not run.")
            return 0
        self.say("")
        return self.run_fn(argv)

    def build(self, command):
        values = {}
        self.fill_required(command, values)
        note, first = "", True
        while True:
            options = self.panel(command, values, note, full=first)
            first = False
            choice = self.ask("\nYour choice: ").lower()
            if choice == "q":
                return 0
            if choice == "x":
                return "again"
            if choice == "p":
                self.say("\n" + self.colored_line(command, values))
                return 0
            if choice == "r":
                return self.finish(command, values)
            if choice == "u":
                if self.history:
                    values.clear()
                    values.update(self.history.pop())
                    note = "undid the last choice"
                else:
                    note = "nothing to undo"
                continue
            if choice == "g":
                self.edit_globals()
                note = "changed the global options"
                continue
            if choice == "a":
                self.history.append(dict(values))
                note = self.edit_positionals(command, values)
                continue
            if choice.startswith("?") and choice[1:].isdigit() and 1 <= int(choice[1:]) <= len(options):
                arg = options[int(choice[1:]) - 1]
                self.say(f"\n  {self.c('opt', ', '.join(arg.flags))}: {EFFECTS.get(arg.dest) or arg.help}")
                note = f"explained {arg.flags[-1]} (nothing changed)"
                continue
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                before, note = self.edit_option(command, values, options[int(choice) - 1])
                self.history.append(before)
                continue
            note = f"'{choice}' is not one of the choices"

    def run(self):
        while True:
            command = self.choose_command()
            if command is None:
                return 0
            self.history = []
            result = self.build(command)
            if result != "again":
                return result
