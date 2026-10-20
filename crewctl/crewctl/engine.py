"""Plan and apply: the Terraform-like core.

Three things are compared for every crewmate:

    config   what your *.agent.json says you want
    state    what crewctl last applied (hashes), in .crewctl/state.json
    disk     what is in the agents folder right now

    config == disk                 nothing to do
    config != disk, disk == state  update (you changed the config)
    disk != state                  drift (someone edited the file outside crewctl); needs --force to overwrite
    file exists, not in state      conflict (run `crewctl import NAME` to adopt it)
    in state, not in config        delete

crewctl writes only the Kiro agent files. For the crew records (Crew's own config.json) it
only works out the `kirocrew` commands to run; see plan_registration.
"""

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from . import config as cfg
from .crewhome import CLI_FIELDS, load_view, record_differences
from .mapping import kiro_agent_bytes, to_registration
from .state import StateError, backup_file, load_state, new_entry, save_state
from .util import FileLock, atomic_write, now_iso, sha256_bytes, sha256_file


class ApplyBlocked(RuntimeError):
    pass


@dataclass
class Change:
    name: str
    action: str                  # create | update | replace | delete | noop
    kiro_agent: str
    reasons: list = field(default_factory=list)
    new_bytes: bytes = b""
    old_bytes: bytes = b""
    drift: bool = False          # disk differs from the last apply
    conflict: str = ""           # non-empty means blocked
    old_file: str = ""           # the file a replace/delete removes
    refresh: bool = False        # noop, but the state hash should be refreshed
    spec: object = None


@dataclass
class RegAction:
    name: str
    kind: str                    # register | update | in-sync | orphan | unknown
    commands: list = field(default_factory=list)   # argv lists, without the kirocrew binary
    dashboard: dict = field(default_factory=dict)  # fields only the dashboard can set
    notes: list = field(default_factory=list)


@dataclass
class Plan:
    changes: list = field(default_factory=list)
    registrations: list = field(default_factory=list)

    def counts(self):
        out = {}
        for change in self.changes:
            out[change.action] = out.get(change.action, 0) + 1
        return out

    def has_changes(self):
        return any(c.action != "noop" or c.conflict for c in self.changes)

    def blockers(self, force):
        problems = []
        for change in self.changes:
            if change.conflict:
                problems.append(f"{change.name}: {change.conflict}")
            elif change.drift and not force:
                problems.append(f"{change.name}: {change.kiro_agent}.json was changed outside crewctl since the last apply. "
                                "Review the diff, then use --force to overwrite it (a backup is kept).")
        return problems


@dataclass
class Context:
    config_dir: str
    settings: object
    specs: list
    state: object
    view: object
    problems: list


def build_context(config_dir, overrides=None, check_workspaces=True):
    config_dir = os.path.abspath(config_dir)
    settings, specs, problems = cfg.load_config(
        config_dir, overrides, local_models=lambda s: load_view(s.crew_home).model_ids)
    view = load_view(settings.crew_home)
    if check_workspaces:
        problems.extend(workspace_problems(specs, view))
    state = load_state(config_dir) if os.path.isdir(config_dir) else None
    return Context(config_dir, settings, specs, state, view, problems)


def workspace_problems(specs, view):
    """A workspace that Crew does not have is an error: Crew would silently use the default one instead."""
    from . import workspaces as ws
    from .config import Problem
    used = [s for s in specs if s.workspace != view.default_workspace and s.workspace != "default"]
    if not used:
        return []
    if not view.usable:
        return [Problem("warn", "workspace", f"cannot check that the workspaces exist: {view.error}. "
                                             f"Used by the config: {', '.join(sorted({s.workspace for s in used}))}.")]
    out = []
    have = ", ".join(sorted(view.workspaces)) or "none"
    for name, users in sorted(ws.missing_workspaces(view, specs).items()):
        out.append(Problem("error", os.path.basename(next(s.source_file for s in specs if s.name == users[0])),
                           f"workspace '{name}' does not exist in Crew (it has: {have}). Used by: {', '.join(users)}. "
                           f"Create it with `crewctl workspace check` (it asks for the details) or "
                           f"`crewctl workspace create {name}`.", kind="workspace_missing"))
    return out


def _agent_path(ctx, kiro_agent):
    return os.path.join(ctx.settings.agents_dir, f"{kiro_agent}.json")


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def build_plan(ctx, targets=None):
    """Compare config, state and disk. targets, if given, limits the plan to those crewmate names."""
    plan = Plan()
    want = {s.name: s for s in ctx.specs if not targets or s.name in targets}
    state_names = {n for n in ctx.state.agents if not targets or n in targets}
    owner_of_file = {e["file"]: n for n, e in ctx.state.agents.items()}

    for name, spec in sorted(want.items()):
        new_bytes = kiro_agent_bytes(spec)
        new_sha = sha256_bytes(new_bytes)
        target = _agent_path(ctx, spec.kiro_agent)
        disk = _read(target)
        disk_sha = sha256_bytes(disk) if disk is not None else None
        entry = ctx.state.agents.get(name)
        change = Change(name=name, action="noop", kiro_agent=spec.kiro_agent, new_bytes=new_bytes,
                        old_bytes=disk or b"", spec=spec)
        if entry is None:
            if disk is None:
                change.action = "create"
                change.reasons.append("new crewmate")
            else:
                other = owner_of_file.get(f"{spec.kiro_agent}.json")
                change.action = "create"
                if other:
                    change.conflict = (f"{spec.kiro_agent}.json is already managed under the name '{other}'. If this is a rename, "
                                       f"run `crewctl state mv {other} {name}` first.")
                elif disk_sha == new_sha:
                    change.action = "adopt"
                    change.reasons.append("the file already holds exactly this config (for example it was written from another "
                                          "config folder); it is recorded in the state without being written")
                else:
                    change.conflict = (f"{spec.kiro_agent}.json already exists and this config folder's state has no record of "
                                       f"crewctl creating it (the state is kept per config folder, in .crewctl/state.json; if you applied from "
                                       f"another config folder, use that one). Run `crewctl import {name}` to adopt it (the plan then "
                                       f"shows what would change), or move it away.")
        else:
            recorded = entry.get("file_sha256")
            if entry.get("kiro_agent") != spec.kiro_agent:
                change.action = "replace"
                change.old_file = os.path.join(ctx.settings.agents_dir, entry["file"])
                change.reasons.append(f"agent file name changed: {entry['kiro_agent']} -> {spec.kiro_agent}")
                old_sha = sha256_file(change.old_file)
                if old_sha is not None and old_sha != recorded:
                    change.drift = True
                    change.reasons.append(f"the old file {entry['file']} was changed outside crewctl")
                if disk is not None:
                    change.conflict = f"{spec.kiro_agent}.json already exists; move it away or import it first."
            elif disk is None:
                change.action = "create"
                change.reasons.append("the file is missing on disk (deleted outside crewctl)")
            elif disk_sha == new_sha:
                change.action = "noop"
                change.refresh = recorded != new_sha
            elif disk_sha == recorded:
                change.action = "update"
                change.reasons.append("config changed")
            else:
                change.action = "update"
                change.drift = True
                change.reasons.append("the file was changed outside crewctl since the last apply (drift)")
        plan.changes.append(change)

    for name in sorted(state_names - set(want)):
        entry = ctx.state.agents[name]
        path = os.path.join(ctx.settings.agents_dir, entry["file"])
        disk = _read(path)
        change = Change(name=name, action="delete", kiro_agent=entry["kiro_agent"], old_bytes=disk or b"", old_file=path)
        change.reasons.append("removed from the config")
        if disk is not None and sha256_bytes(disk) != entry.get("file_sha256"):
            change.drift = True
            change.reasons.append("the file was changed outside crewctl since the last apply")
        if disk is None:
            change.reasons.append("the file is already gone; only the state entry is removed")
        plan.changes.append(change)

    leftover = {n for n in ctx.state.removed if not targets or n in targets}
    plan.registrations = plan_registration(ctx, [s for s in want.values()],
                                           sorted((state_names | leftover) - set(want)))
    return plan


SETTABLE_BY_CONFIG = ("display_name", "description", "triggers", "reasoning_effort", "session_color", "starred")


def config_set_commands(name, wanted):
    """`kirocrew config set agents.<name>.<field> <value>` for each field. Returns (commands, leftovers).

    Crew's own CLI writes these fields (no dashboard, no token). Values are JSON-encoded so that text like "123" or
    "true" stays text. A name with a dot cannot be written as a dotted key, so those fields are left for the dashboard.
    """
    if "." in name:
        return [], dict(wanted)
    commands = []
    for key in SETTABLE_BY_CONFIG:
        if key in wanted:
            value = wanted[key]
            commands.append(["config", "set", f"agents.{name}.{key}", "true" if value is True else json.dumps(value, ensure_ascii=False)])
    return commands, {k: v for k, v in wanted.items() if k not in SETTABLE_BY_CONFIG}


def plan_registration(ctx, specs, removed_names):
    """Work out what is left to do in Crew itself. Nothing here writes anything."""
    view = ctx.view
    actions = []
    for spec in specs:
        desired = to_registration(spec)
        dashboard_wanted = {k: desired[k] for k in ("display_name", "description", "triggers", "reasoning_effort",
                                                     "session_color") if desired[k]}
        if desired["starred"]:
            dashboard_wanted["starred"] = True
        record = view.agents.get(spec.name)
        action = RegAction(name=spec.name, kind="in-sync")
        if not view.config_found:
            action.kind = "unknown"
            action.notes.append(f"could not read Crew's config ({view.error}); showing the commands for a fresh crewmate")
            set_commands, action.dashboard = config_set_commands(spec.name, dashboard_wanted)
            action.commands = [["agent", "create", "--name", spec.name, "--kiro-agent", spec.kiro_agent,
                                                  "--workspace", spec.workspace]] + set_commands
        elif record is None:
            action.kind = "register"
            set_commands, action.dashboard = config_set_commands(spec.name, dashboard_wanted)
            action.commands = [["agent", "create", "--name", spec.name, "--kiro-agent", spec.kiro_agent,
                                                  "--workspace", spec.workspace]] + set_commands
        else:
            diffs = record_differences(desired, record)
            if record.get("model"):
                action.notes.append(f"the crew record pins model '{record['model']}', which overrides the model in the agent "
                                    "file. Clear it in the dashboard (Agent Capabilities -> Agents) to use the file's model.")
            cli_diffs = [f for f in CLI_FIELDS if f in diffs]
            if "workspace" in diffs:
                wanted_ws, actual_ws = diffs["workspace"]
                action.notes.append(f"MOVES this crewmate from workspace '{actual_ws}' to '{wanted_ws}' (the config says '{wanted_ws}'). "
                                    "If that is not what you want, fix the `workspace` line in its config file first "
                                    "(`crewctl workspace assign` or `crewctl workspace restore`).")
            if cli_diffs:
                action.kind = "update"
                update = ["agent", "update", spec.name]
                if "kiro_agent" in cli_diffs:
                    update += ["--kiro-agent", spec.kiro_agent]
                if "workspace" in cli_diffs:
                    update += ["--workspace", spec.workspace]
                action.commands = [update] if cli_diffs else []
            other = {f: v for f, v in diffs.items() if f not in CLI_FIELDS}
            if other:
                action.kind = "update" if action.kind == "in-sync" else action.kind
                set_commands, action.dashboard = config_set_commands(spec.name, {f: wanted for f, (wanted, actual) in other.items()})
                action.commands = action.commands + set_commands
                action.notes.extend(f"{f} is currently {actual!r}" for f, (wanted, actual) in other.items())
        actions.append(action)
    for name in removed_names:
        if name in view.agents:
            actions.append(RegAction(name=name, kind="orphan", commands=[["agent", "delete", name]],
                                     notes=["the crew record still exists but its agent file is gone or going. `agent delete` removes the record "
                                          "and its team entry; it does not appear to delete the crewmate's private memory"]))
    return actions


def command_line(binary, argv):
    return shlex.join([binary] + argv)


def run_commands(binary, commands, log):
    """Run kirocrew commands one by one; stop at the first failure. Returns how many ran."""
    done = 0
    for argv in commands:
        log(f"$ {command_line(binary, argv)}")
        try:
            result = subprocess.run([binary] + argv, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            raise ApplyBlocked(f"could not run {binary}: {error}")
        if result.stdout.strip():
            log(result.stdout.strip())
        if result.returncode != 0:
            raise ApplyBlocked(f"{binary} {' '.join(argv[:2])} failed (exit {result.returncode}): {result.stderr.strip()[:300]}")
        done += 1
    return done


def run_registration(ctx, plan, binary, log):
    """Run the kirocrew commands from the plan. Only called when the user asks for --run."""
    return run_commands(binary, [argv for action in plan.registrations for argv in action.commands], log)


def apply_plan(ctx, plan, force=False, log=print, break_lock=False):
    """Write the agent files and update the state. Returns the counts of what was done."""
    blockers = plan.blockers(force)
    if blockers:
        raise ApplyBlocked("cannot apply:\n  " + "\n  ".join(blockers))
    from .state import lock_path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    done = {"create": 0, "update": 0, "replace": 0, "delete": 0, "refresh": 0, "adopt": 0}
    with FileLock(lock_path(ctx.config_dir), break_lock=break_lock):
        state = load_state(ctx.config_dir)   # re-read under the lock
        ordered = [c for c in plan.changes if c.action not in ("delete",)] + [c for c in plan.changes if c.action == "delete"]
        for change in ordered:
            if change.action == "noop":
                if change.refresh and change.name in state.agents:
                    state.agents[change.name]["file_sha256"] = sha256_bytes(change.new_bytes)
                    done["refresh"] += 1
                continue
            if change.action in ("create", "update", "replace"):
                spec = change.spec
                target = _agent_path(ctx, spec.kiro_agent)
                if os.path.exists(target):
                    backup_file(ctx.config_dir, stamp, target, f"{spec.kiro_agent}.json")
                atomic_write(target, change.new_bytes)
                if change.action == "replace" and os.path.exists(change.old_file):
                    backup_file(ctx.config_dir, stamp, change.old_file, os.path.basename(change.old_file))
                    os.unlink(change.old_file)
                state.removed.pop(change.name, None)
                state.agents[change.name] = new_entry(
                    change.name, spec.kiro_agent, sha256_bytes(change.new_bytes),
                    os.path.relpath(spec.source_file, ctx.config_dir), to_registration(spec))
                log(f"  applied {change.action}: {change.name} -> {target}")
            elif change.action == "adopt":
                spec = change.spec
                state.removed.pop(change.name, None)
                state.agents[change.name] = new_entry(
                    change.name, spec.kiro_agent, sha256_bytes(change.new_bytes),
                    os.path.relpath(spec.source_file, ctx.config_dir), to_registration(spec))
                log(f"  adopted: {change.name} ({_agent_path(ctx, spec.kiro_agent)}) - already identical")
            elif change.action == "delete":
                if os.path.exists(change.old_file):
                    backup_file(ctx.config_dir, stamp, change.old_file, os.path.basename(change.old_file))
                    os.unlink(change.old_file)
                state.agents.pop(change.name, None)
                state.removed[change.name] = {"removed_at": now_iso(), "kiro_agent": change.kiro_agent}
                log(f"  applied delete: {change.name} ({change.old_file})")
            done[change.action] += 1
            state.serial += 1
            save_state(ctx.config_dir, state)
        if done["refresh"] and not any(done[k] for k in ("create", "update", "replace", "delete")):
            state.serial += 1
        if ctx.view.config_found:       # forget removed crewmates whose Crew record is already gone
            for name in [n for n in state.removed if n not in ctx.view.agents]:
                state.removed.pop(name)
        save_state(ctx.config_dir, state)
    ctx.state = state
    return done


def import_existing(ctx, name, break_lock=False):
    """Adopt an existing agent file: record its hash so the next plan can show what would change."""
    spec = next((s for s in ctx.specs if s.name == name), None)
    if spec is None:
        raise ApplyBlocked(f"'{name}' is not in the config. Add a {name}.agent.json first.")
    from .state import lock_path
    with FileLock(lock_path(ctx.config_dir), break_lock=break_lock):
        state = load_state(ctx.config_dir)
        if name in state.agents:
            raise ApplyBlocked(f"'{name}' is already managed.")
        target = _agent_path(ctx, spec.kiro_agent)
        sha = sha256_file(target)
        if sha is None:
            raise ApplyBlocked(f"{target} does not exist; nothing to import. Use `apply` to create it.")
        state.agents[name] = new_entry(name, spec.kiro_agent, sha, os.path.relpath(spec.source_file, ctx.config_dir),
                                       to_registration(spec))
        state.serial += 1
        save_state(ctx.config_dir, state)
    return target


def state_remove(ctx, name, break_lock=False):
    from .state import lock_path
    with FileLock(lock_path(ctx.config_dir), break_lock=break_lock):
        state = load_state(ctx.config_dir)
        if name in state.removed and name not in state.agents:
            state.removed.pop(name)                      # stop reminding me about this crew record
        elif name not in state.agents:
            raise ApplyBlocked(f"'{name}' is not in the state.")
        else:
            state.agents.pop(name)
        state.serial += 1
        save_state(ctx.config_dir, state)


def state_move(ctx, old, new, break_lock=False):
    from .state import lock_path
    with FileLock(lock_path(ctx.config_dir), break_lock=break_lock):
        state = load_state(ctx.config_dir)
        if old not in state.agents:
            raise ApplyBlocked(f"'{old}' is not in the state.")
        if new in state.agents:
            raise ApplyBlocked(f"'{new}' is already in the state.")
        state.agents[new] = state.agents.pop(old)
        state.serial += 1
        save_state(ctx.config_dir, state)


def status_rows(ctx, plan):
    """One row per crewmate for `crewctl status`."""
    regs = {r.name: r for r in plan.registrations}
    rows = []
    for change in plan.changes:
        file_status = {"create": "to create", "update": "to update", "replace": "to replace", "delete": "to delete",
                       "noop": "in sync"}[change.action]
        if change.conflict:
            file_status = "CONFLICT"
        elif change.drift:
            file_status = "DRIFT"
        reg = regs.get(change.name)
        if reg is None:
            crew = "-"
        elif reg.kind == "in-sync":
            crew = "registered"
        elif reg.kind == "register":
            crew = "not registered"
        elif reg.kind == "unknown":
            crew = "unknown"
        elif reg.kind == "orphan":
            crew = "record left behind"
        else:
            crew = "differs"
        spec = change.spec
        rows.append((change.name, change.kiro_agent, (spec.model or "auto") if spec else "-", file_status, crew))
    return rows
