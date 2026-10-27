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

## Install

Two parts, which can live on one machine or two:

| Part | Where | What it needs |
|---|---|---|
| **Exporter**: `crewctl usage push` | The machine that runs Kiro Crew | Python 3.8 or newer. Nothing to pip-install. |
| **Backend**: OTel Collector, VictoriaMetrics, VictoriaLogs, Grafana | That machine, or a server | Docker with Compose v2, about 300 MB of RAM and 2.5 GB of disk (details below) |

`crewctl` does not install or run the backend. This folder gives you a compose file for it, or you use a Victoria stack you
already have.

### Step 1. The backend (local stack)

```bash
cd kiro-kit/crewctl/usage-otel
docker compose up -d
docker compose ps            # grafana-plugins: Exited (0); the four others: Up
```

What happens on the first start, measured: a one-shot `grafana-plugins` step downloads the VictoriaLogs plugin into
Grafana's volume (internet needed this once), then Grafana starts with it. Everything is up in about 40 seconds; later
starts reuse the plugin. Grafana's own default plugin set is turned off, which saves about 330 MB.

Open http://localhost:3000, log in as admin / admin and change the password. The **Kiro Crew usage** dashboard is
already there; it stays empty until the exporter sends something.

| Service | Port (127.0.0.1 only) | Role |
|---|---|---|
| `otel-collector` | 4318 | OTLP/HTTP receiver: what crewctl sends to. Forwards metrics to VictoriaMetrics and logs to VictoriaLogs. |
| `victoriametrics` | 8428 | Metrics, PromQL. 90-day retention. |
| `victorialogs` | 9428 | Log records, LogsQL. 90-day retention. |
| `grafana` | 3000 | The dashboard. |

### Step 2. The exporter (on the Kiro Crew machine)

```bash
cd kiro-kit && git pull            # the usage commands are part of crewctl
./install.sh                       # once: links crewctl into ~/.local/bin (skip if crewctl already works)
crewctl usage init                 # writes ~/.config/crewctl/usage.json; nothing is sent until it has an endpoint
```

Edit `~/.config/crewctl/usage.json` and set the endpoint:

```json
{ "endpoint": "http://localhost:4318" }
```

Then check before sending anything:

```bash
crewctl usage push --dry-run       # the exact records and series that would be sent, decoded; sends nothing, writes nothing
crewctl usage push                 # the first real run: sends the last 3 days (backfill_days), then only what is new
crewctl usage status               # endpoints, last run, last error, bytes not sent yet
```

Open the dashboard. The numbers should match `crewctl usage status` ("Counted: ... credits").

**Try it without Kiro first.** `demo/make_demo_crew_home.py` writes a fake Crew home with a few hours of made-up usage,
in Crew's own file format:

```bash
python3 demo/make_demo_crew_home.py /tmp/demo-crew
crewctl --crew-home /tmp/demo-crew usage push --dry-run
```

### Step 3. Run it every few minutes

`crewctl usage schedule` prints the lines for your scheduler; you add them yourself. It picks launchd on macOS, systemd
on Linux with systemd, else cron.

```bash
crewctl usage schedule             # or: cron | systemd | launchd, and --every MINUTES (default 5)
```

A scheduler starts jobs with almost no environment: no conda or venv on its PATH, and none of the `CREWCTL_*` or
`KIROCREW_HOME` variables from your shell. So every printed line names the Python that ran the command
(`CREWCTL_PYTHON`), the settings file (`--settings`), the state folder (`CREWCTL_USAGE_STATE`), and Crew's folder when you
gave one with `--crew-home` or `KIROCREW_HOME`. The job then runs exactly what you ran by hand and shares its read
positions, so nothing is sent twice. Run `schedule` with the same Python and options you use for `push`.

- **systemd (Linux, no root):** save the two printed units under `~/.config/systemd/user/`, then
  `systemctl --user daemon-reload && systemctl --user enable --now crewctl-usage.timer`. Each run's output:
  `journalctl --user -u crewctl-usage.service`. To keep it running while you are logged out: `loginctl enable-linger`.
- **cron:** `crontab -e` and paste the printed line. Output goes to `~/.local/state/crewctl/usage/cron.log`.
- **launchd (macOS):** save the printed plist as `~/Library/LaunchAgents/local.crewctl.usage.plist`, then run the
  `mkdir` and `launchctl load` line printed under it.

Each job runs `push --quiet` at low priority (nice 10), so it prints only errors.

### Step 4 (optional). Your own Victoria stack instead of the local one

Through an OpenTelemetry Collector, set the base URL:

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

- **Auth.** Put headers in a file only you can read, one `Name: value` per line, and set `"headers_file"`:
  ```bash
  printf 'Authorization: Bearer %s\n' "$TOKEN" > ~/.config/crewctl/usage.headers && chmod 600 ~/.config/crewctl/usage.headers
  ```
  `push` refuses the file if other users can read it. It also refuses to send it over plain `http://` to another
  machine unless you set `"allow_insecure_http": true`. Redirects are never followed, so the headers cannot be carried
  to another host. Header values never appear in crewctl's output.
- **The local stack is for one machine.** Its ports listen on 127.0.0.1 only and have no TLS or auth. Opening port 4318
  to another machine needs both in front of it (a reverse proxy, or the collector's TLS and auth settings); this folder
  does not set that up.
- **Several machines.** Give each one a `"resource_attributes": {"service.instance.id": "laptop-1"}`. To keep them apart
  in the dashboard, give each its own `"service.name"` and use the dashboard's **Service** filter; every panel follows
  it. VictoriaLogs makes every resource attribute a stream field, so keep them few and stable.
- **Grafana.** Import `dashboards/kiro-usage.json`. It asks for two data sources: a Prometheus-type one pointing at
  VictoriaMetrics, and a VictoriaLogs one (plugin `victoriametrics-logs-datasource`).

### System requirements

The exporter: Python 3.8 or newer, any OS with a scheduler. A run takes milliseconds of CPU and about 15 MB, then exits.

The backend, measured at idle with demo data (Linux, Docker 29.6, Compose 5.3), next to the vendors' own guidance:

| Service | Memory | Image | Vendor guidance |
|---|---|---|---|
| Grafana | 230 MB | 1.42 GB, plus a 215 MB data volume (mostly the plugin) | at least 512 MB RAM and 1 CPU core |
| OpenTelemetry Collector (core) | 36 MB | 184 MB | none published |
| VictoriaMetrics | 20 MB | 37 MB | keep 50% of RAM and CPU spare and 20% of disk free |
| VictoriaLogs | 13 MB | 27 MB | the same; compresses logs about 10 times |

In total about 300 MB of RAM and 2.5 GB of disk, including images. Data growth is small (an estimate, not measured over
time): about 20 metric series written every 5 minutes is a few KB a day, and one log record per turn is a few MB a day
even for a busy crew. macOS and Windows with Docker Desktop should work but were not tested.

### What it changes on your machine, and how to remove it

- **crewctl:** adds the `usage` commands. The other commands are unchanged and their tests pass as before. No new
  dependency.
- **Kiro Crew:** nothing. `push` only reads `~/.kiro/crew/usage/tokens/` and `~/.kiro/crew/crew-log/sessions/`, never
  writes there, never calls a model, and does not turn on Crew's own telemetry.
- **Files:** `~/.config/crewctl/usage.json` (made by `usage init`, plus your headers file if you add one) and
  `~/.local/state/crewctl/usage/` (read positions, counters, the hash key; made by the first `push`).
- **Docker:** four images, three volumes (`vmdata`, `vldata`, `grafanadata`) and ports 3000, 4318, 8428 and 9428 on
  127.0.0.1. If one of those ports is taken, change the left-hand number in `docker-compose.yml`.

To remove it: delete the scheduler entry; `docker compose down -v` in this folder (deletes the stored data too);
`rm -r ~/.config/crewctl/usage.json ~/.local/state/crewctl/usage`.

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
- **The log panels show an error.** The Logs data source at the top of the dashboard is not a VictoriaLogs one, or the
  plugin is missing: `docker compose ps` should show `grafana-plugins` as `Exited (0)`; if not, `docker compose logs
  grafana-plugins` says why (usually no internet on the first start).
- **Metrics and log panels disagree.** They follow the same filters, so check the **Service** filter: two senders with
  different `service.name` values are both counted when it is set to All.
- **The scheduled job sends nothing, but `push` by hand works.** Print the lines again with `crewctl usage schedule`
  from the same shell and Python you use for `push`, and replace the old entry: older lines may lack `--settings` or
  `CREWCTL_USAGE_STATE`. For cron, read `~/.local/state/crewctl/usage/cron.log`; for systemd, `journalctl --user -u
  crewctl-usage.service`.
- **"most rows ... are not in the format crewctl knows".** Crew changed its usage-row format. `push` stops rather than
  guess, and sends nothing from that file. Tell the kiro-kit maintainers which Crew version you run.
- **The counters started again from 0.** The state folder was deleted. Graphs that use `increase()` or max−min treat it
  as a counter reset.
- **Change the dashboard.** Edit `dashboards/build_dashboards.py` and run it. A test checks that `kiro-usage.json` matches
  it and reads only metrics and fields that `push` sends.

## What was verified, and what was not

Verified on 2026-10-27, with Crew's files from 0.8.0 (real) and 0.10.0 (format read from its source):

- The protobuf encoding decodes with the official `opentelemetry-proto` 1.45.1 classes.
- `push` through the OpenTelemetry Collector (core image) into VictoriaMetrics and VictoriaLogs (latest images), and
  straight to both without the collector. The totals match the source rows exactly: 92 turns, 44.848 credits.
- The local stack from an empty Docker: the plugin step, then Grafana with the plugin loaded at its first start.
- Every dashboard panel queried through Grafana 13 with the VictoriaLogs plugin 0.33.1, for one service, another and
  both (metric and log panels agree each time), and the rendered dashboard checked by eye, including with two agents
  selected.
- The printed cron line run in a cron-like environment (no conda, minimal PATH, no CREWCTL_* variables), and the printed
  systemd service run as a transient user unit: both sent everything. The launchd plist parses with Python's plistlib.
- The test suite (313 tests) on Python 3.8 and 3.13.

Not verified:

- A live Kiro Crew 0.10.0 session. This machine's Crew data comes from 0.8.0; the 0.10.0 subagent entries were read
  from its source and docs.
- A systemd timer or cron entry left running over hours, and launchd on a real Mac.
- Windows.
