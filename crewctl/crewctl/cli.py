"""Command-line interface: init, validate, models, plan, apply, destroy, status, register, import, state, usage, ..."""

import argparse
import dataclasses
import difflib
import json
import os
import re
import shutil
import sys

from . import __version__
from . import models as model_catalog
from .engine import (ApplyBlocked, Plan, apply_plan, build_context, build_plan, command_line, effective_workspace, import_existing,
                     run_registration, state_move, state_remove, status_rows)
from .helptext import ColorParser, render_overview, use_color
from .state import StateError, state_path
from .util import LockError, pretty_json

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class Out:
    def __init__(self, color):
        self.color = color

    def paint(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def green(self, t): return self.paint("32", t)
    def red(self, t): return self.paint("31", t)
    def yellow(self, t): return self.paint("33", t)
    def cyan(self, t): return self.paint("36", t)
    def bold(self, t): return self.paint("1", t)
    def dim(self, t): return self.paint("2", t)


def _out(args):
    return Out(use_color(["--no-color"] if args.no_color else []))


def _config_dir(args):
    return os.path.abspath(args.config_dir or os.environ.get("CREWCTL_DIR") or "crew")


def _overrides(args):
    return {"agents_dir": args.agents_dir, "crew_home": args.crew_home}


def _tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _print_problems(problems, out, verbose):
    shown = [p for p in problems if verbose or p.level != "info"]
    seen = set()
    for problem in shown:
        key = (problem.level, problem.where, problem.message)
        if key in seen:
            continue
        seen.add(key)
        paint = {"error": out.red, "warn": out.yellow, "info": out.dim}[problem.level]
        print(f"{paint(problem.level.upper() + ':')} {problem.where}: {problem.message}")
    hidden = sum(1 for p in problems if p.level == "info") if not verbose else 0
    if hidden:
        print(out.dim(f"({hidden} notes hidden; use -v to show them)"))
    return sum(1 for p in problems if p.level == "error")


def _load(args, out, need_state=True, check_workspaces=True):
    ctx = build_context(_config_dir(args), _overrides(args), check_workspaces=check_workspaces)
    errors = _print_problems(ctx.problems, out, args.verbose)
    bad = [p for p in ctx.problems if p.level == "error"]
    if errors and bad and all(p.kind == "workspace_missing" for p in bad) and sys.stdin.isatty():
        # The only problem is a workspace Crew does not have: offer to create it now and carry on.
        if input("\nCreate the missing workspace(s) now? Type 'yes' to continue: ").strip() == "yes":
            if _workspace_check(ctx, args, out, announce=False) == 0:
                ctx = build_context(_config_dir(args), _overrides(args), check_workspaces=check_workspaces)
                errors = _print_problems(ctx.problems, out, args.verbose)
    if errors:
        print(out.red(f"\n{errors} error(s) in the config. Fix them and run again."))
        return None
    if need_state and ctx.state is None:
        print(out.red(f"config folder not found: {_config_dir(args)}. Create it with `crewctl init`."))
        return None
    return ctx


def _preview(raw):
    """The agent JSON for a create, with a long prompt shortened."""
    data = json.loads(raw.decode("utf-8"))
    prompt = data.get("prompt", "")
    if isinstance(prompt, str) and len(prompt) > 160:
        data["prompt"] = prompt[:160].replace("\n", " ") + f"... ({len(prompt)} characters)"
    return pretty_json(data)


def _diff(old, new, limit=60):
    lines = list(difflib.unified_diff(old.decode("utf-8", "replace").splitlines(), new.decode("utf-8", "replace").splitlines(),
                                      "on disk", "from config", lineterm="", n=2))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... {len(lines) - limit} more diff lines"]
    return lines


def render_plan(ctx, plan, out, with_registration=True):
    print(f"Config:        {_tilde(ctx.config_dir)}")
    print(f"State:         {_tilde(state_path(ctx.config_dir))} (serial {ctx.state.serial})")
    print(f"Agents folder: {_tilde(ctx.settings.agents_dir)}")
    crew = "config found" if ctx.view.config_found else f"NOT FOUND ({ctx.view.error})"
    print(f"Crew home:     {_tilde(ctx.settings.crew_home)} ({crew})")
    print(f"Plan setting:  {ctx.settings.plan}")
    print()
    symbols = {"create": ("+", out.green), "update": ("~", out.yellow), "replace": ("-/+", out.yellow),
               "delete": ("-", out.red), "noop": (" ", out.dim), "adopt": ("=", out.cyan)}
    for change in plan.changes:
        if change.action == "noop" and not change.conflict:
            continue
        symbol, paint = symbols[change.action]
        flag = ""
        if change.conflict:
            flag = out.red("  [CONFLICT]")
        elif change.drift:
            flag = out.yellow("  [DRIFT: needs --force]")
        print(paint(f"{symbol} {change.name}  ({change.action}: {change.kiro_agent}.json)") + flag)
        for reason in change.reasons:
            print(f"    - {reason}")
        if change.conflict:
            print(out.red(f"    ! {change.conflict}"))
        if change.action == "create" and not change.conflict:
            for line in _preview(change.new_bytes).splitlines():
                print(out.green(f"    + {line}"))
        elif change.action in ("update", "replace") and change.old_bytes:
            for line in _diff(change.old_bytes, change.new_bytes):
                print(f"    {line}")
        print()
    counts = plan.counts()
    summary = ", ".join(f"{counts.get(a, 0)} to {a}" for a in ("create", "update", "replace", "delete", "adopt"))
    if not plan.has_changes():
        print(out.bold("No changes to the agent files.") + f" ({counts.get('noop', 0)} in sync)")
    else:
        print(out.bold(f"Plan: {summary}.") + f" ({counts.get('noop', 0)} unchanged)")
    if with_registration:
        render_registration(ctx, plan, out)


def render_registration(ctx, plan, out):
    todo = [r for r in plan.registrations if r.kind != "in-sync" or r.notes]
    if not plan.registrations:
        return
    print()
    print(out.bold("Crew registration") + out.dim(" (crewctl does not change Crew's own config; run these yourself, or use `crewctl register --run`)"))
    if not todo:
        print("  all crewmates are registered and match the config.")
        return
    binary = ctx.settings.kirocrew_bin
    for action in todo:
        label = {"register": "not registered", "update": "differs from the config", "orphan": "record left behind",
                 "unknown": "unknown", "in-sync": "registered"}[action.kind]
        print(f"  {out.bold(action.name)}: {label}")
        for argv in action.commands:
            print(f"      {command_line(binary, argv)}")
        if action.dashboard:
            print(f"      then in the dashboard (Agent Capabilities -> Agents -> {action.name}), set:")
            for key, value in action.dashboard.items():
                shown = value if len(str(value)) < 100 else str(value)[:97] + "..."
                print(f"        {key} = {shown!r}")
        for note in action.notes:
            print(out.dim(f"      note: {note}"))


def _confirm(args, question):
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("Not a terminal and --yes was not given; nothing was changed.")
        return False
    return input(f"{question} Type 'yes' to continue: ").strip() == "yes"


TEAMS = {"devteam": ("crews", "devteam"), "example": ("examples", "crew")}


def _team_source(name, target, force):
    """Find a ready-made team, and refuse early if the target folder already has agent files."""
    kind, folder = TEAMS[name]
    source = os.path.join(os.path.dirname(TEMPLATES), kind, folder)
    if not os.path.isdir(source):
        raise ApplyBlocked(f"cannot find the '{name}' team at {source}. Run crewctl from a clone of the kiro-kit repository.")
    existing = [f for f in os.listdir(target) if f.endswith(".agent.json")] if os.path.isdir(target) else []
    if existing and not force:
        raise ApplyBlocked(f"{_tilde(target)} already has agent files ({', '.join(sorted(existing)[:3])}...). "
                           "Use another folder (-c FOLDER), or add --force to overwrite files with the same names.")
    return source


TEAM_IGNORE = (".crewctl", "__pycache__", "README.md", "SETUP.md")
TEAM_KEEP = ("crewctl.json", "_defaults.json")     # your settings and defaults are never overwritten by an update


def _team_files(source):
    """Relative paths of every file in a ready-made team, minus the ones that are never copied."""
    found = []
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in TEAM_IGNORE]
        for name in files:
            if name not in TEAM_IGNORE and not name.endswith(".pyc"):
                found.append(os.path.relpath(os.path.join(root, name), source))
    return sorted(found)


PRESERVED_AGENT_KEYS = ("workspace", "workspace_dir")     # chosen per machine (crewctl workspace assign), so an update keeps them


def _wanted_bytes(source_file, mine_file, rel):
    """What the file should hold after an update: the team's version, keeping the workspace lines you set."""
    with open(source_file, "rb") as handle:
        wanted = handle.read()
    if os.path.dirname(rel) or not rel.endswith(".agent.json") or not os.path.exists(mine_file):
        return wanted, False
    try:
        mine = json.loads(open(mine_file, encoding="utf-8").read())
        theirs = json.loads(wanted.decode("utf-8"))
    except (OSError, ValueError):
        return wanted, False
    kept = {k: mine[k] for k in PRESERVED_AGENT_KEYS if k in mine and theirs.get(k) != mine[k]}
    if not kept:
        return wanted, False
    theirs.update(kept)
    return (json.dumps(theirs, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), True


def _same_json(a, b, rel):
    """Two agent files that hold the same data count as equal, whatever their spacing or final newline."""
    if not rel.endswith(".json"):
        return False
    try:
        return json.loads(a.decode("utf-8")) == json.loads(b.decode("utf-8"))
    except ValueError:
        return False


def _update_team(args, out, target):
    """Bring a copy made by `init --team` up to date with the repository, keeping settings and backing up what changes."""
    import time
    from .state import backup_file
    from .util import atomic_write
    if not os.path.exists(os.path.join(target, "crewctl.json")):
        print(out.red(f"{_tilde(target)} has no crewctl.json. Create it first: crewctl -c {_tilde(target)} init --team {args.team}"))
        return 1
    try:
        source = _team_source(args.team, target, True)
    except ApplyBlocked as error:
        print(out.red(str(error)))
        return 1
    new, changed, same = [], [], []
    kept_workspace = 0
    for rel in _team_files(source):
        if rel in TEAM_KEEP:
            continue
        mine = os.path.join(target, rel)
        wanted, was_kept = _wanted_bytes(os.path.join(source, rel), mine, rel)
        kept_workspace += was_kept
        if not os.path.exists(mine):
            new.append(rel)
        else:
            with open(mine, "rb") as handle:
                current = handle.read()
            (same if current == wanted or _same_json(current, wanted, rel) else changed).append(rel)
    theirs = set(_team_files(source))
    own = sorted(rel for rel in _team_files(target) if rel not in theirs and rel not in TEAM_KEEP
                 and not rel.startswith(".crewctl") and not rel.endswith("_template.agent.json") and not rel.endswith("_template.skill.json"))
    print(f"Updating {_tilde(target)} from the '{args.team}' team in the repository:")
    print(f"  {out.green(str(len(new)))} new, {out.yellow(str(len(changed)))} changed, {len(same)} already up to date.")
    for rel in new:
        print(f"    {out.green('+ new    ')} {rel}")
    for rel in changed:
        print(f"    {out.yellow('~ changed')} {rel}")
    if kept_workspace:
        print(f"  Kept the workspace you chose in {kept_workspace} agent file(s) (workspace / workspace_dir lines).")
    print(f"  Kept as they are: {', '.join(TEAM_KEEP)} (your settings)" + (f"; and {len(own)} file(s) that are not part of the team." if own else "."))
    if not new and not changed:
        print(out.green("Nothing to update."))
        return 0
    if changed:
        print(out.dim("  Files you changed by hand will be overwritten; the old versions are saved first."))
    if not args.yes:
        if not sys.stdin.isatty():
            print(out.red("Refusing to ask a question without a terminal. Use --yes."))
            return 1
        if input(f"Update {len(new) + len(changed)} file(s)? Type 'yes' to continue: ").strip() != "yes":
            print("Cancelled.")
            return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(target, ".crewctl", "backups", stamp, "team-update")
    for rel in changed:
        os.makedirs(os.path.dirname(os.path.join(folder, rel)), exist_ok=True)
        shutil.copy2(os.path.join(target, rel), os.path.join(folder, rel))
    for rel in new + changed:
        data, _ = _wanted_bytes(os.path.join(source, rel), os.path.join(target, rel), rel)
        mode = os.stat(os.path.join(source, rel)).st_mode & 0o777
        atomic_write(os.path.join(target, rel), data, mode=mode)
    print(out.green(f"Updated {len(new) + len(changed)} file(s)." ) + (f" Old versions: {_tilde(folder)}" if changed else ""))
    print(out.dim("Next: `crewctl validate`, `crewctl skills apply`, `crewctl apply`, `crewctl register --run`; then start a new chat with each crewmate."))
    return 0


def _copy_team(source, target, keep_settings=False):
    """Copy a ready-made team (agent files, prompts, skills, scripts) into the config folder."""
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*TEAM_IGNORE))
    return sorted(f[:-len(".agent.json")] for f in os.listdir(source) if f.endswith(".agent.json"))



def cmd_init(args):
    out = _out(args)
    target = _config_dir(args)
    if args.update:
        if not args.team:
            print(out.red("--update needs --team: crewctl init --team devteam --update"))
            return 1
        return _update_team(args, out, target)
    if os.path.exists(os.path.join(target, "crewctl.json")) and not args.force:
        print(f"{target} already has a crewctl.json; use --force to overwrite the starter files.")
        return 1
    team_dir = None
    if args.team:
        try:
            team_dir = _team_source(args.team, target, args.force)
        except ApplyBlocked as error:
            print(out.red(str(error)))
            return 1
    os.makedirs(os.path.join(target, "prompts"), exist_ok=True)
    for source, name in (("crewctl.json", "crewctl.json"), ("_defaults.json", "_defaults.json"),
                         ("agent.template.json", "_template.agent.json")):
        shutil.copyfile(os.path.join(TEMPLATES, source), os.path.join(target, name))
    gitignore = os.path.join(target, ".gitignore")
    if not os.path.exists(gitignore):
        with open(gitignore, "w", encoding="utf-8") as handle:
            handle.write(".crewctl/\n")
    open(os.path.join(target, "prompts", ".gitkeep"), "a").close()
    os.makedirs(os.path.join(target, "skills"), exist_ok=True)
    shutil.copyfile(os.path.join(TEMPLATES, "skill.template.json"), os.path.join(target, "skills", "_template.skill.json"))
    print(f"Created {_tilde(target)}:")
    print("  crewctl.json            settings (plan: paid or free)")
    print("  _defaults.json          values every agent inherits")
    print("  _template.agent.json    copy this to <name>.agent.json for each crewmate")
    print("  prompts/                long prompts, referenced by \"prompt_file\"")
    print("  skills/                 one <name>.skill.json per skill; `crewctl skills new <name>` starts one")
    if team_dir:
        before = {f for f in os.listdir(target) if f.endswith(".agent.json") and not f.startswith("_")}
        names = _copy_team(team_dir, target)
        print(f"\nCopied the '{args.team}' team: {', '.join(names)}.")
        kept = sorted(f[:-len(".agent.json")] for f in before if f[:-len(".agent.json")] not in names)
        if kept:
            print(out.yellow(f"Kept {len(kept)} agent file(s) that were already here and are not part of this team: {', '.join(kept)}."))
        print(out.dim("Next: `crewctl validate`, then `crewctl skills apply`, `crewctl apply`, `crewctl register --run`."
                      + (" Step-by-step: crews/devteam/SETUP.md in the repository." if args.team == "devteam" else "")))
        return 0
    print(out.yellow("\nThere are no crewmates yet: validate will say 'no *.agent.json files found' until you add some."))
    print(out.dim("Either copy _template.agent.json to <name>.agent.json and edit it, or start from a ready-made team:\n"
                  "  crewctl init --force --team devteam      (ten crewmates: orchestrator, python, go, rust, ...)\n"
                  "  crewctl init --force --team example      (three small examples)"))
    return 0


def cmd_validate(args):
    out = _out(args)
    ctx = build_context(_config_dir(args), _overrides(args))
    errors = _print_problems(ctx.problems, out, args.verbose)
    for spec in ctx.specs:
        print(f"  {spec.name}: agent file {spec.kiro_agent}.json, model {spec.model or 'auto'}, {len(spec.tools)} tools")
    if errors:
        print(out.red(f"\n{errors} error(s)."))
        return 1
    print(out.green(f"\nOK: {len(ctx.specs)} crewmate(s) valid."))
    return 0


def cmd_models(args):
    ctx = build_context(_config_dir(args), _overrides(args)) if os.path.isdir(_config_dir(args)) else None
    from .crewhome import load_view
    from .config import default_crew_home
    home = ctx.settings.crew_home if ctx else (args.crew_home or default_crew_home())
    local = load_view(home).model_ids
    print(model_catalog.format_table(local_ids=local))
    return 0


def cmd_lint_skills(args):
    from .skilllint import lint_paths
    out = _out(args)
    files, findings = lint_paths(args.paths)
    if not files:
        print("No SKILL.md files found.")
        return 1
    for item in findings:
        colour = out.red if item.level == "error" else out.yellow
        print(f"{colour(item.level.ljust(5))} {item.path}: {item.message}")
    errors = sum(1 for f in findings if f.level == "error")
    print(f"{len(files)} skill(s) checked: {errors} error(s), {len(findings) - errors} warning(s).")
    return 1 if errors else 0


RELOAD_NOTE = ("Crew re-reads its skills folder on its own: the list is cached for about 60 seconds (from Crew's source, not "
               "tested). No restart is needed. Opening the dashboard's Skills tab shows the result.")


def _skills_context(args, out):
    from .skillgen import load_skill_specs, load_skills_state, SkillsBlocked
    config = _config_dir(args)
    if not os.path.isdir(config):
        print(out.red(f"config folder not found: {config}. Create it with `crewctl init`."))
        return None
    settings, specs, problems = load_skill_specs(config)
    errors = _print_problems(problems, out, args.verbose)
    if errors:
        print(out.red(f"\n{errors} error(s) in the skill specs. Fix them and run again."))
        return None
    try:
        return config, settings, specs, load_skills_state(config)
    except SkillsBlocked as error:
        print(out.red(str(error)))
        return None


def _render_skill_changes(changes, out):
    marks = {"create": out.green("+ create"), "update": out.yellow("~ update"), "delete": out.red("- delete"), "noop": out.dim("  no change"),
             "adopt": out.cyan("= adopt")}
    for change in changes:
        print(f"{marks[change.action]}  {change.name}  ->  {_tilde(change.dest)}")
        if change.action in ("create", "update"):
            for rel in change.files:
                print(out.dim(f"      {rel}"))
        for rel in change.remove:
            print(out.dim(f"      remove {rel}"))
        for note in change.notes:
            print(out.dim(f"      note: {note}"))
        for reason in change.hard:
            print(out.red(f"      blocked: {reason}"))
        for reason in change.adoptable:
            print(out.yellow(f"      needs --adopt: {reason}"))
        for reason in change.blockers:
            print(out.yellow(f"      drift: {reason}"))


def cmd_skills(args):
    from .skillgen import (build_skill_plan, apply_skill_plan, lint_rendered, rendered_files, new_spec_text, SkillsBlocked)
    out = _out(args)
    if args.skills_action == "new":
        if not args.name:
            print(out.red("Usage: crewctl skills new <name>"))
            return 1
        config = _config_dir(args)
        folder = os.path.join(config, "skills")
        target = os.path.join(folder, f"{args.name}.skill.json")
        from .skilllint import NAME_RE, MAX_NAME
        if not NAME_RE.match(args.name) or len(args.name) > MAX_NAME:
            print(out.red("The name must be lowercase letters, digits and single hyphens, up to 64 characters."))
            return 1
        if os.path.exists(target):
            print(out.red(f"{target} already exists."))
            return 1
        os.makedirs(folder, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(new_spec_text(os.path.join(TEMPLATES, "skill.template.json"), args.name))
        print(f"Created {_tilde(target)}. Fill in description and steps, then run `crewctl skills plan`.")
        return 0
    loaded = _skills_context(args, out)
    if loaded is None:
        return 1
    config, settings, specs, state = loaded
    names = {s.name for s in specs} | set(state)
    for wanted in args.target:
        if wanted not in names:
            print(out.red(f"unknown skill '{wanted}'. Known: {', '.join(sorted(names)) or 'none'}"))
            return 1
    if args.skills_action == "destroy":
        changes = build_skill_plan(settings, [], state, args.target)
    else:
        changes = build_skill_plan(settings, specs, state, args.target)
    print(f"Skills for crew:    {_tilde(settings.skills_dir)}")
    if settings.project_skills_dir:
        print(f"Skills for project: {_tilde(settings.project_skills_dir)}")
    if not changes:
        print("Nothing to do: no skill specs and nothing managed.")
        return 0
    _render_skill_changes(changes, out)
    by_name = {s.name: s for s in specs}
    if args.skills_action == "plan":
        for change in changes:
            if change.action in ("create", "update") and not change.hard:
                for item in lint_rendered(by_name[change.name], change.files):
                    colour = out.red if item.level == "error" else out.yellow
                    print(f"  {colour(item.level)} {change.name}: {item.message}")
        if args.show:
            for change in changes:
                if change.action in ("create", "update"):
                    print(out.bold(f"\n--- {change.name}/SKILL.md ---"))
                    print(change.files["SKILL.md"].decode("utf-8"))
        return 0
    todo = [c for c in changes if c.action != "noop"]
    if not todo:
        print("\nNo changes.")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print(out.red("\nRefusing to ask a question without a terminal. Use --yes."))
            return 1
        if input(f"\n{args.skills_action} {len(todo)} skill(s)? Type yes to continue: ").strip().lower() != "yes":
            print("Cancelled.")
            return 1
    try:
        done = apply_skill_plan(config, settings, specs if args.skills_action == "apply" else [], changes,
                                force=args.force, break_lock=args.break_lock, adopt=getattr(args, "adopt", False))
    except SkillsBlocked as error:
        print(out.red(str(error)))
        return 1
    print(f"\n{args.skills_action.capitalize()} complete: {done['create']} created, {done['update']} updated, {done['delete']} deleted"
          + (f", {done['adopt']} adopted." if done["adopt"] else "."))
    if done["create"] or done["update"]:
        print(out.dim(RELOAD_NOTE))
    return 0


def cmd_crew_servers(args):
    """Explain where crewctl looks for Crew's own tool servers and what to do if they are missing."""
    import shutil
    from .config import CrewServerLookup, CREW_SERVERS, load_settings
    out = _out(args)
    problems = []
    config = _config_dir(args)
    settings = load_settings(config, problems, _overrides(args))   # works without a config folder: environment and defaults
    for item in problems:
        print(out.red(f"{item.level}: {item.where}: {item.message}"))
    lookup = CrewServerLookup(settings.agents_dir, settings.crew_server_commands)
    print(f"Agents folder:   {_tilde(settings.agents_dir)}  ({'exists' if os.path.isdir(settings.agents_dir) else 'MISSING'})")
    print(f"Crew file:       {_tilde(lookup.path)}  ({'found' if os.path.isfile(lookup.path) else 'NOT FOUND'})")
    binary = shutil.which(settings.kirocrew_bin)
    print(f"kirocrew on PATH: {binary or 'not found'}")
    missing = []
    for short, full in CREW_SERVERS.items():
        entry = lookup.entry(full)
        if entry is None:
            print(f"  {full:<15} {out.red('missing')}")
            missing.append(short)
        else:
            source = "crewctl.json" if full in settings.crew_server_commands else "Crew's file"
            exists = os.path.exists(entry["command"]) or shutil.which(entry["command"])
            note = "" if exists else out.red("  (this command does not exist on this machine)")
            print(f"  {full:<15} {out.green('found')} from {source}: {entry['command']} {' '.join(entry.get('args', []))}{note}")
    if not missing:
        print(out.green("\nNothing to fix."))
        return 0
    print("\nWhat to try, in order:")
    print("  1. Start Crew (the desktop app, or the gateway) and wait about a minute, then run this command again.")
    print("     Crew writes the file when its gateway starts. `kirocrew status` should say the gateway is running.")
    print("  2. Run `kirocrew doctor`; it checks the install. A known cause (from Crew's source): when Crew cannot find its own")
    print("     `kirocrew` launcher, for example because the gateway runs as a background service whose PATH lacks it, it leaves")
    print("     the entry out of the file. Putting `kirocrew` on that service's PATH, then restarting the gateway, fixes it.")
    print("  3. If the file still does not appear, tell crewctl the command yourself. In crewctl.json add:")
    command = binary or "/full/path/to/kirocrew"
    snippet = {"crew_server_commands": {s: {"command": command, "args": ["mcp-" + s]} for s in missing}}
    print(json.dumps(snippet, indent=2))
    print("     `mcp-core` and `mcp-cron` are what Crew 0.8.0 writes for its own servers. I could not confirm them for other"
          " versions, so compare with `kirocrew --version` and Crew's documentation.")
    print("     If you have no standalone `kirocrew` program, use the Python that runs Crew instead:")
    print('       {"command": "/path/to/that/python", "args": ["-P", "-m", "kiro_crew", "mcp-core"]}')
    if not binary:
        print("     `kirocrew` was not found on PATH, so replace /full/path/to/kirocrew with the real path "
              "(`which kirocrew` on Linux and macOS, `where kirocrew` on Windows).")
    return 1


def _review_kit():
    """Folder of the agent-review kit (kiro-kit/aiops), or None. CREWCTL_REVIEW_KIT overrides the guess."""
    candidates = []
    if os.environ.get("CREWCTL_REVIEW_KIT"):
        candidates.append(os.path.abspath(os.path.expanduser(os.environ["CREWCTL_REVIEW_KIT"])))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(here)), "aiops"))
    for folder in candidates:
        if os.path.isfile(os.path.join(folder, "install.sh")):
            return folder
    return None


def cmd_review(args):
    """The agent-review kit: install it into a project, or re-check its citations."""
    import subprocess
    out = _out(args)
    kit = _review_kit()
    if kit is None:
        print(out.red("Cannot find the agent-review kit (the `aiops` folder next to `crewctl` in the kiro-kit repository)."))
        print("Run crewctl from a clone of the repository (`pip install -e .` keeps it in place), "
              "or set CREWCTL_REVIEW_KIT to the aiops folder.")
        return 1
    if args.review_action == "where":
        print(f"Review kit: {_tilde(kit)}")
        return 0
    project = os.path.abspath(os.path.expanduser(args.project))
    if not os.path.isdir(project):
        print(out.red(f"{project} is not a folder."))
        return 1
    if args.review_action == "install":
        if os.name == "nt":
            print(out.red("The installer is a bash script. Run it from WSL or Git Bash: crewctl review install ..."))
            return 1
        argv = ["bash", os.path.join(kit, "install.sh"), project, "--surface", args.surface]
        if args.reference_source:
            argv += ["--reference-source", os.path.abspath(os.path.expanduser(args.reference_source))]
        if args.force:
            argv.append("--force")
        return subprocess.call(argv)
    checker = os.path.join(project, "tools", "agent_review", "check_citations.py")
    if not os.path.isfile(checker):
        print(out.red(f"{project} has no tools/agent_review/check_citations.py. Install the kit first: crewctl review install {args.project}"))
        return 1
    return subprocess.call([sys.executable, "-I", checker, *args.extra], cwd=project)


def cmd_overview(args):
    print(render_overview(_out(args).color))
    return 0


def _usage_settings(args, out, quiet=False):
    """Load usage.json and print its problems. Returns the settings, or None when there are errors."""
    from . import usage
    path = os.path.abspath(os.path.expanduser(args.settings)) if args.settings else usage.default_settings_path()
    settings, errors, warnings = usage.load_usage_settings(path, args.crew_home or "")
    for message in errors:
        print(out.red(f"error: {_tilde(path)}: {message}"))
    if not quiet:
        for message in warnings:
            print(out.yellow(f"warning: {message}"))
    return None if errors else settings


def _usage_summary(result, settings, out, dry_run):
    noun = "Would send" if dry_run else "Sent"
    if settings.logs_url:
        print(f"{noun} {result.turns} turn record(s) ({result.credits:.3f} credits) and {result.subagent_runs} subagent run(s) "
              f"to {settings.logs_url}")
    elif result.turns:
        print(f"Counted {result.turns} turn(s) ({result.credits:.3f} credits) into the metrics; log records are off (signals).")
    if settings.metrics_url:
        print(f"{noun} {result.series} metric series to {settings.metrics_url}")
    if result.skipped_old_files:
        print(out.dim(f"Skipped {result.skipped_old_files} usage file(s) older than backfill_days ({settings.backfill_days}); "
                      "their rows are never sent."))
    if result.unreadable:
        print(out.yellow(f"{result.unreadable} row(s) could not be read and were skipped. Example: {result.unreadable_example}"))
    if result.stopped:
        print(out.yellow(f"Stopped early: {result.stopped}."))
    if result.more_left:
        print(out.dim("More rows are waiting; the next run continues where this one stopped."))


def _usage_preview(result, out):
    print(out.bold("\nFirst log records (exactly what is sent, decoded):"))
    if not result.preview_logs:
        print("  (none)")
    for record in result.preview_logs:
        stamp = time_text(record["time_ns"])
        print(f"  {stamp}  {record['body']}")
        print(out.dim(f"      {json.dumps(record['attributes'], ensure_ascii=False, sort_keys=True)}"))
    if result.preview_metrics:
        print(out.bold("\nMetric series (the value each would have after this run):"))
        shown = 0
        for metric in result.preview_metrics:
            latest = {}
            for point in metric["points"]:
                latest[json.dumps(point["attributes"], sort_keys=True)] = point
            for labels, point in sorted(latest.items()):
                if shown >= 20:
                    break
                value = point["value"]
                text = f"{value:.3f}" if isinstance(value, float) else str(value)
                print(f"  {metric['name']}{labels} {text}")
                shown += 1
        total = sum(len({json.dumps(p['attributes'], sort_keys=True) for p in m["points"]}) for m in result.preview_metrics)
        if total > shown:
            print(out.dim(f"  ... and {total - shown} more"))


def time_text(time_ns):
    import datetime
    return datetime.datetime.fromtimestamp(time_ns / 1e9).astimezone().isoformat(timespec="seconds")


def cmd_usage(args):
    """Send Kiro Crew's credit usage to an OTLP backend (Grafana, VictoriaMetrics, ...), and the helpers around it."""
    from . import usage
    out = _out(args)
    path = os.path.abspath(os.path.expanduser(args.settings)) if args.settings else usage.default_settings_path()
    if args.usage_action == "init":
        if os.path.exists(path) and not args.force:
            print(out.red(f"{_tilde(path)} already exists. Edit it, or use --force to start again from the template."))
            return 1
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(os.path.join(TEMPLATES, "usage.json"), path)
        print(f"Created {_tilde(path)}. Export stays off until you set an endpoint in it.")
        print(out.dim("Next: set \"endpoint\" (or logs_endpoint and metrics_endpoint), run `crewctl usage push --dry-run` to see "
                      "exactly what would be sent, then `crewctl usage schedule` for the line that runs it every few minutes."))
        return 0
    if args.usage_action == "schedule":
        settings = _usage_settings(args, out)
        kind = args.scheduler or usage.default_scheduler()
        crew_home = (settings.crew_home if settings is not None else "") if (args.crew_home or os.environ.get("KIROCREW_HOME")) else ""
        print(usage.schedule_text(kind, args.every, path, usage.launcher_path(), usage.python_path(),
                                  usage.default_state_dir(), crew_home), end="")
        if settings is not None and not settings.exists:
            print(out.yellow(f"\nNote: {_tilde(path)} does not exist yet; create it with `crewctl usage init` and set an endpoint."))
        elif settings is not None and not settings.enabled:
            print(out.yellow(f"\nNote: {_tilde(path)} has no endpoint yet, so each run would only say that export is off."))
        return 0
    if args.usage_action == "status":
        return _usage_status(args, out, path)
    quiet = args.quiet and not args.dry_run
    settings = _usage_settings(args, out, quiet)
    if settings is None:
        return 1
    if not settings.exists or not settings.enabled:
        if not quiet:
            if not settings.exists:
                print(f"Usage export is off: there is no settings file ({_tilde(path)}). `crewctl usage init` creates it.")
            else:
                print(f"Usage export is off: no endpoint is set in {_tilde(path)}. Set \"endpoint\" (or logs_endpoint and "
                      "metrics_endpoint) in it, then run `crewctl usage push --dry-run`.")
        return 0
    if not os.path.isdir(settings.crew_home):
        print(out.red(f"Crew's data folder {_tilde(settings.crew_home)} does not exist. Set \"crew_home\" in {_tilde(path)} or use --crew-home."))
        return 1
    if not args.dry_run and hasattr(os, "nice"):
        try:
            os.nice(10)          # a background job: give way to Kiro and everything else
        except OSError:
            pass
    try:
        result = usage.run_push(settings, usage.default_state_dir(), dry_run=args.dry_run, max_rows=args.max_rows,
                                break_lock=args.break_lock)
    except LockError as error:
        if not quiet:
            print(f"Another `crewctl usage push` is still running ({error}). Nothing was done.")
        return 0
    except usage.UsageError as error:
        print(out.red(f"error: {error}"))
        return 1
    if args.dry_run:
        print(out.bold("Dry run: nothing was sent and the read positions did not move."))
    if not quiet or result.errors:
        _usage_summary(result, settings, out, args.dry_run)
    if args.dry_run:
        _usage_preview(result, out)
    for error in result.errors:
        print(out.red(f"error: {error}"))
    if result.errors:
        print(out.dim("Nothing that failed is lost: the next run sends it again from Crew's files."))
        return 1
    return 0


def _usage_status(args, out, path):
    from . import usage
    settings = _usage_settings(args, out)
    if settings is None:
        return 1
    print(f"Settings:   {_tilde(path)}" + ("" if settings.exists else out.yellow("  (not created yet: crewctl usage init)")))
    print(f"Logs to:    {settings.logs_url or out.dim('off')}")
    print(f"Metrics to: {settings.metrics_url or out.dim('off')}")
    if settings.headers_file:
        state = "missing" if not os.path.exists(settings.headers_file) else (
            "readable by others: chmod 600" if os.name != "nt" and os.stat(settings.headers_file).st_mode & 0o077 else "ok")
        print(f"Headers:    {_tilde(settings.headers_file)} ({state if state == 'ok' else out.red(state)})")
    privacy = []
    if settings.drop_fields:
        privacy.append("not sent: " + ", ".join(settings.drop_fields))
    if settings.hash_fields:
        privacy.append("hashed: " + ", ".join(settings.hash_fields))
    print(f"Privacy:    {'; '.join(privacy) or 'all allowed fields sent as they are'}")
    print(f"Crew home:  {_tilde(settings.crew_home)}" + ("" if os.path.isdir(settings.crew_home) else out.red("  (not found)")))
    state_dir = usage.default_state_dir()
    print(f"State:      {_tilde(state_dir)}")
    try:
        state = usage.load_state(state_dir)
    except usage.UsageError as error:
        print(out.red(f"error: {error}"))
        return 1
    if not state["last_run"]:
        print(out.dim("No run yet."))
        return 0
    credits = sum(e["credits"] for e in state["totals"].values())
    turns = sum(e["turns"] for e in state["totals"].values())
    print(f"Last run:   {state['last_run']}")
    print(f"Last ok:    {state['last_success'] or out.red('never')}")
    if state["last_error"]:
        print(out.red(f"Last error: {state['last_error']}"))
    print(f"Counted:    {turns} turn(s), {credits:.3f} credits, in {len(state['totals'])} series since the state was created")
    behind = usage.pending_bytes(settings, state)
    print(f"Unread:     {behind} byte(s) of usage rows not sent yet" if behind else "Unread:     nothing; up to date")
    if state.get("unreadable_rows"):
        print(out.yellow(f"Skipped:    {state['unreadable_rows']} row(s) that could not be read"))
    return 0


class Cancelled(Exception):
    pass


def _ask(question, default="", validate=None):
    """Ask on the terminal until the answer is valid. Enter keeps the default. Ctrl-D or 'q' cancels."""
    while True:
        try:
            answer = input(f"{question}" + (f" [{default}]" if default else "") + ": ").strip()
        except EOFError:
            raise Cancelled()
        if answer.lower() == "q":
            raise Cancelled()
        answer = answer or default
        problem = validate(answer) if validate else ""
        if not problem:
            return answer
        print(f"  {problem}")


def _workspace_create(ctx, args, out, name, directory, copy_from, assume_yes):
    from . import workspaces as ws
    argv = ws.create_command(name, directory, copy_from)
    binary = ctx.settings.kirocrew_bin
    print(f"\nThis will run: {out.bold(command_line(binary, argv))}")
    print(f"  Workspace {out.bold(name)} will use the folder {out.bold(_tilde(os.path.join(ctx.settings.crew_home, directory)))}"
          + (f", filled with the files of workspace {out.bold(copy_from)}." if copy_from else "."))
    if not assume_yes:
        if not sys.stdin.isatty():
            print(out.red("Refusing to ask a question without a terminal. Use --yes."))
            return False
        if input("Create it? Type 'yes' to continue: ").strip() != "yes":
            print("Skipped.")
            return False
    from .engine import run_commands
    try:
        run_commands(binary, [argv], print)
    except ApplyBlocked as error:
        print(out.red(str(error)))
        return False
    return True


def cmd_workspace(args):
    from . import workspaces as ws
    out = _out(args)
    ctx = _load(args, out, need_state=False, check_workspaces=False)
    if ctx is None:
        return 1
    view = ctx.view
    if not view.usable:
        print(out.red(f"Cannot read Crew's config: {view.error}. Start Crew once on this machine first."))
        return 1
    users = {}
    for spec in ctx.specs:
        users.setdefault(effective_workspace(spec, view), []).append(spec.name)
    if args.workspace_action == "list":
        print(f"{'WORKSPACE':<22}{'FOLDER (under ' + _tilde(ctx.settings.crew_home) + ')':<42}USED BY")
        names = sorted(set(view.workspaces) | set(users) | {view.default_workspace})
        for name in names:
            record = view.workspaces.get(name)
            folder = record.get("dir", "") if record else ("(built-in)" if name == view.default_workspace else out.red("MISSING"))
            star = " *" if name == view.default_workspace else ""
            print(f"{name + star:<22}{folder:<42}{', '.join(users.get(name, [])) or '-'}")
        return 0
    if args.workspace_action == "create":
        problem = ws.check_name(args.name, view) or ws.check_dir(args.dir or ws.default_dir(args.name), view) \
            or ws.check_copy_from(args.copy_from or "", view)
        if problem:
            print(out.red(problem))
            return 1
        return 0 if _workspace_create(ctx, args, out, args.name, args.dir or ws.default_dir(args.name), args.copy_from or "", args.yes) else 1
    if args.workspace_action == "assign":
        return _workspace_assign(ctx, args, out)
    if args.workspace_action == "restore":
        return _workspace_restore(ctx, args, out)
    return _workspace_check(ctx, args, out)


def _create_workspace_flow(ctx, args, out, wanted, hint, interactive, assume_yes, copy_default):
    """Ask for the details (or use the defaults) and create one workspace. Returns its name, "" if it failed, None if cancelled."""
    from . import workspaces as ws
    view = ctx.view
    try:
        if interactive:
            print(out.dim("  Answer each question; Enter keeps the value in brackets; q cancels."))
            name = _ask("  Workspace name", wanted, lambda v: ws.check_name(v, view))
            directory = _ask(f"  Folder name under {_tilde(ctx.settings.crew_home)}", ws.default_dir(name, hint if name == wanted else ""),
                             lambda v: ws.check_dir(v, view))
            print(f"  Existing workspaces you can copy from: {', '.join(sorted(view.workspaces)) or 'none'}")
            copy_from = _ask("  Copy the files of an existing workspace (optional; Enter for none)", "",
                             lambda v: ws.check_copy_from(v, view))
            if name != wanted:
                print(out.yellow(f"  Note: you asked for '{wanted}' but are creating '{name}'."))
        else:
            name, directory, copy_from = wanted, ws.default_dir(wanted, hint), copy_default
            problem = ws.check_name(name, view) or ws.check_dir(directory, view) or ws.check_copy_from(copy_from, view)
            if problem:
                print(out.red(f"  {problem}"))
                return ""
    except Cancelled:
        print("\nCancelled.")
        return None
    if _workspace_create(ctx, args, out, name, directory, copy_from, assume_yes or not interactive):
        view.workspaces[name] = {"dir": directory}
        return name
    return ""


def _workspace_check(ctx, args, out, announce=True):
    """Find the workspaces the config uses but Crew lacks, and create them (asking, or with defaults under --yes)."""
    from . import workspaces as ws
    view = ctx.view
    users = {}
    for spec in ctx.specs:
        users.setdefault(effective_workspace(spec, view), []).append(spec.name)
    assume_yes = getattr(args, "yes", False)
    copy_default = getattr(args, "copy_from", None) or ""
    missing = ws.missing_workspaces(view, ctx.specs)
    if not missing:
        used = sorted(users)
        print(out.green(f"All good: every workspace the crewmates use exists in Crew ({', '.join(used) or 'none'})."))
        return 0
    interactive = sys.stdin.isatty() and not assume_yes
    print(f"{len(missing)} workspace(s) used by the config do not exist in Crew: {', '.join(sorted(missing))}.")
    failed = 0
    for wanted, crewmates in sorted(missing.items()):
        hint = next((s.workspace_dir for s in ctx.specs if s.workspace == wanted and s.workspace_dir), "")
        print(f"\nWorkspace {out.bold(wanted)} is used by: {', '.join(crewmates)}.")
        if not interactive and not assume_yes:
            print(f"  Create it with: {command_line(ctx.settings.kirocrew_bin, ws.create_command(wanted, ws.default_dir(wanted, hint)))}")
            print("  or run `crewctl workspace check` in a terminal to be asked for the details, or add --yes to use the defaults.")
            failed += 1
            continue
        created = _create_workspace_flow(ctx, args, out, wanted, hint, interactive, assume_yes, copy_default)
        if created is None:
            return 1
        if not created:
            failed += 1
        elif created != wanted:
            print(out.yellow(f"  The config still says '{wanted}'. Change `workspace` in the agent files, or run "
                             f"`crewctl workspace assign CREWMATE {created}`."))
    if failed:
        print(out.red(f"\n{failed} workspace(s) still missing."))
        return 1
    if announce:
        print(out.green("\nDone. Run `crewctl validate` again."))
    return 0


def _set_workspace_in_file(path, workspace):
    """Set "workspace" in one *.agent.json file, keeping the other fields in their order."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    data = json.loads(text)
    data["workspace"] = workspace
    indent = 2 if "\n" in text.strip() else None
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def _workspace_assign(ctx, args, out):
    """Point one or more crewmates at a workspace: edit their config files, then update their Crew records."""
    import time
    from . import workspaces as ws
    from .engine import run_commands
    from .state import backup_file
    from .util import atomic_write
    view = ctx.view
    by_name = {s.name: s for s in ctx.specs}
    interactive = sys.stdin.isatty() and not args.yes
    names = list(dict.fromkeys(args.crewmates))
    if args.all:
        names = [s.name for s in ctx.specs]
    elif not names:
        if not interactive:
            print(out.red("name the crewmates (crewctl workspace assign WORKSPACE CREWMATE [CREWMATE ...]) or use --all"))
            return 1
        print(f"Crewmates in the config: {', '.join(by_name) or 'none'}")
        try:
            answer = _ask("Which crewmates? (names separated by spaces or commas, or 'all')", "",
                          lambda v: "" if v else "give at least one name")
        except Cancelled:
            print("\nCancelled.")
            return 1
        names = list(by_name) if answer.strip().lower() == "all" else [n for n in re.split(r"[\s,]+", answer) if n]
    unknown = [n for n in names if n not in by_name]
    if unknown or not names:
        print(out.red(f"unknown crewmate(s): {', '.join(unknown) or '(none given)'}. In the config: {', '.join(by_name) or 'none'}"))
        return 1
    target = args.workspace_name
    if not ws.NAME_RE.match(target):
        print(out.red(f"'{target}' is not a valid workspace name (letters, digits, hyphens and underscores)."))
        return 1
    if not ws.exists(view, target):
        print(f"Workspace {out.bold(target)} does not exist in Crew yet.")
        if not interactive and not args.yes:
            print(out.red(f"Create it first (`crewctl workspace create {target}`), or add --yes to create it with the default folder."))
            return 1
        hint = next((by_name[n].workspace_dir for n in names if by_name[n].workspace == target and by_name[n].workspace_dir), "")
        created = _create_workspace_flow(ctx, args, out, target, hint, interactive, args.yes, "")
        if not created:
            return 1
        target = created
    changing = [n for n in names if by_name[n].workspace != target]
    print(f"\nWorkspace {out.bold(target)} for {len(names)} crewmate(s):")
    for name in names:
        current = by_name[name].workspace
        print(f"  {name:<28} {current} -> {target}" if current != target else f"  {name:<28} already {target}")
    if changing:
        if interactive and input(f"Change {len(changing)} config file(s)? Type 'yes' to continue: ").strip() != "yes":
            print("Skipped.")
            return 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for name in changing:
            spec = by_name[name]
            new_text = _set_workspace_in_file(spec.source_file, target)
            backup_file(ctx.config_dir, stamp, spec.source_file, os.path.basename(spec.source_file))
            atomic_write(spec.source_file, new_text.encode("utf-8"), mode=0o644)
        print(f"Updated {len(changing)} file(s). The old versions are in {_tilde(os.path.join(ctx.config_dir, '.crewctl', 'backups', stamp))}.")
    fresh = build_context(ctx.config_dir, _overrides(args))
    actions = [r for r in build_plan(fresh, set(names)).registrations if r.name in names]
    todo = [r for r in actions if r.kind == "update" and r.commands]
    not_in_crew = [r.name for r in actions if r.kind == "register"]
    if not_in_crew:
        print(out.yellow(f"Not in Crew yet (create them with `crewctl register --run`; they will use '{target}'): {', '.join(not_in_crew)}."))
    if not todo:
        print(out.green("Crew's records already match."))
        return 0
    binary = fresh.settings.kirocrew_bin
    commands = [argv for r in todo for argv in r.commands]
    print("To update the crewmates in Crew:")
    for argv in commands:
        print(f"  {command_line(binary, argv)}")
    if interactive and input("Run these now? Type 'yes' to continue: ").strip() != "yes":
        print("Not run. `crewctl register --run` does the same later.")
        return 0
    if not interactive and not args.yes:
        print("Run `crewctl register --run`, or repeat this command with --yes.")
        return 0
    try:
        run_commands(binary, commands, print)
    except ApplyBlocked as error:
        print(out.red(str(error)))
        return 1
    print(out.green(f"{len(todo)} crewmate(s) now work in workspace '{target}'."))
    return 0

def _backed_up_workspaces(config_dir):
    """{crewmate name: workspace} from the newest backup of each crewmate's config file that says which workspace it used.

    Looks at .crewctl/backups/<time>/**/<name>.agent.json (the copies crewctl saves before it overwrites a config file,
    for example in `init --update` and `workspace assign`). A copy without a workspace line means "default" at that time.
    """
    root = os.path.join(config_dir, ".crewctl", "backups")
    found = {}
    if not os.path.isdir(root):
        return found
    for stamp in sorted(os.listdir(root), reverse=True):
        for folder, _, files in os.walk(os.path.join(root, stamp)):
            for name in sorted(files):
                if not name.endswith(".agent.json") or name.startswith("_"):
                    continue
                try:
                    data = json.load(open(os.path.join(folder, name), encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                crewmate = data.get("name") if isinstance(data, dict) else None
                if isinstance(crewmate, str) and crewmate not in found:
                    found[crewmate] = (data.get("workspace") or "default", stamp)
    return found


def _workspace_restore(ctx, args, out):
    """Put crewmates back on the workspace their config file named before an overwrite (a backup holds it)."""
    old = _backed_up_workspaces(ctx.config_dir)
    wanted = {}
    for spec in ctx.specs:
        previous = old.get(spec.name)
        if previous and previous[0] != spec.workspace:
            wanted[spec.name] = previous
    if not wanted:
        print(out.green("Nothing to restore: no backup names a workspace different from the one the config uses now."))
        if not old:
            print(out.dim(f"(no backups of config files found under {_tilde(os.path.join(ctx.config_dir, '.crewctl', 'backups'))})"))
        return 0
    print("Workspaces from the newest backups, compared with the config now:")
    for name, (workspace, stamp) in sorted(wanted.items()):
        current = next(s.workspace for s in ctx.specs if s.name == name)
        print(f"  {name:<28} {current} -> {out.bold(workspace)}   (backup {stamp})")
    groups = {}
    for name, (workspace, _) in wanted.items():
        groups.setdefault(workspace, []).append(name)
    if not args.yes:
        if not sys.stdin.isatty():
            print(out.red("Refusing to ask a question without a terminal. Use --yes."))
            return 1
        if input("Restore these? Type 'yes' to continue: ").strip() != "yes":
            print("Cancelled.")
            return 1
    failed = 0
    for workspace, names in sorted(groups.items()):
        fresh = build_context(ctx.config_dir, _overrides(args), check_workspaces=False)
        sub = argparse.Namespace(**{**vars(args), "workspace_name": workspace, "crewmates": sorted(names), "all": False, "yes": True})
        failed += _workspace_assign(fresh, sub, out) != 0
    return 1 if failed else 0


def _mcp_entries(current):
    """The mcp_json_servers value of an agent file as {name: [tools]} (a list means every tool of each server)."""
    if isinstance(current, list):
        return {n: [] for n in current if isinstance(n, str)}
    if isinstance(current, dict):
        return {n: list(o.get("tools", [])) if isinstance(o, dict) else [] for n, o in current.items()}
    return {}


def _mcp_value(entries):
    """Write {name: [tools]} back in the shortest form: a plain list when no server limits its tools."""
    if not entries:
        return None
    if all(not tools for tools in entries.values()):
        return list(entries)
    return {n: ({"tools": t} if t else {}) for n, t in entries.items()}


def _edit_agent_file(path, mutate):
    """Load a *.agent.json, let `mutate(data)` change it, and return the new text (same field order, 2-space indent)."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    data = json.loads(text)
    mutate(data)
    return json.dumps(data, indent=2 if "\n" in text.strip() else None, ensure_ascii=False) + "\n"


def _mcp_change(args, out, ctx, servers, removing):
    """Add a server from mcp.json to (or remove it from) one or more crewmates' config files."""
    import time
    from . import mcpjson
    from .state import backup_file
    from .util import atomic_write
    by_name = {s.name: s for s in ctx.specs}
    names = [s.name for s in ctx.specs] if args.all else list(dict.fromkeys(args.crewmates))
    if not names:
        print(out.red("name the crewmates (crewctl mcp add SERVER CREWMATE [CREWMATE ...]) or use --all"))
        return 1
    unknown = [n for n in names if n not in by_name]
    if unknown:
        print(out.red(f"unknown crewmate(s): {', '.join(unknown)}. In the config: {', '.join(by_name) or 'none'}"))
        return 1
    tools = [t.strip() for t in (args.tools or "").split(",") if t.strip()]
    if any(not mcpjson.TOOL_RE.match(t) for t in tools):
        print(out.red("--tools takes tool names separated by commas (letters, digits, '_', '.', '-')."))
        return 1
    if not removing:
        definition = servers[args.server]
        if definition.get("disabled") is True:
            print(out.red(f"'{args.server}' is disabled in {_tilde(ctx.settings.mcp_json)}; enable it there first."))
            return 1
    plan = []
    for name in names:
        spec = by_name[name]
        entries = _mcp_entries(json.load(open(spec.source_file, encoding="utf-8")).get("mcp_json_servers"))
        before = dict(entries)
        if removing:
            entries.pop(args.server, None)
        elif args.server not in entries or tools:
            entries[args.server] = sorted(set(entries.get(args.server, [])) | set(tools)) if tools else (entries.get(args.server) or [])
        if entries != before:
            plan.append((name, before, entries))
    print(f"{'Removing' if removing else 'Adding'} '{args.server}' {'from' if removing else 'to'} {len(names)} crewmate(s):")
    for name in names:
        change = next((c for c in plan if c[0] == name), None)
        if change is None:
            print(f"  {name:<28}{'not using it' if removing else 'already has it' + (' (with these tools)' if tools else '')}")
        else:
            shown = change[2].get(args.server)
            print(f"  {name:<28}" + ("removed" if removing else "tools: " + (", ".join(shown) if shown else "all of its tools")))
    if not plan:
        print(out.green("Nothing to change."))
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print(out.red("Refusing to ask a question without a terminal. Use --yes."))
            return 1
        if input(f"Change {len(plan)} config file(s)? Type 'yes' to continue: ").strip() != "yes":
            print("Cancelled.")
            return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backups = {}
    for name, _, entries in plan:
        spec = by_name[name]

        def mutate(data, entries=entries):
            value = _mcp_value(entries)
            if value is None:
                data.pop("mcp_json_servers", None)
            else:
                data["mcp_json_servers"] = value
        new_text = _edit_agent_file(spec.source_file, mutate)
        backups[name] = backup_file(ctx.config_dir, stamp, spec.source_file, os.path.basename(spec.source_file))
        atomic_write(spec.source_file, new_text.encode("utf-8"), mode=0o644)
    fresh = build_context(ctx.config_dir, _overrides(args), check_workspaces=False)
    bad = [p for p in fresh.problems if p.level == "error"]
    if bad:                                       # never leave a config that no longer validates
        for name, backup in backups.items():
            shutil.copyfile(backup, by_name[name].source_file)
        print(out.red("The change makes the config invalid, so it was undone:"))
        for problem in bad:
            print(out.red(f"  {problem.where}: {problem.message}"))
        return 1
    print(out.green(f"Updated {len(plan)} file(s).") + f" Old versions: {_tilde(os.path.join(ctx.config_dir, '.crewctl', 'backups', stamp))}")
    print(out.dim("Next: `crewctl plan` to see the agent-file change, `crewctl apply`, then start a new chat with each crewmate. "
                  "`register` is not needed: MCP servers are part of the agent file, not of Crew's record."))
    return 0


def cmd_mcp(args):
    """List the MCP servers in mcp.json, or connect to one and list its tools."""
    from . import mcpjson
    out = _out(args)
    config = _config_dir(args)
    settings_problems = []
    from .config import load_settings
    settings = load_settings(config, settings_problems, _overrides(args))
    servers, error = mcpjson.load(settings.mcp_json)
    if error:
        print(out.red(error))
        print("Set \"mcp_json\" in crewctl.json or CREWCTL_MCP_JSON if the file is elsewhere.")
        return 1
    if args.mcp_action in ("add", "remove"):
        if args.mcp_action == "add" and args.server not in servers:
            print(out.red(f"'{args.server}' is not in {settings.mcp_json}. It has: {', '.join(sorted(servers)) or 'no servers'}."))
            return 1
        ctx = _load(args, out, need_state=False, check_workspaces=False)
        if ctx is None:
            return 1
        return _mcp_change(args, out, ctx, servers, removing=args.mcp_action == "remove")
    if args.mcp_action == "list":
        used = {}
        if os.path.isdir(config):
            ctx = build_context(config, _overrides(args), check_workspaces=False)
            for spec in ctx.specs:
                for name in spec.mcp_json_servers:
                    used.setdefault(name, []).append(spec.name)
        print(f"MCP servers in {_tilde(settings.mcp_json)}:")
        print(f"  {'SERVER':<42}{'KIND':<9}{'STATE':<10}USED BY (this config)")
        for name in sorted(servers):
            definition = servers[name]
            state = out.red("disabled") if definition.get("disabled") is True else "enabled"
            print(f"  {out.bold(name):<{42 + (len(out.bold(name)) - len(name))}}{mcpjson.kind(definition):<9}"
                  f"{state:<{10 + (len(state) - len('disabled' if definition.get('disabled') is True else 'enabled'))}}"
                  f"{', '.join(used.get(name, [])) or '-'}")
        print(out.dim("\nAdd a server to a crewmate with  \"mcp_json_servers\": [\"NAME\"]  in its *.agent.json; "
                      "`crewctl mcp tools NAME` shows the tool names you can limit it to."))
        return 0
    name = args.name
    if name not in servers:
        print(out.red(f"'{name}' is not in {settings.mcp_json}. It has: {', '.join(sorted(servers)) or 'no servers'}."))
        return 1
    definition = servers[name]
    if definition.get("disabled") is True:
        print(out.yellow(f"'{name}' is disabled in mcp.json; Kiro would not load it. Connecting anyway to list its tools."))
    if definition.get("url"):
        print(f"Will connect to {name}: {definition['url']}")
    else:
        print(f"Will start {name}: {mcpjson.command_line(definition)}")
        print(out.dim("  (the command from your mcp.json, with its environment; it is stopped again right after the tool list is read)"))
    if not args.yes:
        if not sys.stdin.isatty():
            print(out.red("Refusing to ask a question without a terminal. Use --yes."))
            return 1
        if input("Continue? Type 'yes': ").strip() != "yes":
            print("Cancelled.")
            return 1
    try:
        tools = mcpjson.list_tools(definition, timeout=args.timeout)
    except mcpjson.McpError as error:
        print(out.red(mcpjson.redact(f"Cannot get the tool list from '{name}': {error}", definition)))
        return 1
    print(out.green(f"{name}: {len(tools)} tool(s)"))
    for tool, description in tools:
        print(f"  {out.bold(tool)}  {mcpjson.redact(description, definition)[:110]}")
    print(out.dim(f"\nAll of them:  \"mcp_json_servers\": [\"{name}\"]      Only some:  \"mcp_json_servers\": {{\"{name}\": {{\"tools\": [\"{tools[0][0] if tools else 'tool'}\"]}}}}"))
    return 0


def cmd_guide(args):
    """Build a command step by step, with the effect of every choice shown."""
    from .guide import Guide
    out = _out(args)

    def run(argv):
        try:
            return main(argv)
        except SystemExit as stop:
            return stop.code if isinstance(stop.code, int) else 1
    guide = Guide(build_parser(), _config_dir(args), _overrides(args), out.color, run_fn=run)
    try:
        return guide.run()
    except KeyboardInterrupt:
        print("\nLeft the guide.")
        return 130


def cmd_completion(args):
    """Print a tab-completion script for the chosen shell."""
    from .completion import SCRIPTS
    sys.stdout.write(SCRIPTS[args.shell](build_parser()))
    return 0


def cmd_values(args):
    """Names for tab completion (hidden command): one per line, nothing on any problem."""
    from .guide import World
    for name in World(_config_dir(args), _overrides(args)).values(args.kind):
        print(name)
    return 0


def cmd_plan(args):
    out = _out(args)
    ctx = _load(args, out)
    if ctx is None:
        return 1
    plan = build_plan(ctx, set(args.target) or None)
    render_plan(ctx, plan, out)
    if args.detailed_exitcode:
        return 2 if plan.has_changes() else 0
    return 0


def cmd_apply(args):
    out = _out(args)
    ctx = _load(args, out)
    if ctx is None:
        return 1
    plan = build_plan(ctx, set(args.target) or None)
    render_plan(ctx, plan, out, with_registration=not plan.has_changes())
    blockers = plan.blockers(args.force)
    if blockers:
        print(out.red("\nCannot apply:"))
        for line in blockers:
            print(out.red(f"  {line}"))
        return 1
    refresh_only = not plan.has_changes() and any(c.refresh for c in plan.changes)
    if not plan.has_changes() and not refresh_only:
        return 0
    if plan.has_changes() and not _confirm(args, "\nApply these changes?"):
        print("Cancelled.")
        return 1
    print()
    done = apply_plan(ctx, plan, force=args.force, break_lock=args.break_lock)
    print(out.green(f"\nApply complete: {done['create']} created, {done['update']} updated, {done['replace']} replaced, "
                    f"{done['delete']} deleted" + (f", {done['adopt']} adopted." if done.get("adopt") else ".")))
    plan_after = build_plan(ctx, set(args.target) or None)
    if any(r.kind != "in-sync" for r in plan_after.registrations):
        render_registration(ctx, plan_after, out)
    return 0


def cmd_destroy(args):
    out = _out(args)
    ctx = _load(args, out, check_workspaces=False)
    if ctx is None:
        return 1
    ctx = dataclasses.replace(ctx, specs=[])
    plan = build_plan(ctx, set(args.target) or None)
    if not any(c.action == "delete" for c in plan.changes):
        print("Nothing to destroy: no managed agent files in the state.")
        return 0
    render_plan(ctx, plan, out)
    blockers = plan.blockers(args.force)
    if blockers:
        print(out.red("\nCannot destroy:"))
        for line in blockers:
            print(out.red(f"  {line}"))
        return 1
    orphans = [r for r in plan.registrations if r.kind == "orphan"]
    warning = "\nThis deletes the agent files above (backups are kept)."
    if args.delete_records and orphans:
        warning += " It then runs `kirocrew agent delete` for the crew records listed."
    if not _confirm(args, out.red(warning)):
        print("Cancelled.")
        return 1
    done = apply_plan(ctx, plan, force=args.force, break_lock=args.break_lock)
    print(out.green(f"\nDestroy complete: {done['delete']} agent file(s) removed. Your config files are untouched."))
    if not orphans:
        return 0
    if args.delete_records:
        return _run_commands(ctx, orphans, args, out, confirmed=True)
    print(out.yellow("\nThe crew records still exist in Crew, so these crewmates still show up there (pointing at a file that is gone)."))
    print("To remove them too, run these, or rerun `destroy --delete-records`:")
    for action in orphans:
        for argv in action.commands:
            print(f"  {command_line(ctx.settings.kirocrew_bin, argv)}")
    return 0


def cmd_status(args):
    out = _out(args)
    ctx = _load(args, out)
    if ctx is None:
        return 1
    plan = build_plan(ctx, set(args.target) or None)
    rows = status_rows(ctx, plan)
    if not rows:
        print("No crewmates in the config or the state.")
        return 0
    header = ("CREWMATE", "AGENT FILE", "MODEL", "FILE", "CREW")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(5)]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    return 0


def _run_commands(ctx, actions, args, out, confirmed=False):
    """Run the kirocrew commands in these registration actions. Asks first unless already confirmed."""
    binary = ctx.settings.kirocrew_bin
    if not shutil.which(binary):
        print(out.red(f"'{binary}' was not found on PATH. Set \"kirocrew_bin\" in crewctl.json, or run the commands by hand."))
        return 1
    commands = [command_line(binary, a) for r in actions for a in r.commands]
    print("About to run:\n  " + "\n  ".join(commands))
    if not confirmed and not _confirm(args, ""):
        print("Cancelled.")
        return 1
    try:
        count = run_registration(ctx, Plan(registrations=actions), binary, print)
    except ApplyBlocked as error:
        print(out.red(str(error)))
        return 1
    print(out.green(f"\n{count} command(s) done."))
    return 0


def cmd_register(args):
    out = _out(args)
    ctx = _load(args, out)
    if ctx is None:
        return 1
    plan = build_plan(ctx, set(args.target) or None)
    todo = [r for r in plan.registrations if r.commands or r.dashboard]
    if not todo:
        print("Nothing to register: every crewmate matches the config (or its commands are already done).")
        return 0
    if any(c.action != "noop" for c in plan.changes):
        print(out.yellow("Note: some agent files are not applied yet. Run `crewctl apply` first so the files exist."))
    if not args.run:
        render_registration(ctx, plan, out)
        return 0
    moves = [(r.name, a, b) for r in todo for a, b in r.moves]
    if moves and not args.allow_move:
        print(out.red("Stopped: this would MOVE crewmates to another workspace in Crew (their file has a `workspace` line that differs from Crew's record):"))
        for name, was, now in moves:
            print(f"  {name:<28} {was} -> {now}")
        print("If that is what you want, run again with --allow-move. If not, put the old value back in the file "
              "(`crewctl workspace restore`), or delete the `workspace` line so crewctl leaves Crew's value alone.")
        return 1
    code = _run_commands(ctx, todo, args, out)
    if code:
        return code
    fresh = build_plan(build_context(_config_dir(args), _overrides(args)), set(args.target) or None)
    render_registration(ctx, fresh, out)
    return 0


def cmd_import(args):
    out = _out(args)
    ctx = _load(args, out, check_workspaces=False)
    if ctx is None:
        return 1
    try:
        target = import_existing(ctx, args.name, args.break_lock)
    except ApplyBlocked as error:
        print(out.red(str(error)))
        return 1
    print(out.green(f"Imported {args.name} ({_tilde(target)}). Run `crewctl plan` to see what apply would change."))
    return 0


def cmd_state(args):
    out = _out(args)
    ctx = build_context(_config_dir(args), _overrides(args), check_workspaces=False)
    if ctx.state is None:
        print(out.red("config folder not found."))
        return 1
    if args.state_action == "list":
        for name, entry in sorted(ctx.state.agents.items()):
            print(f"{name}\t{entry['file']}\t{entry['applied_at']}")
        if not ctx.state.agents:
            print("(state is empty)")
    elif args.state_action == "show":
        entry = ctx.state.agents.get(args.name)
        if entry is None:
            print(out.red(f"'{args.name}' is not in the state."))
            return 1
        print(pretty_json(entry), end="")
    elif args.state_action == "rm":
        try:
            state_remove(ctx, args.name, args.break_lock)
        except ApplyBlocked as error:
            print(out.red(str(error)))
            return 1
        print(f"Forgot '{args.name}'. The agent file was not touched.")
    elif args.state_action == "mv":
        try:
            state_move(ctx, args.name, args.new_name, args.break_lock)
        except ApplyBlocked as error:
            print(out.red(str(error)))
            return 1
        print(f"Renamed state entry '{args.name}' -> '{args.new_name}'.")
    return 0


def _minutes(text):
    value = int(text) if text.isdigit() else 0
    if not 1 <= value <= 60:
        raise argparse.ArgumentTypeError("give a whole number of minutes from 1 to 60")
    return value


def build_parser():
    parser = ColorParser(prog="crewctl", description=render_overview(ColorParser.enabled), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"crewctl {__version__}")
    parser.add_argument("-c", "--config-dir", help="folder with the *.agent.json files (default: $CREWCTL_DIR or ./crew)")
    parser.add_argument("--agents-dir", help="where Kiro agent files go (default: ~/.kiro/agents or $CREWCTL_AGENTS_DIR)")
    parser.add_argument("--crew-home", help="Crew's data folder, read-only (default: $KIROCREW_HOME or ~/.kiro/crew)")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="also show notes")
    parser.epilog = "Run `crewctl` with no command for a short overview of what it can do."
    sub = parser.add_subparsers(dest="command", required=False)
    sub.add_parser("overview", help="what crewctl can do, in two groups").set_defaults(func=cmd_overview)
    p = sub.add_parser("completion", help="print a tab-completion script for bash, zsh or fish")
    p.add_argument("shell", choices=["bash", "zsh", "fish"])
    p.set_defaults(func=cmd_completion)
    p = sub.add_parser("values", help=argparse.SUPPRESS)
    p.add_argument("kind", choices=["crewmates", "workspaces", "mcp", "skills", "teams"])
    p.set_defaults(func=cmd_values)
    sub.add_parser("guide", help="build a command step by step; every choice shows the command and what it does").set_defaults(func=cmd_guide)
    p = sub.add_parser("review", help="agent-review kit: install it into a project, or re-check citations")
    review = p.add_subparsers(dest="review_action", required=True)
    q = review.add_parser("install", help="install the review method into a project")
    q.add_argument("project", help="the project folder to review")
    q.add_argument("--surface", choices=["crew", "ide"], default="crew", help="Kiro Crew (default) or Kiro IDE")
    q.add_argument("--reference-source", metavar="PATH", help="a clone of the public system_prompts_leaks repository")
    q.add_argument("--force", action="store_true", help="also replace docs/agent-review-method/inputs.md")
    q = review.add_parser("check", help="re-check every file:line citation in a project's review output")
    q.add_argument("project", nargs="?", default=".", help="the reviewed project (default: current folder)")
    q.add_argument("extra", nargs=argparse.REMAINDER, help="extra options for the checker, after --")
    review.add_parser("where", help="show where the review kit is").set_defaults(project=".")
    p.set_defaults(func=cmd_review)
    p = sub.add_parser("usage", help="send Kiro Crew's credit usage to Grafana or any OTLP backend")
    usub = p.add_subparsers(dest="usage_action", required=True)
    q = usub.add_parser("init", help="create the settings file usage.json (export stays off until you set an endpoint)")
    q.add_argument("--force", action="store_true", help="replace an existing usage.json with the template")
    q = usub.add_parser("push", help="send what Crew recorded since the last run, then exit (run it from a scheduler)")
    q.add_argument("--dry-run", action="store_true", help="show exactly what would be sent; send nothing, move nothing")
    q.add_argument("--quiet", action="store_true", help="print only errors (for cron and timers)")
    q.add_argument("--max-rows", type=int, metavar="N", help="read at most N usage rows in this run")
    q.add_argument("--break-lock", action="store_true", help="remove a lock left by a push that was cut off")
    usub.add_parser("status", help="settings, last run, last error and how far behind Crew's files the export is")
    q = usub.add_parser("schedule", help="print the cron, systemd or launchd lines that run push every few minutes (installs nothing)")
    q.add_argument("scheduler", nargs="?", choices=["cron", "systemd", "launchd"], help="default: launchd on macOS, systemd on Linux with systemd, else cron")
    q.add_argument("--every", type=_minutes, default=5, metavar="MINUTES", help="minutes between runs, 1 to 60 (default 5)")
    for q in usub.choices.values():
        q.add_argument("--settings", metavar="FILE", help="the settings file (default: $CREWCTL_USAGE_CONFIG or ~/.config/crewctl/usage.json)")
    p.set_defaults(func=cmd_usage)

    def with_common(p, target=True, yes=False, force=False):
        if target:
            p.add_argument("--target", action="append", default=[], metavar="NAME", help="limit to this crewmate (repeatable)")
        if yes:
            p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
        if force:
            p.add_argument("--force", action="store_true", help="overwrite or delete files that were changed outside crewctl (backups are kept)")
        p.add_argument("--break-lock", action="store_true", help="remove a stuck state lock")
        return p

    p = sub.add_parser("init", help="create a starter config folder, optionally with a ready-made team")
    p.add_argument("--force", action="store_true", help="overwrite the starter files if the folder already has a crewctl.json")
    p.add_argument("--team", choices=sorted(TEAMS), help="also copy a ready-made team: devteam (ten crewmates) or example (three small ones)")
    p.add_argument("--update", action="store_true", help="with --team: bring an existing copy up to date with the repository; keeps crewctl.json and _defaults.json, saves what it overwrites")
    p.add_argument("-y", "--yes", action="store_true", help="with --update: do not ask for confirmation")
    p.set_defaults(func=cmd_init)
    sub.add_parser("validate", help="check the config, change nothing").set_defaults(func=cmd_validate)
    sub.add_parser("models", help="show the model catalog").set_defaults(func=cmd_models)
    p = sub.add_parser("lint-skills", help="check SKILL.md files (folder or file paths)")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_lint_skills)
    p = sub.add_parser("mcp", help="list the MCP servers in mcp.json, or connect to one and list its tools")
    msub = p.add_subparsers(dest="mcp_action", required=True)
    msub.add_parser("list", help="the servers in mcp.json (default ~/.kiro/settings/mcp.json) and which crewmates use them")
    for action, text in (("add", "add a server from mcp.json to one or more crewmates' config files"),
                         ("remove", "remove a server from one or more crewmates' config files")):
        q = msub.add_parser(action, help=text)
        q.add_argument("server", metavar="SERVER", help="the server name in mcp.json")
        q.add_argument("crewmates", nargs="*", metavar="CREWMATE", help="one or more crewmates (names in the config)")
        q.add_argument("--all", action="store_true", help="every crewmate in the config")
        if action == "add":
            q.add_argument("--tools", metavar="A,B", help="limit the server to these tools (default: all of its tools)")
        else:
            q.set_defaults(tools="")
        q.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    q = msub.add_parser("tools", help="start or call one server, as Kiro would, and list its tool names")
    q.add_argument("name", help="the server name in mcp.json")
    q.add_argument("--timeout", type=int, default=20, help="seconds to wait for the server (default 20)")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask before starting the server")
    p.set_defaults(func=cmd_mcp)
    p = sub.add_parser("workspace", help="list the workspaces, check the ones the crewmates use, create missing ones")
    wsub = p.add_subparsers(dest="workspace_action", required=True)
    wsub.add_parser("list", help="the workspaces Crew has, and which crewmates use them")
    q = wsub.add_parser("check", help="find workspaces the config uses but Crew lacks, and create them (asks for the details)")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask: create each missing workspace with the default folder name")
    q.add_argument("--copy-from", metavar="NAME", help="with --yes: fill the new workspace with the files of this existing workspace")
    q = wsub.add_parser("assign", help="set the workspace of existing crewmates: edits their config files and updates their Crew records")
    q.add_argument("workspace_name", metavar="WORKSPACE", help="the workspace (created first if it does not exist)")
    q.add_argument("crewmates", nargs="*", metavar="CREWMATE", help="one or more crewmates; asked for when left out")
    q.add_argument("--all", action="store_true", help="every crewmate in the config")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask; create the workspace with the default folder if it is missing")
    q = wsub.add_parser("restore", help="put crewmates back on the workspace a backup of their config file names (after an overwrite)")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    q = wsub.add_parser("create", help="create one workspace")
    q.add_argument("name", help="workspace name (letters, digits, hyphens, underscores)")
    q.add_argument("--dir", metavar="FOLDER", help="folder name under Crew's data folder (default: workspace-<name>)")
    q.add_argument("--copy-from", metavar="NAME", help="fill it with the files of this existing workspace (optional)")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_workspace)
    sub.add_parser("crew-servers", help="check where Crew's own tool servers are found").set_defaults(func=cmd_crew_servers)
    p = sub.add_parser("skills", help="build SKILL.md files from skills/*.skill.json")
    p.add_argument("skills_action", choices=["new", "plan", "apply", "destroy"])
    p.add_argument("name", nargs="?", help="skill name (for `new`)")
    p.add_argument("--target", action="append", default=[], metavar="NAME", help="limit to this skill (repeatable)")
    p.add_argument("--show", action="store_true", help="plan: print the SKILL.md that would be written")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--force", action="store_true", help="overwrite or delete files changed outside crewctl (backups are kept)")
    p.add_argument("--adopt", action="store_true", help="apply: take over an existing skill folder crewctl has no record of (old files are saved first); never for Crew's built-in skills")
    p.add_argument("--break-lock", action="store_true", help="remove a stuck state lock")
    p.set_defaults(func=cmd_skills)
    p = with_common(sub.add_parser("plan", help="show what apply would do"))
    p.add_argument("--detailed-exitcode", action="store_true", help="exit 2 if there are changes (for CI)")
    p.set_defaults(func=cmd_plan)
    p = with_common(sub.add_parser("apply", help="write the agent files and update the state"), yes=True, force=True)
    p.set_defaults(func=cmd_apply)
    p = with_common(sub.add_parser("destroy", help="remove the managed agent files"), yes=True, force=True)
    p.add_argument("--delete-records", action="store_true", help="also run `kirocrew agent delete` for their crew records")
    p.set_defaults(func=cmd_destroy)
    with_common(sub.add_parser("status", help="one line per crewmate")).set_defaults(func=cmd_status)
    p = with_common(sub.add_parser("register", help="show (or with --run, execute) the kirocrew commands"), yes=True)
    p.add_argument("--run", action="store_true", help="run the kirocrew commands instead of printing them")
    p.add_argument("--allow-move", action="store_true", help="with --run: also move crewmates whose file names a different workspace than Crew has")
    p.set_defaults(func=cmd_register)
    p = with_common(sub.add_parser("import", help="adopt an agent file that already exists"), target=False)
    p.add_argument("name", help="the agent file to adopt (name of the crewmate)")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("state", help="inspect or edit the state")
    st = p.add_subparsers(dest="state_action", required=True)
    st.add_parser("list")
    for action in ("show", "rm"):
        q = st.add_parser(action)
        q.add_argument("name", help="the crewmate in the state file")
        q.add_argument("--break-lock", action="store_true", help="remove a stuck state lock")
    q = st.add_parser("mv")
    q.add_argument("name", help="the crewmate to rename in the state file")
    q.add_argument("new_name", help="the new name")
    q.add_argument("--break-lock", action="store_true", help="remove a stuck state lock")
    p.set_defaults(func=cmd_state)
    return parser


def main(argv=None):
    ColorParser.enabled = use_color(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_overview(args)
    if getattr(args, "extra", None) and args.extra[:1] == ["--"]:
        args.extra = args.extra[1:]
    for attr in ("break_lock", "yes", "force", "target", "detailed_exitcode", "delete_records"):
        if not hasattr(args, attr):
            setattr(args, attr, [] if attr == "target" else False)
    try:
        return args.func(args)
    except (StateError, ApplyBlocked, LockError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def main_entry():
    """Console-script entry point: `crewctl ...` (same as `python3 -m crewctl ...`)."""
    sys.exit(main())
