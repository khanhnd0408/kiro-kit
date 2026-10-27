# Kiro Crew usage in Grafana: `crewctl usage`

`crewctl usage push` sends Kiro Crew's credit usage to an OpenTelemetry (OTLP) backend. This folder holds a local
**VictoriaMetrics + VictoriaLogs + OpenTelemetry Collector + Grafana** stack to receive it, and the Grafana dashboard.

What you get, per billed turn: the credits, the model, the agent, the workspace, the session it belongs to (a
subagent's spend counts toward the chat that spawned it), and how full the context window was. The dashboard shows
credits by agent, model, workspace and kind (chat, subagent, background, automation), the top sessions, subagent cost
per orchestrator session, subagent outcomes, context pressure and the burn rate.

Architecture: [../docs/usage-otel-architecture.drawio](../docs/usage-otel-architecture.drawio) (a PNG export sits next to it).

## How it works

```
Kiro Crew (unchanged)                       crewctl usage push (one run, then exit)      Victoria stack
~/.kiro/crew/usage/tokens/<date>.jsonl ──┐  read new lines ─ join ─ filter ─ encode  ──▶ OTel Collector :4318
~/.kiro/crew/crew-log/sessions/*/log.jsonl┘  (offsets in ~/.local/state/crewctl/usage)    ├─▶ VictoriaMetrics  ─┐
                                                                                         └─▶ VictoriaLogs     ─┴▶ Grafana
```

- **Where the numbers come from.** Crew writes one row per billed turn, on every surface, to
  `usage/tokens/<date>.jsonl`. That file is the only source of credit amounts, so nothing is counted twice; it is the
  same file Crew's own Usage tab reads, with the same format in Crew 0.8.0 and 0.10.0. The per-session crew log is used
  only to join: the session id and workspace of a row, and the session that spawned a subagent (Crew 0.9.0+).
- **One run, then exit.** A scheduler starts `push` every few minutes. Each run reads only what was added since the last
  run, sends it, and exits. Nothing stays in memory between runs.
- **No queue.** A file's read position moves only after its records were sent. If the backend is down, the next run
  sends the same lines again from Crew's own files. Subagent runs are remembered by id, so none is sent twice.
- **Kiro reports no token counts**, only credits and context size. Token fields appear only for providers that report
  them.

## Quick start: the local stack

Needs Docker. Every port listens on `127.0.0.1` only.

```bash
cd crewctl/usage-otel
docker compose up -d                 # collector :4318, VictoriaMetrics :8428, VictoriaLogs :9428, Grafana :3000
```

Grafana downloads the VictoriaLogs plugin on its first start. That needs internet access once and takes about a minute;
until it finishes, the log panels show an error, so reload the dashboard then.

Then, on the machine that runs Kiro Crew:

```bash
crewctl usage init                   # writes ~/.config/crewctl/usage.json; export stays off until it has an endpoint
# set "endpoint": "http://localhost:4318" in that file
crewctl usage push --dry-run         # shows exactly what would be sent, decoded; sends nothing, moves nothing
crewctl usage push                   # the first real run (backfills the last 3 days, see backfill_days)
crewctl usage status                 # last run, last error, how much is not sent yet
```

Open http://localhost:3000 (admin / admin), dashboard **Kiro Crew usage**.

**Try it without Kiro.** `demo/make_demo_crew_home.py` writes a fake Crew home with a few hours of made-up usage, in
Crew's own file format:

```bash
python3 demo/make_demo_crew_home.py /tmp/demo-crew
crewctl --crew-home /tmp/demo-crew usage push
```

## Run it every few minutes

`crewctl usage schedule` prints the lines for your scheduler. It installs nothing; you add them yourself.

```bash
crewctl usage schedule               # launchd on macOS, systemd on Linux with systemd, else cron
crewctl usage schedule cron --every 5
crewctl usage schedule systemd       # a user service + timer: no root needed
crewctl usage schedule launchd
```

Each one runs `crewctl usage push --quiet` at low priority (nice 10), so it prints only errors.

## Point it at your own Victoria stack

Through an OpenTelemetry Collector (like the local stack), set the base URL:

```json
{ "endpoint": "https://otel-collector.example.internal:4318" }
```

Straight to VictoriaLogs and VictoriaMetrics, without a collector, set the two full URLs:

```json
{
  "endpoint": "",
  "logs_endpoint": "https://victorialogs.example.internal:9428/insert/opentelemetry/v1/logs",
  "metrics_endpoint": "https://victoriametrics.example.internal:8428/opentelemetry/v1/metrics"
}
```

Both ways were tested against VictoriaMetrics and VictoriaLogs (see "What was verified").

- **Auth.** Put headers in a file only you can read, one `Name: value` per line, and set `"headers_file"`:
  ```bash
  printf 'Authorization: Bearer %s\n' "$TOKEN" > ~/.config/crewctl/usage.headers && chmod 600 ~/.config/crewctl/usage.headers
  ```
  `push` refuses the file if other users can read it. It also refuses to send it over plain `http://` to another
  machine unless you set `"allow_insecure_http": true`. Redirects are never followed, so the headers cannot be carried
  to another host. Header values never appear in crewctl's output.
- **Which machine.** Add a few resource attributes, for example
  `"resource_attributes": {"deployment.environment": "client-a", "service.instance.id": "laptop-1"}`. VictoriaLogs makes
  every resource attribute a stream field, so keep them few and stable.
- **Grafana.** Import `dashboards/kiro-usage.json`. It asks for two data sources: a Prometheus-type one pointing at
  VictoriaMetrics, and a VictoriaLogs one (plugin `victoriametrics-logs-datasource`).

## What is sent, and what never is

Log record attributes. Every record carries `event`: `turn` (one per billed turn) or `subagent_run` (one per finished
subagent).

| Attribute | Meaning |
|---|---|
| `kind` | `chat` (dashboard and messaging channels), `subagent`, `background` (Crew's own jobs: tips, suggestions, titles, memory), `automation` (cron, heartbeat, workflow, monitor) |
| `surface`, `model`, `provider`, `agent`, `workspace`, `app`, `stop_reason` | As Crew recorded them |
| `session_key`, `session_id` | The chat (Crew's slot key) and the session id of a turn |
| `root_session_key`, `root_session_id` | The chat the spend belongs to: its own for a chat turn, the spawning chat for a subagent |
| `subagent_id`, `parent_session_key`, `parent_session_id` | For subagent turns and runs |
| `credits`, `duration_ms`, `context_used`, `context_window`, `context_pct` | For turns |
| `outcome`, `subagent_credits`, `duration_ms` | For subagent runs. `subagent_credits` is missing when Crew did not bill the run, which is not the same as 0 |
| `cost_usd`, `input_tokens`, `output_tokens` | Only for providers that report them |

Metrics (cumulative counters, sent in full on every run, Prometheus-safe names that no backend renames):

| Metric | Labels |
|---|---|
| `kiro_credits_total`, `kiro_turns_total` | `kind`, `surface`, `model`, `provider`, `agent`, `workspace` |
| `kiro_cost_usd_total`, `kiro_tokens_total{direction}` | The same; only when a provider reports them |
| `kiro_usage_last_push_timestamp_seconds` | None. The dashboard's "Last push" turns red when the scheduler stops |

Session ids are on log records only, never metric labels, so the metrics database stays small.

**Never sent:** prompt or response text, file paths, `cwd`, a subagent's error text, credentials. A test checks every
attribute against an allowlist and feeds in text that must not come out.

**Privacy settings** in `usage.json`:

- `"drop_fields"` never sends the listed fields. Allowed: `agent`, `workspace`, `app`, `session_key`, `session_id`,
  `parent_session_key`, `parent_session_id`, `subagent_id`, `stop_reason`, `context`.
- `"hash_fields"` sends a keyed hash (12 hex characters) instead of the name, the same in logs and metrics. The key is a
  random secret in the state folder (mode 600), so nobody can reverse a hash by trying likely names.
- `"signals": ["metrics"]` sends counters only, no per-turn records.

## Load on the machine

Measured with the standard library on a laptop, building and gzipping the payload: 1,000 turns take about 13 ms of CPU
and 14 MB of memory, and send 15 KB. A run every 5 minutes usually has a handful of new turns, so Python's start-up
costs more than the work. Each run is also capped:

- at most 1,000 records per request (`max_rows_per_request`);
- at most 20,000 rows per run (`max_rows_per_run`);
- a 10-second network timeout and a 120-second limit per run;
- 16 MB read per file per run.

`push` never calls a model, so it costs no credits, and never writes under `~/.kiro/crew`.

## Queries to start from

LogsQL (VictoriaLogs):

```
{service.name="kiro-crew"} event:=turn | stats by (root_session_key) sum(credits) as credits | sort by (credits desc) | limit 20
{service.name="kiro-crew"} event:=turn kind:=subagent | stats by (root_session_key, agent) sum(credits) as credits
{service.name="kiro-crew"} event:=subagent_run outcome:!=completed
```

PromQL / MetricsQL (VictoriaMetrics):

```
sum by (agent) (max_over_time(kiro_credits_total[24h]) - min_over_time(kiro_credits_total[24h]))
sum by (kind) (increase(kiro_credits_total[1h]))
```

For totals the dashboard uses `max_over_time - min_over_time`. It is exact on any Prometheus-style store. Prometheus'
own `increase()` extrapolates and overstated a test total by 25% (VictoriaMetrics' `increase()` does not). The 0 point
crewctl sends before a new series keeps the result right for agents that appear inside the window.

## Troubleshooting

- **Nothing in Grafana.** Run `crewctl usage status`: it shows the endpoints, the last error and how many bytes of usage
  rows are not sent yet. `crewctl usage push --dry-run` shows what would go out.
- **A push you just made is missing from an instant query.** VictoriaMetrics leaves the last 30 seconds out of instant
  queries (`-search.latencyOffset`). Wait half a minute.
- **The log panels show an error.** The VictoriaLogs plugin is still installing (first start), or the Logs data source
  at the top of the dashboard is not a VictoriaLogs one.
- **"most rows ... are not in the format crewctl knows".** Crew changed its usage-row format. `push` stops rather than
  guess, and sends nothing from that file. Tell the kiro-kit maintainers which Crew version you run.
- **The counters started again from 0.** The state folder was deleted. Graphs that use `increase()` or max−min treat it
  as a counter reset.
- **Change the dashboard.** Edit `dashboards/build_dashboards.py` and run it. A test checks that `kiro-usage.json` matches
  it and reads only metrics and fields that `push` sends.

## What was verified, and what was not

Verified on 2026-10-27, with Crew's files from 0.8.0 (real) and 0.10.0 (format read from its source):

- The protobuf encoding decodes with the official `opentelemetry-proto` 1.45.1 classes.
- `push` through the OpenTelemetry Collector (contrib 0.164.0) into VictoriaMetrics and VictoriaLogs (latest images),
  and straight to both without the collector. The totals match the source rows exactly: 92 turns, 44.848 credits.
- Every dashboard panel queried through Grafana 13 with the VictoriaLogs plugin 0.33.1, and the rendered dashboard
  checked by eye.
- The test suite (313 tests) on Python 3.8 and 3.13.

Not verified:

- A live Kiro Crew 0.10.0 session. This machine's Crew data comes from 0.8.0; the 0.10.0 subagent entries were read
  from its source and docs.
- The launchd and systemd units on a real scheduler (the text is generated and tested, but not installed anywhere).
- Windows.
