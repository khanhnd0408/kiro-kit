# kiro-kit

One command, `crewctl`, for three separate jobs. Pick the job, then the command.

| I want to... | Job | Start here |
|---|---|---|
| Create and manage a team of Kiro Crew agents (crewmates) from JSON files | **Crew** | `crewctl/crews/devteam/SETUP.md` (ten ready-made crewmates) or `crewctl/README.md` |
| Review a project's prompts and agents against the Codex and Claude Code patterns (a structured analysis with scorecards and verified findings) | **Review** | `aiops/README.md` |
| See Kiro Crew's credit usage per agent, model, workspace and session in Grafana (VictoriaMetrics + VictoriaLogs, over OTLP) | **Usage** | `crewctl/usage-otel/README.md` |

## Install the command (once)

On the machine where you will use it, from this folder:

```bash
./install.sh            # links bin/crewctl as ~/.local/bin/crewctl
crewctl                 # prints the overview of the three jobs
```

There is nothing to pip-install and no virtual environment: crewctl uses only Python's standard library and runs with whichever `python3` is on your PATH when you run it (conda, a venv, the system Python). Set `CREWCTL_PYTHON=/path/to/python` to choose another. If the script says to add `~/.local/bin` to your PATH, do that and open a new terminal. Keep the repository where it is. `./install.sh --uninstall` removes the link. Needs Python 3.8 or newer.

Without installing: run `./bin/crewctl <command>` from this folder, or `cd crewctl && python3 -m crewctl <command>`. Running `python3 -m crewctl` from this top folder fails with `No module named crewctl`; the module is one level down.

## Not sure which command?

```bash
crewctl guide                       # describe what you want; it builds the command and shows what each choice does
source <(crewctl completion bash)   # Tab completion (zsh: the same with `zsh`; fish: crewctl completion fish | source)
```

## Job 1: the crew

Start from the ready-made team (`crewctl -c ~/crew init --team devteam` makes your own editable copy), or from an empty folder (`crewctl -c ~/crew init`, then add `<name>.agent.json` files; until you do, `validate` says "no *.agent.json files found").

```bash
export CREWCTL_DIR="$PWD/crewctl/crews/devteam"     # the team folder; replaces -c on every command
crewctl validate
crewctl skills apply
crewctl apply
crewctl register --run
```

Full steps, with what you should see after each one: `crewctl/crews/devteam/SETUP.md`.

## Job 2: the review

```bash
crewctl review install /path/to/your/project --reference-source /path/to/system_prompts_leaks
crewctl review check /path/to/your/project      # re-check every file:line citation later
```

`--reference-source` is a clone of the public `system_prompts_leaks` repository, which the review quotes. The crew job does not need it. After installing, follow `aiops/README.md`: it says how to start the review in Kiro Crew or in the IDE.

## Job 3: credit usage in Grafana

Two parts: the exporter (`crewctl usage`, on the machine that runs Kiro Crew) and a backend (OpenTelemetry Collector,
VictoriaMetrics, VictoriaLogs, Grafana). crewctl does not install the backend; `crewctl/usage-otel/docker-compose.yml`
runs one locally, or you point the exporter at a Victoria stack you already have.

```bash
# 1. backend (needs Docker; ports on 127.0.0.1 only; about 300 MB RAM, 2.5 GB disk)
cd crewctl/usage-otel && docker compose up -d        # Grafana: http://localhost:3000 (admin / admin)

# 2. exporter (Python 3.8+, nothing to pip-install)
crewctl usage init                                   # ~/.config/crewctl/usage.json; set "endpoint": "http://localhost:4318"
crewctl usage push --dry-run                         # exactly what would be sent; sends nothing
crewctl usage push                                   # send what Crew recorded since the last run, then exit

# 3. every few minutes
crewctl usage schedule                               # prints the systemd, launchd or cron lines; you add them
```

No monitoring stack wanted? `crewctl usage export --since 7d` writes one self-contained HTML report from Crew's files
(no Docker, no endpoint), and `crewctl usage schedule --report` prints a job that writes yesterday's report every day.

It only reads Crew's usage files, writes nothing under `~/.kiro`, and calls no model. Prompts, responses, paths and error
text are never sent. The full guide (requirements, your own Victoria stack, several machines, privacy settings, what it
changes and how to remove it): `crewctl/usage-otel/README.md`.

## All commands

```
Crew:    init  validate  plan  apply  destroy  status  register  skills  lint-skills  models  workspace  mcp  crew-servers  import  state
Review:  review install  |  review check  |  review where
Usage:   usage init  |  usage push  |  usage status  |  usage schedule
```

`crewctl <command> --help` shows the options of one command.

## Folders

| Folder | What it holds |
|---|---|
| `crewctl/` | The command itself, the ready-made `crews/devteam` team, the usage stack and dashboard (`usage-otel/`), docs and tests. |
| `aiops/` | The review kit that `crewctl review install` copies into a project: method documents, helper scripts and adapters for Crew and the IDE. |
