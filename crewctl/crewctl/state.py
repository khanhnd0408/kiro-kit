"""The state file: what crewctl last applied, so it can tell a config change from outside drift.

    <config folder>/.crewctl/state.json     the state
    <config folder>/.crewctl/lock           held while a command that writes is running
    <config folder>/.crewctl/backups/<time>/ copies of agent files before they are changed or deleted

The state holds file hashes and the registration values last shown to you. It never holds
prompts or secrets. Keep .crewctl/ out of git.
"""

import json
import os
import shutil
import uuid

from .util import atomic_write, now_iso, pretty_json

STATE_VERSION = 1


class StateError(RuntimeError):
    pass


class State:
    def __init__(self):
        self.version = STATE_VERSION
        self.serial = 0
        self.lineage = str(uuid.uuid4())
        self.agents = {}
        # Crewmates whose agent file crewctl removed but whose Crew record may still exist.
        self.removed = {}

    def to_dict(self):
        return {"version": self.version, "serial": self.serial, "lineage": self.lineage, "agents": self.agents,
                "removed": self.removed}


def state_dir(config_dir):
    return os.path.join(config_dir, ".crewctl")


def state_path(config_dir):
    return os.path.join(state_dir(config_dir), "state.json")


def lock_path(config_dir):
    return os.path.join(state_dir(config_dir), "lock")


def load_state(config_dir):
    path = state_path(config_dir)
    state = State()
    if not os.path.isfile(path):
        return state
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        raise StateError(f"cannot read {path}: {error}. Restore it from {state_dir(config_dir)}/backups or remove it "
                         "and use `crewctl import` to re-adopt the agent files.")
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION or not isinstance(data.get("agents"), dict):
        raise StateError(f"{path} is not a version {STATE_VERSION} crewctl state file")
    state.serial = int(data.get("serial", 0))
    state.lineage = str(data.get("lineage") or state.lineage)
    state.agents = data["agents"]
    removed = data.get("removed", {})
    state.removed = removed if isinstance(removed, dict) else {}
    return state


def save_state(config_dir, state):
    atomic_write(state_path(config_dir), pretty_json(state.to_dict()).encode("utf-8"))


def backup_file(config_dir, stamp, source, label):
    """Copy a file into backups/<stamp>/ before it is overwritten or deleted. Return the copy's path."""
    folder = os.path.join(state_dir(config_dir), "backups", stamp)
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, label)
    shutil.copy2(source, target)
    return target


def new_entry(spec_name, kiro_agent, file_sha, config_file, registration):
    return {
        "kiro_agent": kiro_agent,
        "file": f"{kiro_agent}.json",
        "file_sha256": file_sha,
        "config_file": config_file,
        "applied_at": now_iso(),
        "registration": registration,
    }
