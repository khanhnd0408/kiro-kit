"""Load and validate the crew config folder.

Layout of a config folder:

    crewctl.json          settings (plan, folders). Optional.
    _defaults.json        values used when an agent file leaves a field out. Optional.
    *.agent.json          one crewmate per file (any subfolder). Names starting with _ are ignored.
    prompts/*.md          long prompts, referenced by "prompt_file".

The rules below come from Kiro Crew's own source (member names, agent names, reserved
ids, effort levels), so a config that passes `validate` is one Crew accepts.
"""

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field

from . import models as model_catalog

TEMPLATE_NAME_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,61}[A-Za-z0-9]|[A-Za-z0-9])\Z")
KAS_RESERVED = frozenset({"default", "vibe", "spec", "quick-spec", "bug-fix", "plan", "autonomous"})
EFFORT_VALUES = ("", "low", "medium", "high", "xhigh", "max")
CAPABILITIES = frozenset({"fs_read", "fs_write", "shell", "web_fetch", "web_search", "mcp", "subagent", "skill",
                          "power", "context", "diagnostics", "sandbox_network", "all", "builtin", "filesystem"})
EFFECTS = frozenset({"allow", "ask", "deny"})
COLOR_RE = re.compile(r"^#[0-9a-f]{6}\Z")
MEMBER_NAME_MAX = 500
PROMPT_FILE_MAX_BYTES = 200_000

AGENT_KEYS = ("name", "kiro_agent", "display_name", "description", "triggers", "model", "paid_model", "reasoning_effort",
              "prompt", "prompt_file", "tools", "allowed_tools", "mcp_servers", "include_mcp_json", "resources",
              "permissions", "hooks", "skills", "crew_servers", "extra_agent_fields", "workspace", "workspace_dir",
              "session_color", "starred")
# Keys written by crewctl itself; extra_agent_fields may not override them.
MANAGED_KIRO_KEYS = frozenset({"name", "description", "model", "prompt", "tools", "allowedTools", "mcpServers",
                               "includeMcpJson", "resources", "permissions", "hooks"})
# Crew's always-on MCP servers. Their command is the Crew install's own, so crewctl looks it up on the
# machine it runs on (in <agents folder>/kirocrew.json) instead of storing it in your config.
CREW_SERVERS = {"core": "kirocrew-core", "cron": "kirocrew-cron"}
CREW_SERVER_OPTIONS = ("disabledTools", "tools")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}\Z")
SETTINGS_KEYS = ("plan", "agents_dir", "crew_home", "kirocrew_bin", "allow_unknown_models", "skills_dir", "project_skills_dir")

SECRET_PATTERNS = [
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{20,}"),
]


@dataclass
class Problem:
    level: str      # error | warn | info
    where: str
    message: str

    def __str__(self):
        return f"{self.level}: {self.where}: {self.message}"


@dataclass
class Settings:
    plan: str = "free"
    agents_dir: str = ""
    crew_home: str = ""
    kirocrew_bin: str = "kirocrew"
    allow_unknown_models: bool = False
    skills_dir: str = ""            # where destination "crew" skills go; default <crew_home>/skills
    project_skills_dir: str = ""    # where destination "project" skills go; relative paths start at the config folder


@dataclass
class AgentSpec:
    name: str
    kiro_agent: str
    source_file: str
    display_name: str = ""
    description: str = ""
    triggers: str = ""
    model: str = ""              # the model used on this machine: paid_model if plan is paid and it is set, else model
    paid_model: str = ""
    reasoning_effort: str = ""
    prompt: str = ""
    tools: list = field(default_factory=list)
    allowed_tools: list = field(default_factory=list)
    mcp_servers: dict = field(default_factory=dict)
    include_mcp_json: bool = False
    resources: list = field(default_factory=list)
    permissions: dict = field(default_factory=dict)
    hooks: object = field(default_factory=dict)
    extra_agent_fields: dict = field(default_factory=dict)
    workspace: str = "default"
    workspace_dir: str = ""
    session_color: str = ""
    starred: bool = False
    skills: list = field(default_factory=list)
    crew_servers: dict = field(default_factory=dict)


class CrewServerLookup:
    """Finds the command for Crew's own MCP servers in <agents folder>/kirocrew.json (read once)."""

    def __init__(self, agents_dir):
        self.path = os.path.join(agents_dir, "kirocrew.json")
        self._servers = None
        self.error = ""

    def _load(self):
        self._servers = {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            servers = data.get("mcpServers")
            if isinstance(servers, dict):
                self._servers = {k: v for k, v in servers.items() if isinstance(v, dict)}
        except FileNotFoundError:
            self.error = f"{self.path} not found. Crew writes it when its gateway first starts."
        except (OSError, ValueError, AttributeError) as error:
            self.error = f"could not read {self.path}: {error}"

    def entry(self, server_name):
        """Return {"command": ..., "args": [...]} or None. Only these two keys are ever copied."""
        if self._servers is None:
            self._load()
        found = self._servers.get(server_name)
        if not found or not isinstance(found.get("command"), str):
            return None
        out = {"command": found["command"]}
        if isinstance(found.get("args"), list):
            out["args"] = [a for a in found["args"] if isinstance(a, str)]
        return out


def default_agents_dir():
    return os.path.join(os.path.expanduser("~"), ".kiro", "agents")


def default_crew_home():
    return os.environ.get("KIROCREW_HOME") or os.path.join(os.path.expanduser("~"), ".kiro", "crew")


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_settings(config_dir, problems, overrides=None):
    """Read crewctl.json, then apply environment variables and command-line overrides."""
    settings = Settings()
    path = os.path.join(config_dir, "crewctl.json")
    if os.path.isfile(path):
        try:
            data = _read_json(path)
        except ValueError as error:
            problems.append(Problem("error", "crewctl.json", f"not valid JSON: {error}"))
            data = {}
        if not isinstance(data, dict):
            problems.append(Problem("error", "crewctl.json", "must be a JSON object"))
            data = {}
        for key, value in data.items():
            if key.startswith("_") or key == "$schema":
                continue
            if key not in SETTINGS_KEYS:
                problems.append(Problem("error", "crewctl.json", f"unknown setting '{key}'. Allowed: {', '.join(SETTINGS_KEYS)}"))
                continue
            setattr(settings, key, value)
    # Order of precedence: command line, then environment, then crewctl.json. Empty values never win.
    env = {"agents_dir": os.environ.get("CREWCTL_AGENTS_DIR"), "crew_home": os.environ.get("CREWCTL_CREW_HOME"),
           "skills_dir": os.environ.get("CREWCTL_SKILLS_DIR"), "project_skills_dir": os.environ.get("CREWCTL_PROJECT_SKILLS_DIR")}
    merged = {k: v for k, v in env.items() if v}
    merged.update({k: v for k, v in (overrides or {}).items() if v})
    for key, value in merged.items():
        setattr(settings, key, value)
    if settings.plan not in ("free", "paid"):
        problems.append(Problem("error", "crewctl.json", f"plan must be \"free\" or \"paid\", not {settings.plan!r}"))
        settings.plan = "free"
    if not isinstance(settings.allow_unknown_models, bool):
        problems.append(Problem("error", "crewctl.json", "allow_unknown_models must be true or false"))
        settings.allow_unknown_models = False
    settings.agents_dir = os.path.abspath(os.path.expanduser(settings.agents_dir or default_agents_dir()))
    settings.crew_home = os.path.abspath(os.path.expanduser(settings.crew_home or default_crew_home()))
    settings.skills_dir = os.path.abspath(os.path.expanduser(settings.skills_dir or os.path.join(settings.crew_home, "skills")))
    if settings.project_skills_dir:
        settings.project_skills_dir = os.path.abspath(os.path.join(config_dir, os.path.expanduser(settings.project_skills_dir)))
    return settings


def _discover(config_dir):
    found = []
    for folder, subdirs, files in os.walk(config_dir):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith(".") and d != "__pycache__")
        for name in sorted(files):
            if name.endswith(".agent.json") and not name.startswith("_"):
                found.append(os.path.join(folder, name))
    return found


def _is_str_list(value):
    return isinstance(value, list) and all(isinstance(x, str) and x for x in value)


def _validate_member_name(name):
    if not isinstance(name, str) or not name:
        return "name must be a non-empty string"
    if len(name) > MEMBER_NAME_MAX:
        return f"name must be at most {MEMBER_NAME_MAX} characters"
    if name != name.strip():
        return "name must not start or end with whitespace"
    if len(name.splitlines()) != 1 or "\t" in name:
        return "name must not contain line breaks or tabs"
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        return "name must not contain hidden or control characters"
    if not name.strip("."):
        return "name must not consist only of periods"
    if name == "default":
        return "'default' is the crew's built-in first crewmate; pick another name"
    return None


def _find_secrets(value, path=""):
    hits = []
    if isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                hits.append(path or "(value)")
                break
    elif isinstance(value, dict):
        for key, item in value.items():
            hits.extend(_find_secrets(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_find_secrets(item, f"{path}[{index}]"))
    return hits


def _check_mcp(servers, where, problems):
    if not isinstance(servers, dict):
        problems.append(Problem("error", where, "mcp_servers must be an object of server name -> settings"))
        return
    for server, conf in servers.items():
        if not isinstance(conf, dict):
            problems.append(Problem("error", where, f"mcp_servers.{server} must be an object"))
            continue
        has_command, has_url = "command" in conf, "url" in conf
        if has_command == has_url:
            problems.append(Problem("error", where, f"mcp_servers.{server} needs exactly one of \"command\" or \"url\""))
        if has_command and not isinstance(conf["command"], str):
            problems.append(Problem("error", where, f"mcp_servers.{server}.command must be text"))
        if "args" in conf and not isinstance(conf["args"], list):
            problems.append(Problem("error", where, f"mcp_servers.{server}.args must be a list"))
        for key in ("env", "headers"):
            if key in conf and not (isinstance(conf[key], dict) and all(isinstance(v, str) for v in conf[key].values())):
                problems.append(Problem("error", where, f"mcp_servers.{server}.{key} must be an object of text values"))


def _check_permissions(perms, where, problems):
    if not isinstance(perms, dict) or not isinstance(perms.get("rules"), list):
        problems.append(Problem("error", where, "permissions must look like {\"rules\": [ ... ]}"))
        return
    for index, rule in enumerate(perms["rules"]):
        spot = f"permissions.rules[{index}]"
        if not isinstance(rule, dict):
            problems.append(Problem("error", where, f"{spot} must be an object"))
            continue
        if rule.get("capability") not in CAPABILITIES:
            problems.append(Problem("error", where, f"{spot}.capability must be one of: {', '.join(sorted(CAPABILITIES))}"))
        if rule.get("effect") not in EFFECTS:
            problems.append(Problem("error", where, f"{spot}.effect must be allow, ask or deny"))
        for key in ("match", "exclude"):
            if key in rule and not _is_str_list(rule[key]):
                problems.append(Problem("error", where, f"{spot}.{key} must be a list of text"))
    problems.append(Problem("info", where, "permissions blocks need kiro-cli 2.23.0 or newer; older versions reject the agent "
                                           "(seen in KiroCrew issue 10041)"))


def _build_spec(path, config_dir, raw, defaults, settings, local_models, problems, crew_lookup):
    where = os.path.relpath(path, config_dir)
    unknown = [k for k in raw if k not in AGENT_KEYS and not k.startswith("_") and k != "$schema"]
    for key in unknown:
        problems.append(Problem("error", where, f"unknown field '{key}'. Allowed: {', '.join(AGENT_KEYS)}"))
    data = {k: v for k, v in defaults.items() if k not in ("name", "kiro_agent", "prompt_file", "prompt")}
    data.update({k: v for k, v in raw.items() if k in AGENT_KEYS})

    name = data.get("name")
    error = _validate_member_name(name)
    if error:
        problems.append(Problem("error", where, error))
        return None

    kiro_agent = data.get("kiro_agent") or name
    if not isinstance(kiro_agent, str) or not TEMPLATE_NAME_RE.fullmatch(kiro_agent):
        problems.append(Problem("error", where, f"the agent file name '{kiro_agent}' is not valid. Use letters, digits, '.', '_' or '-' "
                                                "(max 63, starting and ending with a letter or digit). Set \"kiro_agent\" "
                                                "to a valid name."))
        return None
    if kiro_agent.lower().startswith("kirocrew"):
        problems.append(Problem("error", where, "agent file names starting with 'kirocrew' belong to Crew itself; pick another"))
    if kiro_agent.lower() in KAS_RESERVED:
        problems.append(Problem("error", where, f"'{kiro_agent}' is reserved by Kiro; pick another agent file name"))

    spec = AgentSpec(name=name, kiro_agent=kiro_agent, source_file=path)

    for key in ("display_name", "description", "triggers"):
        value = data.get(key, "")
        if not isinstance(value, str):
            problems.append(Problem("error", where, f"{key} must be text"))
        else:
            setattr(spec, key, value.strip())
    if not spec.triggers:
        problems.append(Problem("warn", where, "no triggers: Crew's orchestrator will not offer this crewmate for selection "
                                               "(it can still be chosen by hand)"))
    if not spec.description:
        problems.append(Problem("warn", where, "no description"))

    model, paid_model = data.get("model", ""), data.get("paid_model", "")
    if not isinstance(model, str) or not isinstance(paid_model, str):
        problems.append(Problem("error", where, "model and paid_model must be text"))
    else:
        model, paid_model = model.strip(), paid_model.strip()
        spec.paid_model = paid_model
        use_paid = settings.plan == "paid" and bool(paid_model)
        spec.model = paid_model if use_paid else model
        for level, message in model_catalog.check_model(spec.model, settings.plan, local_models, settings.allow_unknown_models):
            problems.append(Problem(level, where, message))
        unused, label = (model, "model") if use_paid else (paid_model, "paid_model")
        if unused and unused != spec.model:
            # Not used on this machine, so only catch typos and retired models; plan and local-list checks don't apply.
            for level, message in model_catalog.check_model(unused, "paid", None, settings.allow_unknown_models):
                if level == "error":
                    problems.append(Problem("error", where, f"{label} (not used on this plan, but it would break later): {message}"))

    effort = data.get("reasoning_effort", "")
    if effort not in EFFORT_VALUES:
        problems.append(Problem("error", where, f"reasoning_effort must be one of: (empty), {', '.join(EFFORT_VALUES[1:])}"))
    else:
        spec.reasoning_effort = effort

    # The agent's own prompt or prompt_file wins; the defaults file only fills a gap.
    if "prompt" in raw or "prompt_file" in raw:
        prompt, prompt_file = raw.get("prompt"), raw.get("prompt_file")
    else:
        prompt, prompt_file = defaults.get("prompt"), defaults.get("prompt_file")
    prompt_error = None
    if bool(prompt) == bool(prompt_file):
        prompt_error = "set exactly one of \"prompt\" or \"prompt_file\""
    elif prompt:
        if isinstance(prompt, str):
            spec.prompt = prompt
        else:
            prompt_error = "prompt must be text"
    else:
        base = os.path.dirname(path) if "prompt_file" in raw else config_dir
        target = os.path.realpath(os.path.join(base, str(prompt_file)))
        root = os.path.realpath(config_dir)
        if os.path.isabs(str(prompt_file)) or not (target == root or target.startswith(root + os.sep)):
            prompt_error = "prompt_file must be a relative path inside the config folder"
        elif not os.path.isfile(target):
            prompt_error = f"prompt_file not found: {prompt_file}"
        elif os.path.getsize(target) > PROMPT_FILE_MAX_BYTES:
            prompt_error = f"prompt_file is larger than {PROMPT_FILE_MAX_BYTES} bytes"
        else:
            try:
                with open(target, encoding="utf-8") as handle:
                    spec.prompt = handle.read()
            except (OSError, UnicodeDecodeError) as error:
                prompt_error = f"cannot read prompt_file: {error}"
    if prompt_error is None and not spec.prompt.strip():
        prompt_error = "the prompt is empty"
    if prompt_error:
        problems.append(Problem("error", where, prompt_error))

    if "tools" not in data:
        problems.append(Problem("error", where, "\"tools\" is required. List the tools this crewmate may use, or [] for none "
                                                "(there is deliberately no default)."))
    elif not _is_str_list(data["tools"]):
        problems.append(Problem("error", where, "tools must be a list of text"))
    else:
        spec.tools = list(data["tools"])
        if "*" in spec.tools:
            problems.append(Problem("warn", where, "tools contains \"*\": this crewmate can use every tool, including write and shell"))
    if "allowed_tools" in data:
        if _is_str_list(data["allowed_tools"]):
            spec.allowed_tools = list(data["allowed_tools"])
        else:
            problems.append(Problem("error", where, "allowed_tools must be a list of text"))

    if "mcp_servers" in data:
        _check_mcp(data["mcp_servers"], where, problems)
        if isinstance(data["mcp_servers"], dict):
            spec.mcp_servers = data["mcp_servers"]
    if "include_mcp_json" in data:
        if isinstance(data["include_mcp_json"], bool):
            spec.include_mcp_json = data["include_mcp_json"]
        else:
            problems.append(Problem("error", where, "include_mcp_json must be true or false"))
    if "resources" in data:
        resources = data["resources"]
        if isinstance(resources, list) and all(
                (isinstance(r, str) and r.startswith(("file://", "skill://")))
                or (isinstance(r, dict) and r.get("type") == "knowledgeBase") for r in resources):
            spec.resources = list(resources)
        else:
            problems.append(Problem("error", where, "resources must be a list of \"file://...\" / \"skill://...\" strings or "
                                                    "{\"type\": \"knowledgeBase\", ...} objects"))
    if "skills" in data:
        skills = data["skills"]
        if _is_str_list(skills):
            spec.skills = list(skills)
            for item in skills:
                uri = item if item.startswith("skill://") else "skill://" + item
                if uri not in spec.resources:
                    spec.resources.append(uri)
        else:
            problems.append(Problem("error", where, "skills must be a list of paths or skill:// URIs, "
                                                    "for example [\".kiro/skills/log-triage/SKILL.md\"]"))
    wanted = data.get("crew_servers")
    if wanted:
        _add_crew_servers(spec, wanted, where, problems, crew_lookup)
    if data.get("permissions") not in (None, {}):          # {} means "not set"
        _check_permissions(data["permissions"], where, problems)
        if isinstance(data["permissions"], dict):
            spec.permissions = data["permissions"]
    if data.get("hooks") not in (None, {}, []):
        hooks = data["hooks"]
        # kiro-cli 2.x: an object keyed by trigger ({"preToolUse": [...]}). V3: a list of {name, trigger, matcher, action}.
        if isinstance(hooks, dict) or (isinstance(hooks, list) and all(isinstance(h, dict) for h in hooks)):
            spec.hooks = hooks
        else:
            problems.append(Problem("error", where, "hooks must be an object (kiro-cli 2.x style) or a list of hook objects "
                                                    "(V3 style). Use the same style as ~/.kiro/agents/kirocrew.json on that machine."))
    if "extra_agent_fields" in data:
        extra = data["extra_agent_fields"]
        if not isinstance(extra, dict):
            problems.append(Problem("error", where, "extra_agent_fields must be an object"))
        else:
            clash = sorted(set(extra) & MANAGED_KIRO_KEYS)
            if clash:
                problems.append(Problem("error", where, f"extra_agent_fields may not set {', '.join(clash)}; use the normal fields"))
            else:
                spec.extra_agent_fields = extra

    workspace = data.get("workspace", "default")
    if isinstance(workspace, str) and workspace.strip():
        spec.workspace = workspace.strip()
    else:
        problems.append(Problem("error", where, "workspace must be non-empty text"))
    workspace_dir = data.get("workspace_dir", "")
    if workspace_dir:
        if (not isinstance(workspace_dir, str) or os.path.isabs(workspace_dir) or ".." in workspace_dir.split("/")
                or "\\" in workspace_dir):
            problems.append(Problem("error", where, "workspace_dir must be a plain folder name inside Crew's data folder "
                                                    "(no '..', no absolute path)"))
        else:
            spec.workspace_dir = workspace_dir
    color = data.get("session_color", "")
    if color and not (isinstance(color, str) and COLOR_RE.fullmatch(color)):
        problems.append(Problem("error", where, "session_color must be lowercase #rrggbb or empty"))
    else:
        spec.session_color = color
    if "starred" in data:
        if isinstance(data["starred"], bool):
            spec.starred = data["starred"]
        else:
            problems.append(Problem("error", where, "starred must be true or false"))

    scanned = {k: v for k, v in data.items() if k not in ("prompt", "prompt_file")}
    for spot in _find_secrets(scanned) + _find_secrets(spec.prompt, "prompt"):
        problems.append(Problem("error", where, f"{spot} looks like a real credential. Keep secrets out of config files "
                                                "(they get committed); pass them through the environment instead."))
    return spec


def _add_crew_servers(spec, wanted, where, problems, lookup):
    """Add Crew's own MCP servers (core, cron) to this agent: the server entry and its @tool."""
    if isinstance(wanted, list) and all(isinstance(n, str) for n in wanted):
        options = {n: {} for n in wanted}
    elif isinstance(wanted, dict) and all(isinstance(v, dict) for v in wanted.values()):
        options = wanted
    else:
        problems.append(Problem("error", where, "crew_servers must be a list like [\"core\"] or an object like "
                                                "{\"core\": {\"disabledTools\": [\"task_run\"]}}"))
        return
    for short, opts in options.items():
        server = CREW_SERVERS.get(short)
        if server is None:
            problems.append(Problem("error", where, f"crew_servers: unknown server '{short}'. Allowed: {', '.join(CREW_SERVERS)}"))
            continue
        bad = sorted(set(opts) - set(CREW_SERVER_OPTIONS))
        if bad or any(k in opts and not _is_str_list(opts[k]) for k in CREW_SERVER_OPTIONS):
            problems.append(Problem("error", where, f"crew_servers.{short}: only \"tools\" and \"disabledTools\" (lists of tool names) "
                                                    "are allowed. Crew forbids auto-approving its own tools."))
            continue
        if any(not TOOL_NAME_RE.match(n) for n in opts.get("tools", [])):
            problems.append(Problem("error", where, f"crew_servers.{short}.tools must be plain tool names like \"spawn_run\""))
            continue
        if server in spec.mcp_servers:
            problems.append(Problem("error", where, f"mcp_servers already defines '{server}'; remove it and use crew_servers"))
            continue
        entry = lookup.entry(server)
        if entry is None:
            problems.append(Problem("error", where, f"crew_servers: cannot find '{server}' on this machine. "
                                                    f"{lookup.error or 'Its entry is missing from kirocrew.json.'} Run crewctl on the "
                                                    "machine where Crew is installed and has started once."))
            continue
        if opts.get("disabledTools"):
            entry["disabledTools"] = list(opts["disabledTools"])
        spec.mcp_servers = {**spec.mcp_servers, server: entry}
        spec.crew_servers[short] = opts
        # "tools" lists exactly which of the server's tools this agent gets (each one costs context on every turn).
        # Without it, the agent gets the whole server.
        wanted_tools = [f"@{server}/{n}" for n in opts["tools"]] if opts.get("tools") else [f"@{server}"]
        if "*" not in spec.tools:
            spec.tools = [*spec.tools, *[w for w in wanted_tools if w not in spec.tools]]


def load_config(config_dir, overrides=None, local_models=None):
    """Load a config folder. Return (settings, specs, problems)."""
    problems = []
    config_dir = os.path.abspath(config_dir)
    if not os.path.isdir(config_dir):
        problems.append(Problem("error", config_dir, "config folder not found. Create it with `crewctl init`."))
        return Settings(agents_dir=default_agents_dir(), crew_home=default_crew_home()), [], problems
    settings = load_settings(config_dir, problems, overrides)
    if callable(local_models):
        local_models = local_models(settings)

    defaults = {}
    defaults_path = os.path.join(config_dir, "_defaults.json")
    if os.path.isfile(defaults_path):
        try:
            defaults = _read_json(defaults_path)
            if not isinstance(defaults, dict):
                raise ValueError("must be a JSON object")
        except ValueError as error:
            problems.append(Problem("error", "_defaults.json", f"not usable: {error}"))
            defaults = {}
        defaults = {k: v for k, v in defaults.items() if not k.startswith("_") and k != "$schema"}
        for key in list(defaults):
            if key not in AGENT_KEYS:
                problems.append(Problem("error", "_defaults.json", f"unknown field '{key}'"))
                defaults.pop(key)

    crew_lookup = CrewServerLookup(settings.agents_dir)
    specs, seen_names, seen_files = [], {}, {}
    for path in _discover(config_dir):
        where = os.path.relpath(path, config_dir)
        try:
            raw = _read_json(path)
        except ValueError as error:
            problems.append(Problem("error", where, f"not valid JSON: {error}"))
            continue
        if not isinstance(raw, dict):
            problems.append(Problem("error", where, "must be a JSON object"))
            continue
        spec = _build_spec(path, config_dir, raw, defaults, settings, local_models, problems, crew_lookup)
        if spec is None:
            continue
        if spec.name in seen_names:
            problems.append(Problem("error", where, f"name '{spec.name}' is also used in {seen_names[spec.name]}"))
            continue
        key = spec.kiro_agent.lower()
        if key in seen_files:
            problems.append(Problem("error", where, f"agent file name '{spec.kiro_agent}' is also used by '{seen_files[key]}'"))
            continue
        seen_names[spec.name] = where
        seen_files[key] = spec.name
        specs.append(spec)
    if not specs and not any(p.level == "error" for p in problems):
        problems.append(Problem("warn", config_dir, "no *.agent.json files found"))
    specs.sort(key=lambda s: s.name)
    return settings, specs, problems
