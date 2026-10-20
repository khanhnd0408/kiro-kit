"""Workspaces: check that the ones the crewmates use exist in Crew, and create the missing ones.

A Crew workspace is a name plus a folder inside Crew's data folder (default ~/.kiro/crew). A crewmate that
names a workspace Crew does not know falls back to the default workspace without any error, so crewctl
treats a missing workspace as a mistake and asks you to create it (with `kirocrew workspace create`).
"""

import os
import re

# The same rules Crew applies (kiro_crew.validation.WORKSPACE_NAME_RE).
NAME_RE = re.compile(r"^(?:[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}[a-zA-Z0-9]|[a-zA-Z0-9])\Z")
DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")


def exists(view, name):
    """True when Crew knows this workspace. The default workspace always exists."""
    return name in view.workspaces or name == (view.default_workspace or "default")


def missing_workspaces(view, specs):
    """{workspace name: [crewmate names]} for workspaces the config uses and Crew does not have."""
    missing = {}
    for spec in specs:
        if not exists(view, spec.workspace):
            missing.setdefault(spec.workspace, []).append(spec.name)
    return missing


def default_dir(name, hint=""):
    return hint or f"workspace-{name}"


def check_name(name, view):
    if not NAME_RE.match(name or ""):
        return "use letters, digits, hyphens and underscores (at most 64 characters, starting and ending with a letter or digit)"
    if exists(view, name):
        return f"a workspace named '{name}' already exists"
    return ""


def check_dir(directory, view):
    """The folder name is relative to Crew's data folder. Crew refuses anything that leaves it."""
    if not directory:
        return "the directory name cannot be empty"
    if os.path.isabs(directory) or directory.startswith("~") or "\\" in directory or ".." in directory.split("/"):
        return "give a plain folder name inside Crew's data folder (no absolute path, no '..', no '~')"
    if not DIR_RE.match(directory):
        return "use letters, digits, '.', '_' and '-' (and '/' for a sub-folder)"
    for other, record in view.workspaces.items():
        if str(record.get("dir", "")).strip("/") == directory.strip("/"):
            return f"the folder '{directory}' is already used by workspace '{other}'"
    return ""


def check_copy_from(source, view):
    if source and source not in view.workspaces:
        return f"there is no workspace named '{source}'. Existing: {', '.join(sorted(view.workspaces)) or 'none'}"
    return ""


def create_command(name, directory, copy_from=""):
    """argv for `kirocrew` (without the program name)."""
    argv = ["workspace", "create", "--name", name, "--dir", directory]
    if copy_from:
        argv += ["--copy-from", copy_from]
    return argv
