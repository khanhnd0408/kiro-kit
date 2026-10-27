"""`crewctl usage export`: one self-contained HTML report of Kiro Crew's credit usage, for people without a monitoring
stack.

It reads the same files as `crewctl usage push` (and as Crew's own Usage tab): the usage rows for the amounts, the crew
log for sessions, workspaces and subagents. Nothing is sent and no state is written. The page has no external links,
scripts or fonts, so it opens offline, prints, and can be attached to an email. Charts are inline SVG built here; hover
a bar for its value, and every chart has a table with the same numbers.

Colors are the validated reference palette of the data-visualization method (categorical slots in fixed order, light
and dark steps chosen separately).
"""

import datetime
import glob
import html
import math
import os
import re
from dataclasses import dataclass, field

from . import __version__, usage

KINDS = ("chat", "subagent", "background", "automation")      # fixed order, so a kind keeps its color in every report
KIND_LABELS = {"chat": "Chat", "subagent": "Subagent", "background": "Background", "automation": "Automation"}
TOP_AGENTS = 6                                               # agents with their own color; the rest fold into "Other"
NO_AGENT = "(no agent)"


@dataclass
class Report:
    crew_home: str
    start: datetime.datetime
    end: datetime.datetime
    turns: list = field(default_factory=list)       # [(time, credits, attrs)]
    runs: list = field(default_factory=list)        # [(time, attrs)]
    unreadable: int = 0
    privacy_note: str = ""

    @property
    def credits(self):
        return sum(c for _, c, _ in self.turns)


# ---- the period ------------------------------------------------------------------------------------------------------

def parse_day(text, today=None):
    """YYYY-MM-DD, `today` or `yesterday`, as a date."""
    today = today or datetime.date.today()
    words = {"today": today, "yesterday": today - datetime.timedelta(days=1)}
    if text in words:
        return words[text]
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise usage.UsageError(f"'{text}' is not a date: use YYYY-MM-DD, today or yesterday")


def parse_span(text):
    match = re.fullmatch(r"([1-9][0-9]*)([hd])", text or "")
    if not match:
        raise usage.UsageError(f"'{text}' is not a period: use a number of hours or days, for example 24h or 7d")
    amount = int(match.group(1))
    return datetime.timedelta(hours=amount) if match.group(2) == "h" else datetime.timedelta(days=amount)


def period(since=None, day_from=None, day_to=None, now=None):
    """(start, end) as local aware datetimes. --from/--to are whole local days, --to inclusive; default: the last 30 days."""
    now = (now or datetime.datetime.now()).astimezone()
    if day_from or day_to:
        if since:
            raise usage.UsageError("use either --since or --from/--to, not both")
        first = parse_day(day_from or day_to, now.date())
        last = parse_day(day_to or day_from, now.date())
        if last < first:
            raise usage.UsageError(f"--to {last} is before --from {first}")
        start = datetime.datetime.combine(first, datetime.time()).astimezone()
        end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time()).astimezone()
        return start, min(end, max(now, start))
    return now - parse_span(since or "30d"), now


# ---- collecting --------------------------------------------------------------------------------------------------

def collect(crew_home, start, end, settings=None, state_dir=None):
    """Read Crew's files for the period. Writes nothing, except the hash key when hash_fields is set and none exists."""
    settings = settings or usage.UsageSettings()
    salt = usage._salt(state_dir or usage.default_state_dir()) if settings.hash_fields else b""
    privacy = usage.Privacy(settings, salt)
    state = usage.new_state()                               # joins only, in memory
    events, _ = usage.scan_crew_log(crew_home, state, max_events=10 ** 9)
    joins = usage.Joins(state)
    report = Report(crew_home=crew_home, start=start, end=end)
    notes = []
    if settings.drop_fields:
        notes.append("not shown: " + ", ".join(settings.drop_fields))
    if settings.hash_fields:
        notes.append("hashed: " + ", ".join(settings.hash_fields))
    report.privacy_note = "; ".join(notes)
    start_ns, end_ns = int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)
    first_file = (start.date() - datetime.timedelta(days=1)).isoformat()
    last_file = (end.date() + datetime.timedelta(days=1)).isoformat()
    for path in sorted(glob.glob(os.path.join(crew_home, "usage", "tokens", "*.jsonl"))):
        name = os.path.basename(path)[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) or not first_file <= name <= last_file:
            continue
        with open(path, "rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = usage.parse_usage_row(line)
                if row == "ignore":
                    continue
                if row is None:
                    report.unreadable += 1
                    continue
                if start_ns <= row["time_ns"] < end_ns:
                    _, _, attrs, _ = usage.build_turn_record(row, joins, privacy)
                    report.turns.append((row["time_ns"], row["credits"], attrs))
    for event in events:
        if start_ns <= event["time_ns"] < end_ns:
            _, _, attrs = usage.build_subagent_record(event, joins, privacy)
            report.runs.append((event["time_ns"], attrs))
    report.turns.sort(key=lambda t: t[0])
    return report


# ---- numbers -----------------------------------------------------------------------------------------------------

def _local(time_ns):
    return datetime.datetime.fromtimestamp(time_ns / 1e9).astimezone()


def buckets(start, end):
    """[(key, label, start)] for the chart: hours up to 2 days, days up to 92, else weeks (from Monday)."""
    span = end - start
    out = []
    if span <= datetime.timedelta(days=2):
        moment = start.replace(minute=0, second=0, microsecond=0)
        while moment < end:
            out.append((moment.strftime("%Y-%m-%d %H"), moment.strftime("%H:00"), moment))
            moment = (moment + datetime.timedelta(hours=1)).astimezone()
        return out, "hour"
    if span <= datetime.timedelta(days=92):
        day = start.date()
        while day <= end.date():
            out.append((day.isoformat(), day.strftime("%b %d"), day))
            day += datetime.timedelta(days=1)
        return out, "day"
    day = start.date() - datetime.timedelta(days=start.weekday())
    while day <= end.date():
        out.append((day.isoformat(), "wk " + day.strftime("%b %d"), day))
        day += datetime.timedelta(days=7)
    return out, "week"


def bucket_key(time_ns, unit):
    moment = _local(time_ns)
    if unit == "hour":
        return moment.strftime("%Y-%m-%d %H")
    if unit == "day":
        return moment.date().isoformat()
    day = moment.date()
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def group(turns, key):
    """{value: [credits, turns]} for one attribute, largest first."""
    out = {}
    for _, credits, attrs in turns:
        entry = out.setdefault(key(attrs), [0.0, 0])
        entry[0] += credits
        entry[1] += 1
    return dict(sorted(out.items(), key=lambda item: -item[1][0]))


def agent_of(attrs):
    return attrs.get("agent") or NO_AGENT


def agent_colors(report):
    """The agents that get their own color (largest spend first) and the rest, which share "Other"."""
    ranked = list(group(report.turns, agent_of))
    return ranked[:TOP_AGENTS], set(ranked[TOP_AGENTS:])


# ---- rendering helpers ---------------------------------------------------------------------------------------------

def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value, digits=2):
    if value >= 10000:
        return f"{value / 1000:,.1f}K"
    return f"{value:,.{digits}f}"


def pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "0.0%"


def nice_ticks(top, count=4):
    """Clean y-axis ticks from 0 to at least `top`."""
    if top <= 0:
        return [0, 1]
    raw = top / count
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    ticks, value = [], 0.0
    while value < top - 1e-9:
        ticks.append(round(value, 10))
        value += step
    ticks.append(round(value, 10))
    return ticks


def tick_label(value):
    return f"{value:g}" if value < 1000 else f"{value / 1000:g}K"


def stacked_bars(title, subtitle, slots, series, legend_extra=""):
    """An SVG stacked column chart. `slots`: [(label, {series: value})]; `series`: [(name, label, css class)].

    Thin columns with a 2px surface gap between segments and a 4px rounded top, one hairline baseline, recessive
    gridlines, a legend above, and a <title> on every segment for its value.
    """
    width, height = 960, 260
    left, right, top, bottom = 48, 12, 12, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    totals = [sum(values.values()) for _, values in slots]
    ticks = nice_ticks(max(totals) if totals else 0)
    y_max = ticks[-1] or 1
    band = plot_w / max(len(slots), 1)
    bar = min(24.0, band * 0.7)
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">']
    for tick in ticks:
        y = top + plot_h - plot_h * tick / y_max
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{tick_label(tick)}</text>')
    every = max(1, math.ceil(len(slots) / 12))
    for index, (label, values) in enumerate(slots):
        x = left + band * index + (band - bar) / 2
        if index % every == 0:
            parts.append(f'<text class="tick" x="{x + bar / 2:.1f}" y="{height - 10}" text-anchor="middle">{esc(label)}</text>')
        y = top + plot_h
        drawn = [(name, cls, values.get(name, 0.0)) for name, _, cls in series if values.get(name, 0.0) > 0]
        for position, (name, cls, value) in enumerate(drawn):
            h = plot_h * value / y_max
            gap = 2 if position < len(drawn) - 1 else 0
            seg_top, seg_h = y - h, max(h - gap, 0.5)
            label_name = next(text for n, text, _ in series if n == name)
            tip = f"{esc(label)} · {esc(label_name)}: {value:.3f} credits"
            if position == len(drawn) - 1 and seg_h > 4:            # rounded data end on the top segment only
                r = 4
                d = (f"M{x:.1f},{seg_top + seg_h:.1f} V{seg_top + r:.1f} Q{x:.1f},{seg_top:.1f} {x + r:.1f},{seg_top:.1f} "
                     f"H{x + bar - r:.1f} Q{x + bar:.1f},{seg_top:.1f} {x + bar:.1f},{seg_top + r:.1f} V{seg_top + seg_h:.1f} Z")
                parts.append(f'<path class="{cls}" d="{d}"><title>{tip}</title></path>')
            else:
                parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{seg_top + gap:.1f}" width="{bar:.1f}" height="{seg_h:.1f}">'
                             f"<title>{tip}</title></rect>")
            y -= h
        hit_h = plot_h * sum(values.values()) / y_max
        if values:
            parts.append(f'<rect class="hit" x="{left + band * index:.1f}" y="{top + plot_h - hit_h:.1f}" width="{band:.1f}" '
                         f'height="{max(hit_h, 1):.1f}"><title>{esc(label)}: {sum(values.values()):.3f} credits</title></rect>')
    parts.append(f'<line class="axis" x1="{left}" x2="{width - right}" y1="{top + plot_h}" y2="{top + plot_h}"/></svg>')
    legend = "".join(f'<span class="key"><i class="sw {cls}"></i>{esc(text)}</span>' for _, text, cls in series)
    return (f'<section class="card"><h2>{esc(title)}</h2><p class="sub">{esc(subtitle)}</p>'
            f'<div class="legend">{legend}{legend_extra}</div>{"".join(parts)}</section>')


def table(title, header, rows, subtitle="", numeric_from=1, empty="Nothing in this period."):
    head = "".join(f'<th class="{"num" if i >= numeric_from else ""}">{esc(h)}</th>' for i, h in enumerate(header))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i >= numeric_from else ""}">{cell}</td>' for i, cell in enumerate(row))
                   + "</tr>" for row in rows)
    if not rows:
        body = f'<tr><td colspan="{len(header)}" class="empty">{esc(empty)}</td></tr>'
    sub = f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""
    return f'<section class="card"><h2>{esc(title)}</h2>{sub}<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>'


def meter(share):
    return f'<span class="meter"><span style="width:{max(0.0, min(share, 1.0)) * 100:.1f}%"></span></span>'


def swatch(cls):
    return f'<i class="sw {cls}"></i>'


# ---- the page ------------------------------------------------------------------------------------------------------

def render_html(report, now=None):
    now = (now or datetime.datetime.now()).astimezone()
    turns, runs = report.turns, report.runs
    total = report.credits
    by_kind = group(turns, lambda a: a.get("kind", "chat"))
    kind_credits = {k: by_kind.get(k, [0.0, 0])[0] for k in KINDS}
    failed = sum(1 for _, a in runs if a.get("outcome") != "completed")
    days = max((report.end - report.start).total_seconds() / 86400, 1 / 24)
    first, last = report.start.strftime("%Y-%m-%d %H:%M"), report.end.strftime("%Y-%m-%d %H:%M")

    tiles = [
        ("Credits", fmt(total), "hero"),
        ("Billed turns", f"{len(turns):,}", ""),
        ("Credits per day", fmt(total / days), ""),
        ("Avg credits per turn", fmt(total / len(turns), 3) if turns else "0", ""),
        ("Subagent share", pct(kind_credits["subagent"], total), ""),
        ("Background share", pct(kind_credits["background"], total), ""),
        ("Subagent runs", f"{len(runs):,}" + (f' <small>({failed} not completed)</small>' if failed else ""), ""),
    ]
    tiles_html = "".join(f'<div class="tile {cls}"><div class="label">{esc(label)}</div><div class="value">{value}</div></div>'
                         for label, value, cls in tiles)

    slots_def, unit = buckets(report.start, report.end)
    kind_series = [(k, KIND_LABELS[k], f"s{i + 1}") for i, k in enumerate(KINDS)]
    top_agents, folded = agent_colors(report)
    agent_class = {name: f"s{i + 1}" for i, name in enumerate(top_agents)}
    agent_series = [(name, name, agent_class[name]) for name in top_agents] + ([("Other", f"Other ({len(folded)})", "other")] if folded else [])
    by_bucket_kind, by_bucket_agent = {}, {}
    for time_ns, credits, attrs in turns:
        key = bucket_key(time_ns, unit)
        kinds = by_bucket_kind.setdefault(key, {})
        kinds[attrs.get("kind", "chat")] = kinds.get(attrs.get("kind", "chat"), 0.0) + credits
        agent = agent_of(attrs)
        agents = by_bucket_agent.setdefault(key, {})
        name = agent if agent in agent_class else "Other"
        agents[name] = agents.get(name, 0.0) + credits
    unit_word = {"hour": "hour", "day": "day", "week": "week"}[unit]
    chart_kind = stacked_bars(f"Credits per {unit_word}, by kind",
                              "Chat: you and messaging channels. Subagent: work a chat handed off. Background: Crew's own jobs "
                              "(tips, titles, memory). Automation: cron, heartbeat, workflow.",
                              [(label, by_bucket_kind.get(key, {})) for key, label, _ in slots_def], kind_series)
    chart_agent = stacked_bars(f"Credits per {unit_word}, by agent",
                               f"The {len(top_agents)} agents that spent the most have their own color; the rest are Other.",
                               [(label, by_bucket_agent.get(key, {})) for key, label, _ in slots_def], agent_series)

    def breakdown(title, key, label, colored=False, subtitle=""):
        rows = []
        for value, (credits, count) in group(turns, key).items():
            mark = swatch(agent_class.get(value, "other")) if colored else ""
            rows.append([f"{mark}{esc(value)}", fmt(credits, 3), f"{count:,}", pct(credits, total) + meter(credits / total if total else 0),
                         fmt(credits / count, 3)])
        return table(title, [label, "Credits", "Turns", "Share", "Per turn"], rows, subtitle)

    tables = [
        breakdown("By agent", agent_of, "Agent", colored=True),
        breakdown("By model", lambda a: a.get("model") or "(unknown)", "Model"),
        breakdown("By workspace", lambda a: a.get("workspace") or "(none)", "Workspace",
                  subtitle="A subagent counts toward the workspace of the chat that spawned it."),
        breakdown("By surface", lambda a: f'{a.get("kind", "chat")} · {a.get("surface") or "?"}', "Kind · surface"),
    ]

    sessions = {}
    for time_ns, credits, attrs in turns:
        key = attrs.get("root_session_key")
        if not key:
            continue
        entry = sessions.setdefault(key, {"own": 0.0, "sub": 0.0, "turns": 0, "workspace": attrs.get("workspace", ""),
                                          "agents": {}, "first": time_ns, "last": time_ns})
        entry["sub" if attrs.get("kind") == "subagent" else "own"] += credits
        entry["turns"] += 1
        entry["last"] = max(entry["last"], time_ns)
        if attrs.get("kind") != "subagent" and attrs.get("agent"):
            entry["agents"][attrs["agent"]] = entry["agents"].get(attrs["agent"], 0) + 1
    top = sorted(sessions.items(), key=lambda item: -(item[1]["own"] + item[1]["sub"]))[:15]
    session_rows = [[esc(key), esc(max(e["agents"], key=e["agents"].get) if e["agents"] else "-"), esc(e["workspace"] or "-"),
                     _local(e["first"]).strftime("%m-%d %H:%M"), fmt(e["own"], 3), fmt(e["sub"], 3), f'<b>{fmt(e["own"] + e["sub"], 3)}</b>',
                     f'{e["turns"]:,}'] for key, e in top]
    session_table = table("Top sessions, with the subagents they spawned",
                          ["Session", "Agent", "Workspace", "Started", "Own", "Subagents", "Total", "Turns"], session_rows,
                          "Own: the chat's turns. Subagents: the work it handed off, counted toward it.", numeric_from=4)

    spawned = {}
    for _, credits, attrs in turns:
        if attrs.get("kind") == "subagent" and attrs.get("root_session_key"):
            entry = spawned.setdefault((attrs["root_session_key"], agent_of(attrs)), [0.0, 0])
            entry[0] += credits
            entry[1] += 1
    spawned_rows = [[esc(s), f"{swatch(agent_class.get(a, 'other'))}{esc(a)}", f"{n:,}", fmt(c, 3)]
                    for (s, a), (c, n) in sorted(spawned.items(), key=lambda item: -item[1][0])[:20]]
    spawned_table = table("Subagent spend per orchestrator session", ["Session", "Subagent", "Turns", "Credits"], spawned_rows, numeric_from=2)

    outcomes = {}
    for _, attrs in runs:
        entry = outcomes.setdefault(attrs.get("agent") or NO_AGENT, {"completed": 0, "failed": 0, "stopped": 0, "unknown": 0,
                                                                     "credits": 0.0, "ms": 0})
        entry[attrs.get("outcome", "unknown") if attrs.get("outcome") in entry else "unknown"] += 1
        entry["credits"] += attrs.get("subagent_credits", 0.0)
        entry["ms"] += attrs.get("duration_ms", 0)
    outcome_rows = []
    for agent, e in sorted(outcomes.items(), key=lambda item: -item[1]["credits"]):
        count = e["completed"] + e["failed"] + e["stopped"] + e["unknown"]
        not_done = e["failed"] + e["stopped"] + e["unknown"]
        outcome_rows.append([f"{swatch(agent_class.get(agent, 'other'))}{esc(agent)}", f"{count:,}", f'{e["completed"]:,}',
                             f'<span class="{"warn" if not_done else ""}">{not_done:,}</span>', fmt(e["credits"], 3),
                             f'{e["ms"] / count / 60000:.1f} min'])
    outcome_table = table("Subagent runs", ["Subagent", "Runs", "Completed", "Failed or stopped", "Credits", "Avg time"],
                          outcome_rows, "Credits as Crew reported them per run, retries included.")

    contexts = {}
    for _, _, attrs in turns:
        if "context_pct" in attrs:
            entry = contexts.setdefault(agent_of(attrs), [0.0, 0.0, 0, 0])
            entry[0] = max(entry[0], attrs["context_pct"])
            entry[1] += attrs["context_pct"]
            entry[2] += 1
            entry[3] += attrs["context_pct"] >= 80
    context_rows = [[f"{swatch(agent_class.get(a, 'other'))}{esc(a)}", f"{e[0]:.1f}%" + meter(e[0] / 100), f"{e[1] / e[2]:.1f}%",
                     f'<span class="{"warn" if e[3] else ""}">{e[3]:,}</span>']
                    for a, e in sorted(contexts.items(), key=lambda item: -item[1][0])]
    context_table = table("Context window use", ["Agent", "Highest", "Average", "Turns at 80% or more"], context_rows,
                          "Near 100% the chat is compacted, and every turn re-reads a large context.")

    daily = {}
    for time_ns, credits, attrs in turns:
        entry = daily.setdefault(_local(time_ns).date().isoformat(), dict({k: 0.0 for k in KINDS}, turns=0))
        entry[attrs.get("kind", "chat")] = entry.get(attrs.get("kind", "chat"), 0.0) + credits
        entry["turns"] += 1
    daily_rows = [[day, f'{e["turns"]:,}', f'<b>{fmt(sum(e[k] for k in KINDS), 3)}</b>'] + [fmt(e[k], 3) for k in KINDS]
                  for day, e in sorted(daily.items(), reverse=True)]
    daily_table = table("Daily history", ["Day", "Turns", "Credits"] + [KIND_LABELS[k] for k in KINDS], daily_rows,
                        "The numbers behind the charts.", numeric_from=1)

    if not turns:
        body = f'<section class="card empty-state"><h2>No billed turns in this period</h2><p class="sub">Crew recorded no usage between {esc(first)} and {esc(last)}. Try a longer period (--since 30d) or check --crew-home.</p></section>'
    else:
        body = (f'<div class="tiles">{tiles_html}</div>{chart_kind}{chart_agent}'
                f'<div class="grid2">{"".join(tables)}</div>{session_table}<div class="grid2">{spawned_table}{outcome_table}</div>'
                f'{context_table}{daily_table}')
    notes = [f"Period {esc(first)} to {esc(last)} ({esc(now.tzname() or '')}).",
             f"Read from {esc(_tilde(report.crew_home))}: Crew's usage rows (the same ones Crew's Usage tab reads) and session logs.",
             "Prompts, responses, file paths and error text are never included."]
    if report.privacy_note:
        notes.append(f"Privacy settings applied: {esc(report.privacy_note)}.")
    if report.unreadable:
        notes.append(f"{report.unreadable} row(s) could not be read and are left out.")
    notes.append(f"Generated {esc(now.strftime('%Y-%m-%d %H:%M'))} by crewctl {esc(__version__)} (kiro-kit).")
    return PAGE.format(title=esc(f"Kiro Crew usage, {first[:10]} to {last[:10]}"), period=esc(f"{first} to {last}"),
                       body=body, footer="".join(f"<p>{n}</p>" for n in notes))


def _tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def default_filename(start, end):
    last = (end - datetime.timedelta(seconds=1)).date()
    first = start.date()
    return f"kiro-usage-{first}.html" if first == last else f"kiro-usage-{first}_{last}.html"


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
.viz-root {{
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --warn: #b54708;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100; --series-5: #e87ba4;
  --series-6: #008300; --series-7: #6250d6; --series-other: #b7b5ad; --meter: #2a78d6; --meter-track: #e1e0d9;
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --warn: #ec835a;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500; --series-5: #d55181;
    --series-6: #008300; --series-7: #9085e9; --series-other: #5f5e59; --meter: #3987e5; --meter-track: #2c2c2a;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; }}
.viz-root {{ background: var(--page); color: var(--text-primary); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  min-height: 100vh; padding: 28px 32px 40px; }}
header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 16px; flex-wrap: wrap; margin-bottom: 18px; }}
h1 {{ font-size: 22px; font-weight: 600; margin: 0; }}
header .period {{ color: var(--text-secondary); }}
.card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; margin-bottom: 16px;
  break-inside: avoid; }}
h2 {{ font-size: 15px; font-weight: 600; margin: 0 0 2px; }}
.sub {{ color: var(--text-secondary); margin: 0 0 10px; font-size: 13px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }}
.tile {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }}
.tile .label {{ color: var(--text-secondary); font-size: 13px; }}
.tile .value {{ font-size: 24px; font-weight: 600; margin-top: 2px; }}
.tile.hero .value {{ font-size: 48px; line-height: 1.1; }}
.tile small {{ font-size: 13px; font-weight: 400; color: var(--text-secondary); }}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(520px, 1fr)); gap: 0 16px; }}
@media (max-width: 1100px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
.legend {{ display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 4px 0 8px; color: var(--text-secondary); font-size: 13px; }}
.key {{ display: inline-flex; align-items: center; gap: 6px; }}
.sw {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }}
.key .sw {{ margin-right: 0; }}
svg.chart {{ width: 100%; height: auto; display: block; }}
svg .grid {{ stroke: var(--grid); stroke-width: 1; }}
svg .axis {{ stroke: var(--axis); stroke-width: 1; }}
svg .tick {{ fill: var(--text-muted); font-size: 11px; font-variant-numeric: tabular-nums; }}
svg .hit {{ fill: transparent; }}
svg .hit:hover {{ fill: var(--text-primary); fill-opacity: 0.05; }}
.s1 {{ fill: var(--series-1); background: var(--series-1); }} .s2 {{ fill: var(--series-2); background: var(--series-2); }}
.s3 {{ fill: var(--series-3); background: var(--series-3); }} .s4 {{ fill: var(--series-4); background: var(--series-4); }}
.s5 {{ fill: var(--series-5); background: var(--series-5); }} .s6 {{ fill: var(--series-6); background: var(--series-6); }}
.s7 {{ fill: var(--series-7); background: var(--series-7); }} .other {{ fill: var(--series-other); background: var(--series-other); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; color: var(--text-secondary); font-weight: 500; border-bottom: 1px solid var(--axis); padding: 6px 8px; }}
td {{ border-bottom: 1px solid var(--grid); padding: 6px 8px; }}
td:first-child {{ white-space: nowrap; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
td.empty {{ color: var(--text-muted); text-align: center; padding: 14px; }}
.meter {{ display: inline-block; width: 56px; height: 6px; border-radius: 3px; background: var(--meter-track); margin-left: 8px;
  vertical-align: 1px; overflow: hidden; }}
.meter span {{ display: block; height: 100%; background: var(--meter); border-radius: 3px; }}
.warn {{ color: var(--warn); font-weight: 600; }}
footer {{ color: var(--text-muted); font-size: 12px; margin-top: 8px; }}
footer p {{ margin: 2px 0; }}
@media print {{
  .viz-root {{ background: #fff; padding: 0; }}
  .card, .tile {{ border-color: #ddd; }}
  svg .hit {{ display: none; }}
}}
</style></head>
<body><div class="viz-root">
<header><h1>Kiro Crew usage</h1><div class="period">{period}</div></header>
{body}
<footer>{footer}</footer>
</div></body></html>
"""
