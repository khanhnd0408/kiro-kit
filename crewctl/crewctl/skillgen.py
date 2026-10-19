"""Build SKILL.md files from skills/*.skill.json and keep them in sync, with state like the agents.

    <config folder>/skills/<name>.skill.json   one spec per skill (files starting with _ are ignored)
    <config folder>/.crewctl/skills.json       what crewctl last wrote: folder and a hash for each file

A spec has a destination: "crew" writes to settings.skills_dir (default <crew home>/skills, shared by
the whole crew) and "project" writes to settings.project_skills_dir (a .kiro/skills folder in a repo).
A skill folder crewctl did not create is never overwritten.
"""

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from .config import Problem, load_settings
from .skilllint import NAME_RE, MAX_NAME, lint_file
from .state import backup_file, state_dir
from .util import FileLock, atomic_write, now_iso, pretty_json, sha256_bytes, sha256_file

SPEC_KEYS = ("name", "description", "triggers", "destination", "title", "goal", "when_to_use", "steps", "output",
             "gotchas", "always", "repo_scope", "body_file", "files")
DESTINATIONS = ("crew", "project")
STATE_VERSION = 1


class SkillsBlocked(RuntimeError):
    pass


@dataclass
class SkillSpec:
    name: str
    source_file: str
    description: str = ""
    triggers: list = field(default_factory=list)
    destination: str = "crew"
    title: str = ""
    goal: str = ""
    when_to_use: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    output: object = ""
    gotchas: list = field(default_factory=list)
    always: bool = False
    repo_scope: str = ""
    body_file: str = ""
    files: dict = field(default_factory=dict)   # path inside the skill folder -> source path


@dataclass
class SkillChange:
    name: str
    action: str                 # create | update | noop | delete
    dest: str
    files: dict = field(default_factory=dict)       # rel path -> bytes to write
    executable: set = field(default_factory=set)    # rel paths that get the execute permission
    remove: list = field(default_factory=list)      # rel paths to delete
    notes: list = field(default_factory=list)
    blockers: list = field(default_factory=list)    # reasons this cannot be applied without --force
    hard: list = field(default_factory=list)       # reasons that --force never overrides


def skills_state_path(config_dir):
    return os.path.join(state_dir(config_dir), "skills.json")


def skills_lock_path(config_dir):
    return os.path.join(state_dir(config_dir), "skills.lock")


def load_skills_state(config_dir):
    path = skills_state_path(config_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != STATE_VERSION or not isinstance(data.get("skills"), dict):
            raise ValueError("unexpected layout")
    except (OSError, ValueError, AttributeError) as error:
        raise SkillsBlocked(f"cannot read {path}: {error}. Restore it from {state_dir(config_dir)}/backups or remove it.")
    return data["skills"]


def save_skills_state(config_dir, skills):
    atomic_write(skills_state_path(config_dir), pretty_json({"version": STATE_VERSION, "skills": skills}).encode("utf-8"))


# ---------- reading the specs ----------

def _text_list(value, where, problems, key):
    if value in ("", None):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
        return list(value)
    problems.append(Problem("error", where, f"'{key}' must be a text or a list of non-empty texts"))
    return []


def _one_line(value, where, problems, key):
    if not isinstance(value, str):
        problems.append(Problem("error", where, f"'{key}' must be text"))
        return ""
    if "\n" in value or "\r" in value:
        problems.append(Problem("error", where, f"'{key}' must be one line"))
    return value.strip()


def _safe_source(config_dir, value, where, problems, key):
    path = os.path.realpath(os.path.join(config_dir, value))
    root = os.path.realpath(config_dir)
    if not (path == root or path.startswith(root + os.sep)):
        problems.append(Problem("error", where, f"{key} '{value}' points outside the config folder"))
    elif not os.path.isfile(path):
        problems.append(Problem("error", where, f"{key} '{value}' is not a file"))
    return path


def load_skill_specs(config_dir):
    """Return (settings, specs, problems)."""
    problems = []
    config_dir = os.path.abspath(config_dir)
    settings = load_settings(config_dir, problems)
    folder = os.path.join(config_dir, "skills")
    specs, seen = [], {}
    names = sorted(n for n in os.listdir(folder)) if os.path.isdir(folder) else []
    for entry in names:
        if not entry.endswith(".skill.json") or entry.startswith("_"):
            continue
        where = f"skills/{entry}"
        try:
            with open(os.path.join(folder, entry), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            problems.append(Problem("error", where, f"cannot read: {error}"))
            continue
        if not isinstance(data, dict):
            problems.append(Problem("error", where, "must be a JSON object"))
            continue
        for key in data:
            if key not in SPEC_KEYS and not key.startswith("_"):
                problems.append(Problem("error", where, f"unknown field '{key}'. Allowed: {', '.join(SPEC_KEYS)}"))
        name = data.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > MAX_NAME:
            problems.append(Problem("error", where, "'name' must be lowercase letters, digits and single hyphens (up to 64)"))
            continue
        if name in seen:
            problems.append(Problem("error", where, f"name '{name}' is also used by {seen[name]}"))
            continue
        seen[name] = where
        spec = SkillSpec(name=name, source_file=os.path.join(folder, entry))
        spec.description = _one_line(data.get("description", ""), where, problems, "description")
        if not spec.description:
            problems.append(Problem("error", where, "'description' is required"))
        triggers = data.get("triggers", [])
        spec.triggers = [t.strip() for t in _text_list(triggers if not isinstance(triggers, str) else triggers.split(","),
                                                       where, problems, "triggers") if t.strip()]
        for trigger in spec.triggers:
            if "," in trigger or "\n" in trigger:
                problems.append(Problem("error", where, f"trigger '{trigger}' must not contain a comma or a line break (Crew splits them on commas)"))
        spec.destination = data.get("destination", "crew")
        if spec.destination not in DESTINATIONS:
            problems.append(Problem("error", where, f"destination must be one of {', '.join(DESTINATIONS)}"))
        elif spec.destination == "project" and not settings.project_skills_dir:
            problems.append(Problem("error", where, 'destination "project" needs "project_skills_dir" in crewctl.json (for example "../.kiro/skills")'))
        spec.title = _one_line(data.get("title", ""), where, problems, "title")
        spec.goal = "\n".join(_text_list(data.get("goal", ""), where, problems, "goal"))
        spec.when_to_use = _text_list(data.get("when_to_use", []), where, problems, "when_to_use")
        spec.steps = _text_list(data.get("steps", []), where, problems, "steps")
        spec.gotchas = _text_list(data.get("gotchas", []), where, problems, "gotchas")
        out = data.get("output", "")
        spec.output = out if isinstance(out, str) else _text_list(out, where, problems, "output")
        spec.always = data.get("always", False)
        if not isinstance(spec.always, bool):
            problems.append(Problem("error", where, "'always' must be true or false"))
            spec.always = False
        spec.repo_scope = _one_line(data.get("repo_scope", ""), where, problems, "repo_scope")
        spec.body_file = data.get("body_file", "") or ""
        if spec.body_file:
            spec.body_file = _safe_source(config_dir, spec.body_file, where, problems, "body_file")
        elif not spec.steps:
            problems.append(Problem("error", where, "needs 'steps' (or 'body_file' with the whole body)"))
        files = data.get("files", {})
        if not isinstance(files, dict):
            problems.append(Problem("error", where, "'files' must map a path in the skill folder to a source file"))
            files = {}
        for rel, item in files.items():
            parts = rel.replace("\\", "/").split("/")
            source, executable = (item, None) if isinstance(item, str) else (
                (item.get("source"), item.get("executable")) if isinstance(item, dict) else (None, None))
            if (not isinstance(source, str) or rel.startswith("/") or ".." in parts or "" in parts
                    or rel == "SKILL.md" or any(p.startswith(".") for p in parts)):
                problems.append(Problem("error", where, f"files: '{rel}' must be a plain relative path (not SKILL.md, not hidden) "
                                                        'mapped to a source path or {"source": path, "executable": true}'))
                continue
            if executable is not None and not isinstance(executable, bool):
                problems.append(Problem("error", where, f"files: '{rel}' executable must be true or false"))
                continue
            path = _safe_source(config_dir, source, where, problems, "files source")
            if executable is None:     # follow the source file's own permission
                executable = os.path.isfile(path) and bool(os.stat(path).st_mode & 0o111)
            spec.files[rel.replace("\\", "/")] = {"source": path, "executable": executable}
        specs.append(spec)
    return settings, specs, problems


# ---------- rendering ----------

def _field(value):
    if re.search(r"(?:^|\s)#|: |^[\"'\[{>|&*!%@`-]", value) or value != value.strip():
        return json.dumps(value, ensure_ascii=False)
    return value


def render_skill(spec, skill_dir):
    lines = ["---", f"name: {spec.name}", f"description: {_field(spec.description)}"]
    if spec.triggers:
        lines.append(f"triggers: {', '.join(spec.triggers)}")
    if spec.always:
        lines.append("always: true")
    if spec.repo_scope:
        lines.append(f"repo_scope: {_field(spec.repo_scope)}")
    lines += ["---", ""]
    if spec.body_file:
        with open(spec.body_file, encoding="utf-8") as handle:
            body = handle.read().strip("\n")
        return _fill(("\n".join(lines) + body + "\n"), skill_dir).encode("utf-8")
    lines += [f"# {spec.title or spec.name}", ""]
    if spec.goal:
        lines += [spec.goal, ""]
    if spec.when_to_use:
        lines += ["## When to use", ""] + [f"- {item}" for item in spec.when_to_use] + [""]
    lines += ["## Steps", ""] + [f"{i}. {item}" for i, item in enumerate(spec.steps, 1)] + [""]
    if spec.output:
        lines += ["## Output", ""]
        lines += [spec.output] if isinstance(spec.output, str) else [f"- {item}" for item in spec.output]
        lines += [""]
    if spec.gotchas:
        lines += ["## Gotchas", ""] + [f"- {item}" for item in spec.gotchas] + [""]
    return _fill("\n".join(lines), skill_dir).encode("utf-8")


def _fill(text, skill_dir):
    """Replace {{skill_dir}} with the folder the skill is written to, so a step can say where its scripts are."""
    return text.replace("{{skill_dir}}", skill_dir)


def rendered_files(spec, skill_dir):
    files = {"SKILL.md": render_skill(spec, skill_dir)}
    for rel, item in spec.files.items():
        with open(item["source"], "rb") as handle:
            files[rel] = handle.read()
    return files


def executable_files(spec):
    return {rel for rel, item in spec.files.items() if item["executable"]}


def lint_rendered(spec, files):
    """Lint the SKILL.md that would be written. Returns a list of Finding."""
    with tempfile.TemporaryDirectory(prefix="crewctl-lint-") as tmp:
        folder = os.path.join(tmp, spec.name)
        for rel, data in files.items():
            target = os.path.join(folder, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(data)
        return lint_file(os.path.join(folder, "SKILL.md"))


# ---------- plan and apply ----------

def dest_dir(settings, spec):
    base = settings.skills_dir if spec.destination == "crew" else settings.project_skills_dir
    return os.path.join(base, spec.name)


def _current_sha(path):
    try:
        return sha256_file(path)
    except OSError:
        return None


def build_skill_plan(settings, specs, state, targets=None):
    changes = []
    wanted = set(targets or [])
    for spec in specs:
        if wanted and spec.name not in wanted:
            continue
        dest = dest_dir(settings, spec)
        files = rendered_files(spec, dest)
        change = SkillChange(spec.name, "create", dest, files=files, executable=executable_files(spec))
        entry = state.get(spec.name)
        if entry is None:
            if os.path.exists(dest):
                change.hard.append(f"{dest} exists and was not created by crewctl (a built-in or hand-made skill); pick another name")
            changes.append(change)
            continue
        if entry.get("dir") != dest:
            change.hard.append(f"destination changed from {entry.get('dir')}; run `crewctl skills destroy --target {spec.name}` first")
            changes.append(change)
            continue
        recorded = entry.get("files", {})
        if not os.path.isdir(dest):
            change.notes.append("the folder was deleted outside crewctl; it will be created again")
            changes.append(change)
            continue
        for rel, sha in recorded.items():
            now = _current_sha(os.path.join(dest, rel))
            if now != sha:
                change.blockers.append(f"{rel} was changed outside crewctl" if now else f"{rel} was removed outside crewctl")
        change.remove = [rel for rel in recorded if rel not in files]
        same = (all(recorded.get(rel) == sha256_bytes(data) for rel, data in files.items()) and not change.remove
                and set(entry.get("executable", [])) == change.executable)
        change.action = "noop" if same and not change.blockers else "update"
        changes.append(change)
    for name, entry in sorted(state.items()):
        if (wanted and name not in wanted) or any(c.name == name for c in changes):
            continue
        change = SkillChange(name, "delete", entry.get("dir", ""), remove=list(entry.get("files", {})))
        for rel, sha in entry.get("files", {}).items():
            now = _current_sha(os.path.join(entry["dir"], rel))
            if now is not None and now != sha:
                change.blockers.append(f"{rel} was changed outside crewctl")
        changes.append(change)
    return changes


def _prune_empty(folder):
    try:
        os.rmdir(folder)
    except OSError:
        pass


def apply_skill_plan(config_dir, settings, specs, changes, force=False, log=print, break_lock=False):
    """Write the skill folders. Returns counts. Raises SkillsBlocked before writing anything if not allowed."""
    problems = []
    for change in changes:
        problems += [f"{change.name}: {m}" for m in change.hard]
        if not force:
            problems += [f"{change.name}: {m} (use --force to overwrite; a backup is kept)" for m in change.blockers]
    by_name = {s.name: s for s in specs}
    for change in changes:
        if change.action in ("create", "update") and not change.hard:
            for item in lint_rendered(by_name[change.name], change.files):
                if item.level == "error":
                    problems.append(f"{change.name}: {item.message}")
    if problems:
        raise SkillsBlocked("cannot apply:\n  " + "\n  ".join(problems))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    done = {"create": 0, "update": 0, "delete": 0}
    with FileLock(skills_lock_path(config_dir), break_lock=break_lock):
        state = load_skills_state(config_dir)
        for change in changes:
            if change.action == "noop":
                continue
            label = lambda rel: f"skill-{change.name}--{rel.replace('/', '__')}"
            if change.action in ("create", "update"):
                crew_dest = by_name[change.name].destination == "crew"
                os.makedirs(change.dest, mode=0o700 if crew_dest else 0o755, exist_ok=True)
                for rel, data in change.files.items():
                    target = os.path.join(change.dest, rel)
                    if os.path.exists(target):
                        backup_file(config_dir, stamp, target, label(rel))
                    if rel in change.executable:
                        mode = 0o700 if crew_dest else 0o755
                    else:
                        mode = 0o600 if crew_dest else 0o644
                    atomic_write(target, data, mode=mode)
                for rel in change.remove:
                    target = os.path.join(change.dest, rel)
                    if os.path.exists(target):
                        backup_file(config_dir, stamp, target, label(rel))
                        os.unlink(target)
                state[change.name] = {"dir": change.dest, "applied_at": now_iso(),
                                      "files": {rel: sha256_bytes(data) for rel, data in change.files.items()},
                                      "executable": sorted(change.executable)}
                log(f"  applied {change.action}: {change.name} -> {change.dest}")
            else:
                for rel in change.remove:
                    target = os.path.join(change.dest, rel)
                    if os.path.exists(target):
                        backup_file(config_dir, stamp, target, label(rel))
                        os.unlink(target)
                for root, dirs, _files in sorted(os.walk(change.dest), reverse=True):
                    _prune_empty(root)
                state.pop(change.name, None)
                log(f"  applied delete: {change.name} ({change.dest})")
            done[change.action] += 1
        save_skills_state(config_dir, state)
    return done


def new_spec_text(template_path, name):
    with open(template_path, encoding="utf-8") as handle:
        text = handle.read()
    return text.replace("replace-with-skill-name", name)
