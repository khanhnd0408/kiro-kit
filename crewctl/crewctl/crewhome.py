"""A read-only view of Kiro Crew's own data folder.

crewctl never writes here. It reads only three things from config.json (the agent
records, the workspace names, and nothing else; the file also holds channel tokens,
which this module never touches) and the model list from provider_models.json.
"""

import json
import os


class CrewView:
    def __init__(self, home):
        self.home = home
        self.config_found = False
        self.error = ""
        self.agents = {}
        self.workspaces = {}
        self.model_ids = None


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_view(home):
    view = CrewView(home)
    config_path = os.path.join(home, "config.json")
    try:
        data = _load_json(config_path)
    except FileNotFoundError:
        view.error = f"no Crew config at {config_path}"
    except (OSError, ValueError) as error:
        view.error = f"could not read {config_path}: {error}"
    else:
        if isinstance(data, dict):
            view.config_found = True
            agents = data.get("agents")
            if isinstance(agents, dict):
                view.agents = {k: v for k, v in agents.items() if isinstance(v, dict)}
            workspaces = data.get("workspaces")
            if isinstance(workspaces, dict):
                view.workspaces = {k: v for k, v in workspaces.items() if isinstance(v, dict)}
    try:
        models = _load_json(os.path.join(home, "provider_models.json"))
        ids = set()
        for value in (models.values() if isinstance(models, dict) else []):
            if isinstance(value, list):
                ids.update(x for x in value if isinstance(x, str))
        view.model_ids = ids or None
    except (OSError, ValueError):
        view.model_ids = None
    return view


RECORD_FIELDS = ("kiro_agent", "workspace", "display_name", "description", "triggers",
                 "reasoning_effort", "session_color", "starred")
# The only fields `kirocrew agent create/update` can set. The rest are set in the dashboard.
CLI_FIELDS = ("kiro_agent", "workspace")


def record_differences(desired, record):
    """Return {field: (wanted, actual)} for the record fields that differ."""
    # Crew's own defaults for a field missing from a record
    missing = {"starred": False, "workspace": "default"}
    diffs = {}
    for field in RECORD_FIELDS:
        wanted = desired.get(field)
        actual = record.get(field, missing.get(field, ""))
        if wanted != actual:
            diffs[field] = (wanted, actual)
    return diffs
