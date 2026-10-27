"""`crewctl usage`: send Kiro Crew's credit usage to an OpenTelemetry (OTLP) backend.

One run reads what Crew appended since the last run, sends it, and exits. A scheduler (cron, a systemd user timer or
launchd) starts it every few minutes, so nothing stays resident. Nothing under Crew's folder is ever written.

Two sources, both written by Crew itself:
  <crew home>/usage/tokens/<date>.jsonl          One row per billed turn on every surface: chat, subagent, cron,
                                                 background. The ONLY source of credit amounts, so nothing is counted
                                                 twice. The row format is the same in Crew 0.8.0 and 0.10.0.
  <crew home>/crew-log/sessions/<id>/log*.jsonl  One log per session, used for joins only: which session and workspace
                                                 a row belongs to, and which session spawned a subagent (Crew 0.9.0+).
                                                 Each subagent/completed or subagent/failed entry also becomes one
                                                 "subagent_run" log record.

The read position of a file only moves forward after its records were sent, so Crew's own files are the buffer when
the backend is down; there is no queue to grow. Metrics are cumulative counters kept in the local state and sent in
full on every run.

What is sent is an allowlist (SENT_FIELDS, built by build_turn_record and build_subagent_record): never prompt or
response text, file paths, cwd, error text or credentials. Session ids go on log records only, never on metric labels.
"""

import datetime
import gzip
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import __version__, otlp
from .util import FileLock, atomic_write, canonical_json, now_iso

SETTINGS_KEYS = ("endpoint", "logs_endpoint", "metrics_endpoint", "signals", "headers_file", "allow_insecure_http",
                 "resource_attributes", "hash_fields", "drop_fields", "crew_home", "backfill_days",
                 "max_rows_per_request", "max_rows_per_run", "timeout_seconds", "time_limit_seconds")

# Fields a user may drop or hash. Everything else that is sent is a number, a model name or a fixed word.
OPTIONAL_FIELDS = ("agent", "workspace", "app", "session_key", "session_id", "parent_session_key", "parent_session_id",
                   "subagent_id", "stop_reason", "context")
HASHABLE_FIELDS = ("agent", "workspace", "app", "session_key", "session_id", "parent_session_key", "parent_session_id",
                   "subagent_id")
METRIC_LABELS = ("kind", "surface", "model", "provider", "agent", "workspace")

# Every attribute a log record can carry. The tests hold the builders to this list.
SENT_FIELDS = ("event", "kind", "surface", "model", "provider", "agent", "workspace", "app", "stop_reason", "outcome",
               "session_key", "session_id", "parent_session_key", "parent_session_id", "root_session_key", "root_session_id",
               "subagent_id", "credits", "subagent_credits", "cost_usd", "duration_ms", "input_tokens", "output_tokens",
               "context_used", "context_window", "context_pct")

# How a usage row's `surface` is grouped. Anything not listed counts as "chat": the dashboard and messaging channels.
AUTOMATION_SURFACES = {"cron", "heartbeat", "workflow", "monitor", "task", "hook", "webhook"}

SCOPE = ("crewctl.usage", __version__)
STATE_VERSION = 1
READ_CAP_BYTES = 16 * 1024 * 1024        # most bytes read from one file in one run; the rest waits for the next run
SPAWN_KEEP_DAYS = 14
SESSIONS_KEEP = 5000
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class UsageSettings:
    path: str = ""
    exists: bool = False
    endpoint: str = ""
    logs_endpoint: str = ""
    metrics_endpoint: str = ""
    signals: list = field(default_factory=lambda: ["logs", "metrics"])
    headers_file: str = ""
    allow_insecure_http: bool = False
    resource_attributes: dict = field(default_factory=dict)
    hash_fields: list = field(default_factory=list)
    drop_fields: list = field(default_factory=list)
    crew_home: str = ""
    backfill_days: int = 3
    max_rows_per_request: int = 1000
    max_rows_per_run: int = 20000
    timeout_seconds: int = 10
    time_limit_seconds: int = 120

    @property
    def logs_url(self):
        if "logs" not in self.signals:
            return ""
        return self.logs_endpoint or (self.endpoint.rstrip("/") + "/v1/logs" if self.endpoint else "")

    @property
    def metrics_url(self):
        if "metrics" not in self.signals:
            return ""
        return self.metrics_endpoint or (self.endpoint.rstrip("/") + "/v1/metrics" if self.endpoint else "")

    @property
    def enabled(self):
        return bool(self.logs_url or self.metrics_url)


def default_settings_path():
    if os.environ.get("CREWCTL_USAGE_CONFIG"):
        return os.path.abspath(os.path.expanduser(os.environ["CREWCTL_USAGE_CONFIG"]))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "crewctl", "usage.json")


def default_state_dir():
    if os.environ.get("CREWCTL_USAGE_STATE"):
        return os.path.abspath(os.path.expanduser(os.environ["CREWCTL_USAGE_STATE"]))
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "crewctl", "usage")


def _int_setting(raw, key, low, high, errors, default):
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        errors.append(f"{key} must be a whole number from {low} to {high}")
        return default
    return value


def _check_url(url, key, errors):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        errors.append(f"{key} must be an http:// or https:// URL, got {url!r}")
        return None
    return parts


def load_usage_settings(path, crew_home_override=""):
    """Read usage.json. Returns (settings, errors, warnings). A missing file means export is off, not an error."""
    settings = UsageSettings(path=path)
    errors, warnings = [], []
    raw = {}
    if os.path.exists(path):
        settings.exists = True
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as error:
            return settings, [f"cannot read {path}: {error}"], warnings
        if not isinstance(raw, dict):
            return settings, [f"{path} must hold a JSON object"], warnings
    for key in raw:
        if key not in SETTINGS_KEYS and not key.startswith("_"):
            errors.append(f"unknown setting '{key}'. Allowed: {', '.join(SETTINGS_KEYS)}")
    for key in ("endpoint", "logs_endpoint", "metrics_endpoint", "headers_file", "crew_home"):
        value = raw.get(key, "")
        if not isinstance(value, str):
            errors.append(f"{key} must be a string")
            value = ""
        setattr(settings, key, value.strip())
    settings.allow_insecure_http = raw.get("allow_insecure_http", False) is True
    signals = raw.get("signals", ["logs", "metrics"])
    if not isinstance(signals, list) or not signals or any(s not in ("logs", "metrics") for s in signals):
        errors.append('signals must be a list holding "logs", "metrics" or both')
    else:
        settings.signals = list(dict.fromkeys(signals))
    resource = raw.get("resource_attributes", {})
    if not isinstance(resource, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in resource.items()):
        errors.append('resource_attributes must be an object of strings, for example {"deployment.environment": "client-a"}')
    else:
        settings.resource_attributes = dict(resource)
        if len(resource) > 5:
            warnings.append("resource_attributes has more than 5 entries; VictoriaLogs turns every one of them into a stream field")
    for key, allowed in (("hash_fields", HASHABLE_FIELDS), ("drop_fields", OPTIONAL_FIELDS)):
        value = raw.get(key, [])
        if not isinstance(value, list) or any(v not in allowed for v in value):
            errors.append(f"{key} must be a list of: {', '.join(allowed)}")
        else:
            setattr(settings, key, list(dict.fromkeys(value)))
    settings.backfill_days = _int_setting(raw, "backfill_days", 0, 365, errors, settings.backfill_days)
    settings.max_rows_per_request = _int_setting(raw, "max_rows_per_request", 1, 5000, errors, settings.max_rows_per_request)
    settings.max_rows_per_run = _int_setting(raw, "max_rows_per_run", 1, 1000000, errors, settings.max_rows_per_run)
    settings.timeout_seconds = _int_setting(raw, "timeout_seconds", 1, 120, errors, settings.timeout_seconds)
    settings.time_limit_seconds = _int_setting(raw, "time_limit_seconds", 5, 3600, errors, settings.time_limit_seconds)
    from .config import default_crew_home
    settings.crew_home = os.path.abspath(os.path.expanduser(crew_home_override or settings.crew_home or default_crew_home()))
    if settings.headers_file:
        settings.headers_file = os.path.abspath(os.path.expanduser(settings.headers_file))
    for key, url in (("logs endpoint", settings.logs_url), ("metrics endpoint", settings.metrics_url)):
        if not url:
            continue
        parts = _check_url(url, key, errors)
        if parts is None or parts.scheme == "https" or parts.hostname in LOCAL_HOSTS:
            continue
        if settings.headers_file and not settings.allow_insecure_http:
            errors.append(f"the {key} {url} is plain http to another machine, so the headers in headers_file would travel unencrypted. "
                          "Use https, or set \"allow_insecure_http\": true if that network is trusted.")
        elif not settings.allow_insecure_http:
            warnings.append(f"the {key} {url} is plain http to another machine: the usage data travels unencrypted")
    return settings, errors, warnings


def read_headers(path):
    """Header lines ("Name: value") from headers_file. Refuses a file that other users can read."""
    if not path:
        return {}
    if os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise UsageError(f"{path} can be read by other users. Run: chmod 600 {path}")
    headers = {}
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition(":")
            if not sep or not re.match(r"^[A-Za-z0-9-]+$", name.strip()):
                raise UsageError(f"{path} line {number}: expected 'Header-Name: value'")
            headers[name.strip()] = value.strip()
    return headers


class UsageError(RuntimeError):
    pass


# ---- state -------------------------------------------------------------------------------------------------------

def new_state():
    return {"version": STATE_VERSION, "started_ns": time.time_ns(), "offsets": {}, "log_offsets": {}, "sessions": {},
            "spawns": {}, "sent_runs": {}, "totals": {}, "emitted_series": [], "last_run": "", "last_success": "", "last_error": "",
            "unreadable_rows": 0}


def load_state(state_dir):
    path = os.path.join(state_dir, "state.json")
    if not os.path.exists(path):
        return new_state()
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError) as error:
        raise UsageError(f"cannot read {path}: {error}. Move it away to start over; the counters then restart at 0.")
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise UsageError(f"{path} is not a state file this crewctl understands. Move it away to start over.")
    base = new_state()
    base.update(state)
    return base


def save_state(state_dir, state):
    atomic_write(os.path.join(state_dir, "state.json"), (json.dumps(state, indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)


def _salt(state_dir, create=True):
    """The local secret behind hash_fields. Without it a hash cannot be reversed by trying likely names."""
    path = os.path.join(state_dir, "salt")
    if not os.path.exists(path):
        if not create:
            return b"preview-only"
        atomic_write(path, secrets.token_hex(32).encode("ascii"), mode=0o600)
    with open(path, encoding="ascii") as handle:
        return handle.read().strip().encode("ascii")


# ---- reading Crew's files ----------------------------------------------------------------------------------------

def read_new_lines(path, entry):
    """Complete lines appended to `path` since `entry["offset"]`, as [(line bytes, end offset)], plus (inode, size).

    A file that shrank or was replaced is read again from the start. A last line without its newline is not returned:
    Crew may still be writing it.
    """
    st = os.stat(path)
    offset = entry.get("offset", 0)
    if st.st_size < offset or (entry.get("inode") and st.st_ino and entry["inode"] != st.st_ino):
        offset = 0
    lines = []
    if st.st_size > offset:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read(min(st.st_size - offset, READ_CAP_BYTES))
        position = offset
        start = 0
        while True:
            end = data.find(b"\n", start)
            if end < 0:
                break
            position = offset + end + 1
            if end > start:
                lines.append((data[start:end], position))
            else:
                lines.append((b"", position))
            start = end + 1
    return lines, st.st_ino, st.st_size, offset


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def _number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return default
    return value


def parse_usage_row(line):
    """One usage row as a dict of clean values, "ignore" for a row of another type, or None when it cannot be read."""
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("_type") != "tokens":
        return "ignore" if isinstance(raw.get("_type"), str) else None
    moment = _parse_ts(raw.get("ts"))
    credits = raw.get("credits")
    if moment is None or isinstance(credits, bool) or not isinstance(credits, (int, float)) or not math.isfinite(credits):
        return None

    def text(key):
        value = raw.get(key, "")
        return value if isinstance(value, str) else ""

    return {
        "time_ns": int(moment.timestamp() * 1_000_000_000),
        "slot": text("slot"), "model": text("model"), "provider": text("provider"), "surface": text("surface"),
        "agent": text("agent"), "app": text("app"), "stop_reason": text("stop_reason"),
        "credits": float(max(credits, 0.0)),
        "cost_usd": float(_number(raw.get("cost"))),
        "input_tokens": int(_number(raw.get("input"), 0)),
        "output_tokens": int(_number(raw.get("output"), 0)),
        "duration_ms": int(_number(raw.get("duration_ms"), 0)),
        "context_used": int(_number(raw.get("context_used"), 0)),
        "context_window": int(_number(raw.get("context_window"), 0)),
    }


def kind_of(surface, slot):
    if surface == "subagent" or slot.startswith("subagent:"):
        return "subagent"
    if surface.startswith("bg:") or slot == "_bg":
        return "background"
    if surface in AUTOMATION_SURFACES:
        return "automation"
    return "chat"


def _segments(folder):
    """log.jsonl first, then log.<first seq>.jsonl in seq order (Crew's segment naming)."""
    names = []
    for name in os.listdir(folder):
        if name == "log.jsonl":
            names.append((0, name))
        else:
            match = re.fullmatch(r"log\.([0-9]+)\.jsonl", name)
            if match:
                names.append((int(match.group(1)), name))
    return [os.path.join(folder, name) for _, name in sorted(names)]


def scan_crew_log(crew_home, state, max_events):
    """Read new crew-log entries: update the joins in `state` and return the subagent_run events and the new offsets.

    The joins are idempotent, so reading the same lines again after a failed send changes nothing. Offsets are returned,
    not stored, so the caller moves them only after the events were sent.
    """
    root = os.path.join(crew_home, "crew-log", "sessions")
    events, offsets = [], {}
    if not os.path.isdir(root):
        return events, offsets
    sessions, spawns = state["sessions"], state["spawns"]
    for folder in sorted(os.listdir(root)):
        path = os.path.join(root, folder)
        if not os.path.isdir(path) or folder == "projections":
            continue
        for segment in _segments(path):
            rel = os.path.relpath(segment, crew_home)
            entry = state["log_offsets"].get(rel, {})
            try:
                lines, inode, size, start = read_new_lines(segment, entry)
            except OSError:
                continue
            if not lines and start == entry.get("offset", 0):
                continue
            end = start
            for line, position in lines:
                if len(events) >= max_events:
                    break
                end = position
                try:
                    item = json.loads(line) if line else None
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                kind = item.get("type")
                data = item.get("data") if isinstance(item.get("data"), dict) else {}
                if kind == "session":
                    record = sessions.setdefault(folder, {})
                    for key, source in (("sid", "id"), ("slot", "slot"), ("agent", "agent")):
                        if isinstance(item.get(source), str):
                            record[key] = item[source]
                    if isinstance(item.get("createdAt"), int):
                        record["created_ms"] = item["createdAt"]
                elif kind == "session/opened":
                    record = sessions.setdefault(folder, {})
                    workspace = data.get("class", {}).get("workspace") if isinstance(data.get("class"), dict) else None
                    if isinstance(workspace, str):
                        record["workspace"] = workspace
                    if isinstance(data.get("slot"), str):
                        record.setdefault("slot", data["slot"])
                    if "created_ms" not in record and isinstance(item.get("time"), int):
                        record["created_ms"] = item["time"]
                elif kind == "subagent/spawned" and isinstance(data.get("agent_id"), str):
                    spawns[data["agent_id"]] = {"parent": folder, "agent": data.get("agent") if isinstance(data.get("agent"), str) else "",
                                                "model": data.get("model") if isinstance(data.get("model"), str) else "",
                                                "time_ms": item.get("time") if isinstance(item.get("time"), int) else 0}
                elif kind in ("subagent/completed", "subagent/failed") and isinstance(data.get("agent_id"), str):
                    outcome = "completed" if kind == "subagent/completed" else data.get("outcome")
                    events.append({
                        "time_ns": int(item.get("time", 0)) * 1_000_000 if isinstance(item.get("time"), int) else time.time_ns(),
                        "parent": folder, "subagent_id": data["agent_id"],
                        "outcome": outcome if outcome in ("completed", "failed", "stopped", "unknown") else "unknown",
                        "duration_ms": int(_number(data.get("ms"), 0)),
                        "credits": float(data["credits"]) if isinstance(data.get("credits"), (int, float)) and not isinstance(data.get("credits"), bool)
                        and math.isfinite(data["credits"]) else None,
                    })
            offsets[rel] = {"offset": end, "inode": inode, "size": size}
    return events, offsets


class Joins:
    """Answers: which session does a usage row belong to, and which session spawned a subagent."""

    def __init__(self, state):
        self.sessions = state["sessions"]
        self.spawns = state["spawns"]
        self.by_slot = {}
        for folder, record in self.sessions.items():
            if record.get("slot"):
                self.by_slot.setdefault(record["slot"], []).append((record.get("created_ms", 0), folder))
        for items in self.by_slot.values():
            items.sort()

    def session(self, slot, time_ms):
        """The newest session of this slot opened before the row (a slot is reused when a chat is reset)."""
        best = None
        for created, folder in self.by_slot.get(slot, []):
            if created <= time_ms + 60_000 or best is None:
                best = folder
        return self.sessions.get(best) if best else None

    def parent_of(self, subagent_id):
        spawn = self.spawns.get(subagent_id)
        if not spawn:
            return None, None
        return spawn, self.sessions.get(spawn.get("parent"))


# ---- building what is sent -----------------------------------------------------------------------------------------

class Privacy:
    """Applies drop_fields and hash_fields to every attribute and label."""

    def __init__(self, settings, salt=b""):
        self.drop = set(settings.drop_fields)
        self.hash = set(settings.hash_fields)
        self.salt = salt

    def value(self, name, text):
        if not text:
            return ""
        if name in self.hash:
            return hmac.new(self.salt, text.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
        return text

    def put(self, attrs, name, text, policy=None):
        """Set attrs[name] unless dropped. `policy` names the field whose drop/hash setting applies (default: name)."""
        policy = policy or name
        if policy in self.drop or not text:
            return
        attrs[name] = self.value(policy, text)


def build_turn_record(row, joins, privacy):
    """(time_ns, body, attributes) of one usage row."""
    slot = row["slot"]
    kind = kind_of(row["surface"], slot)
    attrs = {"event": "turn", "kind": kind}
    for name in ("surface", "model", "provider"):
        if row[name]:
            attrs[name] = row[name]
    workspace = ""
    if kind == "subagent" and slot.startswith("subagent:"):
        subagent_id = slot[len("subagent:"):]
        privacy.put(attrs, "subagent_id", subagent_id)
        spawn, parent = joins.parent_of(subagent_id)
        if parent:
            privacy.put(attrs, "parent_session_key", parent.get("slot", ""))
            privacy.put(attrs, "parent_session_id", parent.get("sid", ""))
            privacy.put(attrs, "root_session_key", parent.get("slot", ""), "session_key")
            privacy.put(attrs, "root_session_id", parent.get("sid", ""), "session_id")
            workspace = parent.get("workspace", "")
    else:
        privacy.put(attrs, "session_key", slot)
        session = joins.session(slot, row["time_ns"] // 1_000_000) if slot else None
        if slot and slot != "_bg":         # _bg is Crew's shared background slot, not a session
            privacy.put(attrs, "root_session_key", slot, "session_key")
        if session:
            privacy.put(attrs, "session_id", session.get("sid", ""))
            privacy.put(attrs, "root_session_id", session.get("sid", ""), "session_id")
            workspace = session.get("workspace", "")
    privacy.put(attrs, "agent", row["agent"])
    privacy.put(attrs, "workspace", workspace)
    privacy.put(attrs, "app", row["app"])
    privacy.put(attrs, "stop_reason", row["stop_reason"])
    attrs["credits"] = row["credits"]
    attrs["duration_ms"] = row["duration_ms"]
    if row["cost_usd"]:
        attrs["cost_usd"] = row["cost_usd"]
    if row["input_tokens"] or row["output_tokens"]:
        attrs["input_tokens"] = row["input_tokens"]
        attrs["output_tokens"] = row["output_tokens"]
    if "context" not in privacy.drop and row["context_window"]:
        attrs["context_used"] = row["context_used"]
        attrs["context_window"] = row["context_window"]
        attrs["context_pct"] = round(100.0 * row["context_used"] / row["context_window"], 2)
    body = f"{attrs.get('agent', '-')} {row['model'] or '-'} {row['credits']:.3f} credits ({kind})"
    return row["time_ns"], body, attrs, workspace


def build_subagent_record(event, joins, privacy):
    spawn = joins.spawns.get(event["subagent_id"], {})
    parent = joins.sessions.get(event["parent"], {})
    attrs = {"event": "subagent_run", "kind": "subagent", "outcome": event["outcome"]}
    privacy.put(attrs, "subagent_id", event["subagent_id"])
    privacy.put(attrs, "agent", spawn.get("agent", ""))
    if spawn.get("model"):
        attrs["model"] = spawn["model"]
    privacy.put(attrs, "parent_session_key", parent.get("slot", ""))
    privacy.put(attrs, "parent_session_id", parent.get("sid", ""))
    privacy.put(attrs, "root_session_key", parent.get("slot", ""), "session_key")
    privacy.put(attrs, "root_session_id", parent.get("sid", ""), "session_id")
    privacy.put(attrs, "workspace", parent.get("workspace", ""))
    attrs["duration_ms"] = event["duration_ms"]
    if event["credits"] is not None:          # absent is not zero: a run Crew did not bill has no credits field
        attrs["subagent_credits"] = event["credits"]
    shown = f"{event['credits']:.3f} credits" if event["credits"] is not None else "credits not reported"
    body = f"subagent {attrs.get('agent', '-')} {event['outcome']}, {shown}"
    return event["time_ns"], body, attrs


def _series_key(labels):
    return canonical_json(labels)


def add_to_totals(totals, row, workspace):
    labels = {"kind": kind_of(row["surface"], row["slot"]), "surface": row["surface"], "model": row["model"],
              "provider": row["provider"], "agent": row["agent"], "workspace": workspace}
    entry = totals.setdefault(_series_key(labels), {"labels": labels, "credits": 0.0, "turns": 0, "cost_usd": 0.0,
                                                    "input_tokens": 0, "output_tokens": 0})
    entry["credits"] += row["credits"]
    entry["turns"] += 1
    entry["cost_usd"] += row["cost_usd"]
    entry["input_tokens"] += row["input_tokens"]
    entry["output_tokens"] += row["output_tokens"]


def build_metrics(state, privacy, now_ns):
    """Cumulative series for every total, with privacy applied. A series sent for the first time also gets a 0 point
    one second earlier, so that increase() in Prometheus or VictoriaMetrics counts its first value."""
    merged = {}
    for entry in state["totals"].values():
        labels = {}
        for name in METRIC_LABELS:
            if name in ("agent", "workspace"):
                privacy.put(labels, name, entry["labels"].get(name, ""))
            elif entry["labels"].get(name):
                labels[name] = entry["labels"][name]
        key = _series_key(labels)
        into = merged.setdefault(key, {"labels": labels, "credits": 0.0, "turns": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0})
        for name in ("credits", "turns", "cost_usd", "input_tokens", "output_tokens"):
            into[name] += entry[name]
    emitted = set(state["emitted_series"])
    start = state["started_ns"]

    def points(name, cast=float, extra=None, only_nonzero=False):
        out = []
        for key, entry in sorted(merged.items()):
            value = cast(entry[name])
            if only_nonzero and not value:
                continue
            labels = dict(entry["labels"], **(extra or {}))
            if key not in emitted:
                out.append(otlp.number_point(labels, start, now_ns - 1_000_000_000, cast(0)))
            out.append(otlp.number_point(labels, start, now_ns, value))
        return out

    metrics = [otlp.sum_metric("kiro_credits_total", "Kiro credits billed, from Crew's usage rows", "", points("credits")),
               otlp.sum_metric("kiro_turns_total", "Billed turns (one per Crew usage row)", "", points("turns", int))]
    if any(e["cost_usd"] for e in merged.values()):
        metrics.append(otlp.sum_metric("kiro_cost_usd_total", "Cost in USD, for providers that bill in dollars", "", points("cost_usd", only_nonzero=True)))
    if any(e["input_tokens"] or e["output_tokens"] for e in merged.values()):
        metrics.append(otlp.sum_metric("kiro_tokens_total", "Tokens, for providers that report them", "",
                                       points("input_tokens", int, {"direction": "input"}, True) + points("output_tokens", int, {"direction": "output"}, True)))
    metrics.append(otlp.gauge_metric("kiro_usage_last_push_timestamp_seconds", "When crewctl usage push last ran", "s",
                                     [otlp.number_point({}, 0, now_ns, now_ns / 1e9)]))
    return metrics, set(merged)


def resource_attributes(settings):
    attrs = {"service.name": "kiro-crew"}
    attrs.update(settings.resource_attributes)
    return attrs


# ---- sending -----------------------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it could carry the auth headers to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


def post(url, payload, headers, timeout):
    """POST one OTLP protobuf payload, gzipped. Returns None on success, or a message (never containing header values)."""
    request = urllib.request.Request(url, data=gzip.compress(payload), method="POST", headers={
        "Content-Type": "application/x-protobuf", "Content-Encoding": "gzip", "User-Agent": f"crewctl/{__version__}", **headers})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(65536)
            return None
    except urllib.error.HTTPError as error:
        detail = error.read(300).decode("utf-8", "replace").strip().replace("\n", " ")
        for value in headers.values():
            if value:
                detail = detail.replace(value, "***")
        return f"HTTP {error.code} from {url}" + (f": {detail}" if detail else "")
    except (urllib.error.URLError, OSError, socket.timeout) as error:
        reason = getattr(error, "reason", error)
        return f"cannot reach {url}: {reason}"


# ---- one run ----------------------------------------------------------------------------------------------------

@dataclass
class RunResult:
    turns: int = 0
    subagent_runs: int = 0
    credits: float = 0.0
    series: int = 0
    unreadable: int = 0
    unreadable_example: str = ""
    skipped_old_files: int = 0
    more_left: bool = False
    stopped: str = ""
    errors: list = field(default_factory=list)
    preview_logs: list = field(default_factory=list)
    preview_metrics: list = field(default_factory=list)


def _usage_files(crew_home):
    folder = os.path.join(crew_home, "usage", "tokens")
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", n))


def read_usage(settings, state, limit, result):
    """New usage rows, oldest file first: [(row, rel path, end offset)], and the file entries to commit after sending."""
    rows, entries = [], {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=settings.backfill_days)).isoformat()
    for path in _usage_files(settings.crew_home):
        rel = os.path.relpath(path, settings.crew_home)
        entry = state["offsets"].get(rel)
        if entry is None and os.path.basename(path)[:10] < cutoff:
            # Older than backfill_days the first time we see it: start at its end. Its rows are never sent.
            size = os.path.getsize(path)
            entries[rel] = {"offset": size, "inode": os.stat(path).st_ino, "size": size}
            result.skipped_old_files += 1
            continue
        if len(rows) >= limit:
            result.more_left = True
            break
        lines, inode, size, start = read_new_lines(path, entry or {})
        if size - start > READ_CAP_BYTES:
            result.more_left = True
        unreadable_here = 0
        taken = start
        for line, position in lines:
            if len(rows) >= limit:
                result.more_left = True
                break
            taken = position
            row = parse_usage_row(line) if line.strip() else "ignore"
            if row == "ignore":
                continue
            if row is None:
                unreadable_here += 1
                result.unreadable_example = result.unreadable_example or line[:120].decode("utf-8", "replace")
                continue
            rows.append((row, rel, position))
        if unreadable_here >= 10 and unreadable_here * 2 > len(lines):
            raise UsageError(f"most rows in {path} are not in the format crewctl knows ({unreadable_here} of {len(lines)}). "
                             f"Crew may have changed it; nothing was sent from this file. Example: {result.unreadable_example}")
        result.unreadable += unreadable_here
        entries[rel] = {"offset": taken, "inode": inode, "size": size}
    return rows, entries


def commit_offsets(state, rows, entries, sent):
    """Move each file's read position past what was sent, and return the indexes of the rows that are now done.

    `sent` holds the indexes (into `rows`, which is in file order) of the rows that went out. A file moves past its
    rows up to the first one that was not sent; a file whose rows all went out (or that had none) moves to the end of
    what was read, which also skips lines that were empty, of another type or unreadable.
    """
    done = []
    by_file = {}
    for index, (_, rel, position) in enumerate(rows):
        by_file.setdefault(rel, []).append((index, position))
    for rel, entry in entries.items():
        upto = None
        complete = True
        for index, position in by_file.get(rel, []):
            if index not in sent:
                complete = False
                break
            done.append(index)
            upto = position
        if complete:
            state["offsets"][rel] = entry
        elif upto is not None:
            state["offsets"][rel] = dict(entry, offset=upto)
    return sorted(done)


def _prune(state):
    cutoff_ms = int((time.time() - SPAWN_KEEP_DAYS * 86400) * 1000)
    state["spawns"] = {k: v for k, v in state["spawns"].items() if v.get("time_ms", 0) >= cutoff_ms}
    state["sent_runs"] = {k: v for k, v in state["sent_runs"].items() if v >= cutoff_ms}
    if len(state["sessions"]) > SESSIONS_KEEP:
        keep = sorted(state["sessions"].items(), key=lambda item: item[1].get("created_ms", 0))[-SESSIONS_KEEP:]
        state["sessions"] = dict(keep)


def run_push(settings, state_dir, dry_run=False, sender=None, clock=time.time_ns, max_rows=None, break_lock=False):
    """One export run. Returns a RunResult. Writes only to `state_dir`, and not at all with dry_run."""
    result = RunResult()
    sender = sender or post
    if not dry_run:
        lock = FileLock(os.path.join(state_dir, "push.lock"), break_lock=break_lock)
        lock.acquire()
    try:
        return _run_push(settings, state_dir, dry_run, sender, clock, max_rows, result)
    finally:
        if not dry_run:
            lock.release()


PREVIEW_RECORDS = 5


def _preview(result, payload):
    """Keep only the first few decoded records of a dry run; the rest are counted, not held."""
    if len(result.preview_logs) < PREVIEW_RECORDS:
        result.preview_logs.extend(otlp.decode_logs(payload)[:PREVIEW_RECORDS - len(result.preview_logs)])


def _run_push(settings, state_dir, dry_run, sender, clock, max_rows, result):
    began = time.monotonic()
    state = load_state(state_dir)
    headers = read_headers(settings.headers_file)
    privacy = Privacy(settings, _salt(state_dir, create=not dry_run) if settings.hash_fields else b"")
    limit = min(max_rows or settings.max_rows_per_run, settings.max_rows_per_run)
    events, log_offsets = scan_crew_log(settings.crew_home, state, max_events=limit)
    rows, usage_entries = read_usage(settings, state, limit, result)
    joins = Joins(state)
    resource = resource_attributes(settings)
    logs_url, metrics_url = settings.logs_url, settings.metrics_url
    batch_size = settings.max_rows_per_request

    def over_time():
        return time.monotonic() - began > settings.time_limit_seconds

    # 1. One time-ordered stream of log records: subagent runs and turns merged. Loki refuses a record that is much
    #    older than the newest one it already holds for the stream, so sending in time order is what lets a first run
    #    (or a catch-up after downtime) land completely.
    sent_runs = state["sent_runs"]
    events = [e for e in events if e["subagent_id"] not in sent_runs]
    items = [(e["time_ns"], 0, i, "run") for i, e in enumerate(events)]
    items += [(row["time_ns"], 1, i, "turn") for i, (row, _, _) in enumerate(rows)]
    items.sort()
    built = {}

    def record(item):
        _, _, index, kind = item
        if kind == "run":
            return build_subagent_record(events[index], joins, privacy)
        key = ("turn", index)
        if key not in built:
            built[key] = build_turn_record(rows[index][0], joins, privacy)
        time_ns, body, attrs, _ = built[key]
        return time_ns, body, attrs

    sent = len(items) if not logs_url else 0
    if logs_url:
        for i in range(0, len(items), batch_size):
            if over_time():
                result.stopped = f"time limit of {settings.time_limit_seconds}s reached"
                result.more_left = True
                break
            chunk = items[i:i + batch_size]
            payload = otlp.logs_request(resource, SCOPE, [otlp.log_record(*record(item)) for item in chunk])
            if dry_run:
                _preview(result, payload)
            else:
                error = sender(logs_url, payload, headers, settings.timeout_seconds)
                if error:
                    result.errors.append(error)
                    result.more_left = True
                    break
            sent = i + len(chunk)

    # 2. Commit what went out. Subagent runs are remembered by id, so one sent before a failure is never sent twice;
    #    the crew-log offsets move once every run found was sent. A usage file moves past the rows sent without a gap
    #    (in file order), and only those rows are added to the counters, so nothing is counted twice.
    sent_turns = set()
    for _, _, index, kind in items[:sent]:
        if kind == "run":
            event = events[index]
            sent_runs[event["subagent_id"]] = event["time_ns"] // 1_000_000
            result.subagent_runs += 1
        else:
            sent_turns.add(index)
    if sent == len(items):
        state["log_offsets"].update(log_offsets)
    committed = commit_offsets(state, rows, usage_entries, sent_turns)
    for index in committed:
        row = rows[index][0]
        workspace = built[("turn", index)][3] if ("turn", index) in built else build_turn_record(row, joins, privacy)[3]
        add_to_totals(state["totals"], row, workspace)
        result.turns += 1
        result.credits += row["credits"]
    if result.unreadable:
        state["unreadable_rows"] = state.get("unreadable_rows", 0) + result.unreadable

    # 3. metrics: every series, cumulative, every run.
    now_ns = clock()
    if metrics_url:
        metrics, keys = build_metrics(state, privacy, now_ns)
        payload = otlp.metrics_request(resource, SCOPE, metrics)
        result.series = len(keys)
        if dry_run:
            result.preview_metrics = otlp.decode_metrics(payload)
        else:
            error = sender(metrics_url, payload, headers, settings.timeout_seconds)
            if error:
                result.errors.append(error)
            else:
                state["emitted_series"] = sorted(set(state["emitted_series"]) | keys)
    if dry_run:
        return result
    _prune(state)
    state["last_run"] = now_iso()
    if result.errors:
        state["last_error"] = f"{now_iso()} {result.errors[-1]}"
    else:
        state["last_success"] = state["last_run"]
        state["last_error"] = ""
    save_state(state_dir, state)
    return result


# ---- status and scheduling -----------------------------------------------------------------------------------------

def pending_bytes(settings, state):
    """Bytes Crew has written that no run has read yet (usage rows and crew log)."""
    total = 0
    for path in _usage_files(settings.crew_home):
        rel = os.path.relpath(path, settings.crew_home)
        entry = state["offsets"].get(rel)
        if entry is None:
            continue
        total += max(0, os.path.getsize(path) - entry.get("offset", 0))
    return total


def launcher_path():
    """The crewctl command a scheduler should run: the one on PATH, else this very program."""
    import shutil
    found = shutil.which("crewctl")
    if found:
        return found
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    return argv0 if os.path.basename(argv0) == "crewctl" else "/full/path/to/kiro-kit/bin/crewctl"


def schedule_text(kind, minutes, settings_path, launcher):
    """The scheduler snippet, as text to print. crewctl never installs it itself."""
    extra = "" if settings_path == default_settings_path() else f" --settings {settings_path}"
    command = f"{launcher} usage push --quiet{extra}"
    state_dir = default_state_dir()
    if kind == "cron":
        return (f"# Add with `crontab -e`. Runs every {minutes} min at low priority; output goes to a log file.\n"
                f"*/{minutes} * * * * nice -n 10 {command} >> {state_dir}/cron.log 2>&1\n")
    if kind == "systemd":
        return (f"# ~/.config/systemd/user/crewctl-usage.service\n"
                f"[Unit]\nDescription=Send Kiro Crew usage to OTLP (crewctl usage push)\n\n"
                f"[Service]\nType=oneshot\nNice=10\nIOSchedulingClass=idle\nTimeoutStartSec=300\n"
                f"ExecStart={command}\n\n"
                f"# ~/.config/systemd/user/crewctl-usage.timer\n"
                f"[Unit]\nDescription=Run crewctl usage push every {minutes} min\n\n"
                f"[Timer]\nOnBootSec=2min\nOnUnitActiveSec={minutes}min\nRandomizedDelaySec=30\n\n"
                f"[Install]\nWantedBy=timers.target\n\n"
                f"# Then:\n#   systemctl --user daemon-reload\n#   systemctl --user enable --now crewctl-usage.timer\n"
                f"#   journalctl --user -u crewctl-usage.service     (the output of each run)\n")
    args = "".join(f"\n    <string>{a}</string>" for a in [launcher, "usage", "push", "--quiet"] + (["--settings", settings_path] if extra else []))
    return (f"<!-- ~/Library/LaunchAgents/local.crewctl.usage.plist -->\n"
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0">\n<dict>\n  <key>Label</key>\n  <string>local.crewctl.usage</string>\n'
            f"  <key>ProgramArguments</key>\n  <array>{args}\n  </array>\n"
            f"  <key>StartInterval</key>\n  <integer>{minutes * 60}</integer>\n"
            f"  <key>Nice</key>\n  <integer>10</integer>\n  <key>LowPriorityIO</key>\n  <true/>\n"
            f"  <key>StandardOutPath</key>\n  <string>{state_dir}/launchd.log</string>\n"
            f"  <key>StandardErrorPath</key>\n  <string>{state_dir}/launchd.log</string>\n</dict>\n</plist>\n"
            f"<!-- Then: mkdir -p {state_dir} && launchctl load ~/Library/LaunchAgents/local.crewctl.usage.plist -->\n")


def default_scheduler():
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux") and os.path.isdir("/run/systemd/system"):
        return "systemd"
    return "cron"
