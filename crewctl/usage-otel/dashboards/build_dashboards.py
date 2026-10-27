#!/usr/bin/env python3
"""Build the Grafana dashboard for `crewctl usage`: kiro-usage.json, for VictoriaMetrics and VictoriaLogs.

    python3 build_dashboards.py          writes kiro-usage.json next to this script

The metric panels use PromQL (VictoriaMetrics answers it through Grafana's Prometheus data source). The session and
subagent panels read the log records with LogsQL, through the VictoriaLogs plugin (victoriametrics-logs-datasource).
Edit this script, not the JSON, and run it again.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = {"type": "prometheus", "uid": "${metrics}"}
# Every panel filters on the same variables, so metric and log panels always describe the same data. service.name
# tells apart senders that set their own (resource_attributes); several machines can share one.
FILTER = 'service.name=~"$service", agent=~"$agent", workspace=~"$workspace", model=~"$model"'


def prom(expr, legend="", instant=False, table=False, ref="A"):
    target = {"refId": ref, "datasource": METRICS, "expr": expr, "legendFormat": legend, "range": not instant, "instant": instant}
    if table:
        target["format"] = "table"
    return target


def stat(title, target, unit="none", decimals=2, x=0, y=0, w=4, h=4, thresholds=None, description=""):
    steps = thresholds or [{"color": "blue", "value": None}]
    return {"type": "stat", "title": title, "description": description, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": target["datasource"], "targets": [target],
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": {"mode": "absolute", "steps": steps}},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value",
                        "graphMode": "none", "textMode": "auto"}}


def timeseries(title, targets, unit="none", x=0, y=0, w=12, h=8, stack=True, bars=False, description="", calcs=("sum",)):
    custom = {"drawStyle": "bars" if bars else "line", "fillOpacity": 60 if bars else 0, "lineWidth": 1,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "showPoints": "never" if bars else "always", "pointSize": 5, "spanNulls": True}
    return {"type": "timeseries", "title": title, "description": description, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": targets[0]["datasource"], "targets": targets,
            "fieldConfig": {"defaults": {"unit": unit, "decimals": 2, "custom": custom}, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": list(calcs)}, "tooltip": {"mode": "multi"}}}


def bargauge(title, target, x=0, y=0, w=8, h=8, description=""):
    return {"type": "bargauge", "title": title, "description": description, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": target["datasource"], "targets": [target],
            "fieldConfig": {"defaults": {"unit": "none", "decimals": 2, "min": 0}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


def table(title, target, x=0, y=0, w=12, h=9, rename=None, hide=("Time",), sort="credits", description="", frames=False):
    """frames=True for a source that returns one frame per group (VictoriaLogs stats): its labels become columns and
    the frames are merged into one table."""
    transformations = [{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}}] if frames else []
    if frames:
        hide = tuple(hide) + ("__name__",)
    transformations.append({"id": "organize", "options": {"excludeByName": {name: True for name in hide},
                                                          "renameByName": rename or {}}})
    return {"type": "table", "title": title, "description": description, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": target["datasource"], "targets": [target], "transformations": transformations,
            "fieldConfig": {"defaults": {"decimals": 3}, "overrides": []},
            "options": {"showHeader": True, "sortBy": [{"displayName": sort, "desc": True}]}}


def row(title, y):
    return {"type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def spent(metric, by="", window="$__range", extra=""):
    """Exact increase of a counter over the window: max - min of each series, then summed.

    Prometheus' increase() extrapolates when samples are sparse (one push every few minutes) and can overstate a total
    by a quarter. max - min is exact on Prometheus, Mimir and VictoriaMetrics alike; the 0 point crewctl sends before a
    new series makes it right for a series that starts inside the window too.
    """
    selector = f"{metric}{{{extra}{FILTER}}}"
    grouping = f" by ({by})" if by else ""
    return f"sum{grouping} (max_over_time({selector}[{window}]) - min_over_time({selector}[{window}]))"


def named(expr, *labels):
    """Give series without these labels the value "(none)" (background turns have no agent or workspace)."""
    for label in labels:
        expr = f'label_replace({expr}, "{label}", "(none)", "{label}", "")'
    return expr


def metric_panels():
    total = spent("kiro_credits_total")
    subagent = spent("kiro_credits_total", extra='kind="subagent", ')
    background = spent("kiro_credits_total", extra='kind="background", ')
    panels = [
        row("Credits (from the kiro_*_total counters)", 0),
        stat("Credits in range", prom(total, instant=True), x=0, y=1,
             description="Every billed turn: chat, subagents, background and automation."),
        stat("Billed turns", prom(spent("kiro_turns_total"), instant=True), decimals=0, x=4, y=1),
        stat("Subagent share", prom(f"({subagent} or vector(0)) / {total}",
                                    instant=True), unit="percentunit", x=8, y=1),
        stat("Background share", prom(f"({background} or vector(0)) / {total}",
                                      instant=True), unit="percentunit", x=12, y=1,
             description="Crew's own background jobs (tips, suggestions, titles, memory). Not caused by a chat."),
        stat("Burn rate (credits in the last hour)", prom(spent("kiro_credits_total", window="1h"), instant=True), x=16, y=1),
        stat("Last push", prom('time() - max(max_over_time(kiro_usage_last_push_timestamp_seconds{service.name=~"$service"}[1d]))', instant=True), unit="s", decimals=0, x=20, y=1,
             thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 900}, {"color": "red", "value": 3600}],
             description="Seconds since `crewctl usage push` last ran. Red: the scheduler stopped or cannot reach this backend."),
        *trend_panels(),
        bargauge("Credits by agent", prom(f"sort_desc({named(spent('kiro_credits_total', 'agent'), 'agent')})", "{{agent}}", instant=True), x=0, y=13),
        bargauge("Credits by model", prom(f"sort_desc({named(spent('kiro_credits_total', 'model'), 'model')})", "{{model}}", instant=True), x=8, y=13),
        bargauge("Credits by workspace", prom(f"sort_desc({named(spent('kiro_credits_total', 'workspace'), 'workspace')})", "{{workspace}}", instant=True), x=16, y=13),
        table("Credits by surface", prom(f"sort_desc({spent('kiro_credits_total', 'kind, surface')})", instant=True, table=True),
              x=0, y=21, w=12, h=8, rename={"Value": "credits"}),
        table("Average credits per turn, by agent and model",
              prom(named(f"{spent('kiro_credits_total', 'agent, model')} / {spent('kiro_turns_total', 'agent, model')}", "agent", "model"),
                   instant=True, table=True),
              x=12, y=21, w=12, h=8, rename={"Value": "credits"}),
    ]
    return panels


LOGS = {"type": "victoriametrics-logs-datasource", "uid": "${logs}"}
LOG_BASE = '{service.name=~"$service"}'
LOG_FILTER = 'agent:~"$agent" workspace:~"$workspace" model:~"$model"'


def none_as(*fields):
    """LogsQL that names an empty field "(none)", so a legend or table never shows a blank (background turns)."""
    return "".join(f' | format if ({f}:"") "(none)" as {f}' for f in fields)


def trend_panels():
    """Credits over time from the log records, so each turn sits at the time it happened (a counter only moves when
    a push happens, which would pile a backfill into one bar)."""
    def q(by):
        return {"refId": "A", "datasource": LOGS, "queryType": "statsRange", "legendFormat": f"{{{{{by}}}}}",
                "expr": f'{LOG_BASE} event:=turn {LOG_FILTER}{none_as(by)} | stats by ({by}) sum(credits) as credits'}
    note = "From the log records: each turn at the time it finished. "
    return [timeseries("Credits over time, by kind", [q("kind")], x=0, y=5, w=12, h=8, bars=True,
                       description=note + "chat = you and messaging channels; subagent; background; automation = cron, heartbeat, workflow."),
            timeseries("Credits over time, by agent", [q("agent")], x=12, y=5, w=12, h=8, bars=True, description=note)]


def victoria_panels(y):
    logs, base, sel = LOGS, LOG_BASE, LOG_FILTER

    def q(expr, kind="stats", legend="", ref="A"):
        return {"refId": ref, "datasource": logs, "expr": expr, "queryType": kind, "legendFormat": legend}

    return [
        row("Sessions and subagents (from the log records)", y),
        table("Top sessions, with the subagents they spawned",
              q(f'{base} event:=turn root_session_key:* {sel}{none_as("workspace")} | stats by (root_session_key, root_session_id, workspace) sum(credits) as credits '
                f'| sort by (credits desc) | limit 20'),
              x=0, y=y + 1, w=14, h=9, frames=True, rename={"Value": "credits"},
              description="root_session_key is the chat a turn belongs to; a subagent's spend counts toward the chat that spawned it."),
        table("Subagent spend per orchestrator session",
              q(f'{base} event:=turn kind:=subagent {sel}{none_as("agent")} | stats by (root_session_key, agent) sum(credits) as credits '
                f'| sort by (credits desc) | limit 30'), x=14, y=y + 1, w=10, h=9, frames=True, rename={"Value": "credits"}),
        table("Subagent runs by outcome", q(f'{base} event:=subagent_run {sel}{none_as("agent")} | stats by (agent, outcome) count() as runs'),
              x=0, y=y + 10, w=8, h=8, sort="runs", frames=True, rename={"Value": "runs"}),
        timeseries("Context used (max %, by agent)",
                   [q(f'{base} event:=turn {sel}{none_as("agent")} | stats by (agent) max(context_pct) as context_pct', kind="statsRange", legend="{{agent}}")],
                   unit="percent", x=8, y=y + 10, w=16, h=8, stack=False, calcs=("max",),
                   description="How full each agent's context window was. Near 100% means compaction and expensive turns."),
        {"type": "logs", "title": "Latest turns and subagent runs", "gridPos": {"x": 0, "y": y + 18, "w": 24, "h": 10}, "datasource": logs,
         "targets": [q(f'{base} {sel} | sort by (_time desc)', kind="instant")],
         "options": {"showTime": True, "wrapLogMessage": False, "sortOrder": "Descending", "enableLogDetails": True}},
    ]


def variable(name, label, expr):
    return {"name": name, "label": label, "type": "query", "datasource": METRICS, "query": {"query": expr, "refId": name},
            "definition": expr, "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*",
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "sort": 1}


def dashboard(uid, title, logs_type, log_panels):
    panels = metric_panels()
    panels += log_panels(30)
    for number, panel in enumerate(panels, 1):
        panel["id"] = number
    return {
        "uid": uid, "title": title, "tags": ["kiro", "crewctl", "usage"], "schemaVersion": 39, "version": 1, "editable": True,
        "time": {"from": "now-24h", "to": "now"}, "refresh": "5m", "timezone": "browser", "graphTooltip": 1,
        "description": "Kiro Crew credit usage sent by `crewctl usage push` (kiro-kit). Metrics: kiro_credits_total and friends. "
                       "Logs: one record per billed turn and per subagent run.",
        "templating": {"list": [
            {"name": "metrics", "label": "Metrics", "type": "datasource", "query": "prometheus", "refresh": 1, "hide": 0},
            {"name": "logs", "label": "Logs", "type": "datasource", "query": logs_type, "refresh": 1, "hide": 0},
            variable("service", "Service", "label_values(kiro_credits_total, service.name)"),
            variable("agent", "Agent", "label_values(kiro_credits_total, agent)"),
            variable("model", "Model", "label_values(kiro_credits_total, model)"),
            variable("workspace", "Workspace", "label_values(kiro_credits_total, workspace)"),
        ]},
        "annotations": {"list": []},
        "panels": panels,
    }


def main():
    data = dashboard("kiro-crew-usage", "Kiro Crew usage", "victoriametrics-logs-datasource", victoria_panels)
    with open(os.path.join(HERE, "kiro-usage.json"), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    print("wrote kiro-usage.json")


if __name__ == "__main__":
    main()
