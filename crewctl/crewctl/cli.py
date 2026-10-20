"""Command-line interface: init, validate, models, plan, apply, destroy, status, register, import, state."""

import argparse
import dataclasses
import difflib
import json
import os
import shutil
import sys

from . import __version__
from . import models as model_catalog
from .engine import (ApplyBlocked, Plan, apply_plan, build_context, build_plan, command_line, import_existing,
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


def _load(args, out, need_state=True):
    ctx = build_context(_config_dir(args), _overrides(args))
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
               "delete": ("-", out.red), "noop": (" ", out.dim)}
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
    summary = ", ".join(f"{counts.get(a, 0)} to {a}" for a in ("create", "update", "replace", "delete"))
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


def _copy_team(source, target):
    """Copy a ready-made team (agent files, prompts, skills, scripts) into the config folder."""
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".crewctl", "__pycache__", "README.md", "SETUP.md"))
    return sorted(f[:-len(".agent.json")] for f in os.listdir(source) if f.endswith(".agent.json"))


def cmd_init(args):
    out = _out(args)
    target = _config_dir(args)
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
    marks = {"create": out.green("+ create"), "update": out.yellow("~ update"), "delete": out.red("- delete"), "noop": out.dim("  no change")}
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
                                force=args.force, break_lock=args.break_lock)
    except SkillsBlocked as error:
        print(out.red(str(error)))
        return 1
    print(f"\n{args.skills_action.capitalize()} complete: {done['create']} created, {done['update']} updated, {done['delete']} deleted.")
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
                    f"{done['delete']} deleted."))
    plan_after = build_plan(ctx, set(args.target) or None)
    if any(r.kind != "in-sync" for r in plan_after.registrations):
        render_registration(ctx, plan_after, out)
    return 0


def cmd_destroy(args):
    out = _out(args)
    ctx = _load(args, out)
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
    code = _run_commands(ctx, todo, args, out)
    if code:
        return code
    fresh = build_plan(build_context(_config_dir(args), _overrides(args)), set(args.target) or None)
    render_registration(ctx, fresh, out)
    return 0


def cmd_import(args):
    out = _out(args)
    ctx = _load(args, out)
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
    ctx = build_context(_config_dir(args), _overrides(args))
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
    p.set_defaults(func=cmd_init)
    sub.add_parser("validate", help="check the config, change nothing").set_defaults(func=cmd_validate)
    sub.add_parser("models", help="show the model catalog").set_defaults(func=cmd_models)
    p = sub.add_parser("lint-skills", help="check SKILL.md files (folder or file paths)")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_lint_skills)
    sub.add_parser("crew-servers", help="check where Crew's own tool servers are found").set_defaults(func=cmd_crew_servers)
    p = sub.add_parser("skills", help="build SKILL.md files from skills/*.skill.json")
    p.add_argument("skills_action", choices=["new", "plan", "apply", "destroy"])
    p.add_argument("name", nargs="?", help="skill name (for `new`)")
    p.add_argument("--target", action="append", default=[], metavar="NAME", help="limit to this skill (repeatable)")
    p.add_argument("--show", action="store_true", help="plan: print the SKILL.md that would be written")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--force", action="store_true", help="overwrite or delete files changed outside crewctl (backups are kept)")
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
