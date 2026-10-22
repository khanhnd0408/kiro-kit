"""Zero-token watchdog for subagent runs: a Crew *script cron*. No model is called, so a check costs no credits.

Install (on the machine where Crew runs):

    cp crons/watch_runs.py ~/.kiro/crew/crons/
    kirocrew cron add "watch-runs" '{}' --every 300 --script ~/.kiro/crew/crons/watch_runs.py:run --no-persistent-session --minimal-context

Every tick it reads the same one-line-per-run list the orchestrator reads (`spawn_list`), stays silent while runs make
progress, and sends ONE short notification (to the dashboard) only when something needs a person:

    awaiting-approval   a run waits for you to approve it
    failed              a run ended with an error
    not started         a run has run for a while with zero turns: it is queued (memory, capacity), not working
    no progress         a run's turn count did not change for several ticks
    too long            a run has been going longer than the limit

The message of the cron job is optional JSON:  {"never_started_secs": 300, "stall_ticks": 3, "max_minutes": 120, "repeat_minutes": 30}
It does not start, stop or change any run. It notifies; it does not wake the orchestrator's conversation.
"""
import json
import os
import re
import time
from pathlib import Path

DEFAULTS = {"never_started_secs": 300, "stall_ticks": 3, "max_minutes": 120, "repeat_minutes": 30}
LINE = re.compile(r"^(?P<id>[0-9A-Za-z]+)\s+\[(?P<state>running|done|awaiting-approval)\](?P<rest>.*)$")
PROGRESS = re.compile(r"\((?P<elapsed>\d+)s(?:, (?P<turns>\d+) turns)?(?:, (?P<tool>[^)]*))?\)")
ERROR = re.compile(r"error: (?P<error>.*?)(?= \(\d+s|\s{2}|$)")


def parse_runs(text):
    """The runs in a spawn_list reply. Lines that are not run lines (the roster of agents, headings) are ignored."""
    runs = []
    for line in text.splitlines():
        match = LINE.match(line.strip())
        if not match:
            continue
        rest = match.group("rest")
        progress = PROGRESS.search(rest)
        error = ERROR.search(rest)
        runs.append({"id": match.group("id"), "state": match.group("state"),
                     "error": error.group("error").strip() if error else "",
                     "elapsed": int(progress.group("elapsed")) if progress else 0,
                     "turns": int(progress.group("turns") or 0) if progress else 0,
                     "tool": (progress.group("tool") or "") if progress else ""})
    return runs


def analyze(runs, state, now, config=None):
    """Return (alerts, new_state). Pure: the same inputs always give the same answer."""
    config = {**DEFAULTS, **(config or {})}
    seen = state.get("runs", {})
    new = {"runs": {}}
    alerts = []
    repeat = config["repeat_minutes"] * 60

    def alert(run, kind, text, record):
        last = record.get("alerted", {}).get(kind)
        record.setdefault("alerted", {})
        if last is None or now - last >= repeat:
            alerts.append(f"run {run['id']}: {text}")
            record["alerted"][kind] = now

    for run in runs:
        record = {**seen.get(run["id"], {})}
        record["alerted"] = dict(record.get("alerted", {}))
        if run["state"] == "done":
            if run["error"]:
                alert(run, "failed", f"failed - {run['error'][:160]}", record)
            record["finished"] = True
        elif run["state"] == "awaiting-approval":
            alert(run, "approval", "waits for your approval in the dashboard (Approvals)", record)
        else:
            if run["turns"] == 0 and run["elapsed"] >= config["never_started_secs"]:
                alert(run, "not_started", f"has run {run['elapsed']}s with no turn, so it is queued and has not started "
                                          "(memory or capacity). Do not start a replacement.", record)
            if run["turns"] > 0:
                same = record.get("turns") == run["turns"]
                record["same_ticks"] = record.get("same_ticks", 0) + 1 if same else 0
                if record["same_ticks"] >= config["stall_ticks"]:
                    alert(run, "stalled", f"turn count stuck at {run['turns']} for {record['same_ticks']} checks "
                                          f"(last tool: {run['tool'] or 'unknown'})", record)
            if run["elapsed"] >= config["max_minutes"] * 60:
                alert(run, "too_long", f"has been running {run['elapsed'] // 60} minutes", record)
            record["turns"] = run["turns"]
        record["last_seen"] = now
        new["runs"][run["id"]] = record
    # forget runs not listed any more, so the file stays small
    return alerts, new


def _state_path():
    home = os.environ.get("KIROCREW_HOME") or os.path.join(os.path.expanduser("~"), ".kiro", "crew")
    return Path(home) / "crons" / "watch_runs.state.json"


def _load_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass                                    # best effort: without it only the 'no progress' check is weaker


def run(ctx):
    try:
        from kiro_crew.cron_script import Skip
    except ImportError:                          # only when run outside Crew (tests)
        class Skip(Exception):
            pass
    try:
        config = json.loads(ctx.message) if ctx.message.strip() else {}
        config = config if isinstance(config, dict) else {}
    except ValueError:
        config = {}
    text = ctx.call_tool("kirocrew-core", "spawn_list", {})
    runs = parse_runs(text)
    path = _state_path()
    alerts, state = analyze(runs, _load_state(path), time.time(), config)
    _save_state(path, state)
    if not alerts:
        raise Skip()
    ctx.notify("Subagent watchdog:\n- " + "\n- ".join(alerts))
