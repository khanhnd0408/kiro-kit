# kiro-kit

One command, `crewctl`, for two separate jobs. Pick the job, then the command.

| I want to... | Job | Start here |
|---|---|---|
| Create and manage a team of Kiro Crew agents (crewmates) from JSON files | **Crew** | `crewctl/crews/devteam/SETUP.md` (ten ready-made crewmates) or `crewctl/README.md` |
| Review a project's prompts and agents against the Codex and Claude Code patterns (a structured analysis with scorecards and verified findings) | **Review** | `aiops/README.md` |

## Install the command (once)

On the machine where you will use it, from this folder:

```bash
./install.sh            # links bin/crewctl as ~/.local/bin/crewctl
crewctl                 # prints the overview of the two jobs
```

There is nothing to pip-install and no virtual environment: crewctl uses only Python's standard library and runs with whichever `python3` is on your PATH when you run it (conda, a venv, the system Python). Set `CREWCTL_PYTHON=/path/to/python` to choose another. If the script says to add `~/.local/bin` to your PATH, do that and open a new terminal. Keep the repository where it is. `./install.sh --uninstall` removes the link. Needs Python 3.8 or newer.

Without installing: run `./bin/crewctl <command>` from this folder, or `cd crewctl && python3 -m crewctl <command>`. Running `python3 -m crewctl` from this top folder fails with `No module named crewctl`; the module is one level down.

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

## All commands

```
Crew:    init  validate  plan  apply  destroy  status  register  skills  lint-skills  models  workspace  mcp  crew-servers  import  state
Review:  review install  |  review check  |  review where
```

`crewctl <command> --help` shows the options of one command.

## Folders

| Folder | What it holds |
|---|---|
| `crewctl/` | The command itself, the ready-made `crews/devteam` team, docs and tests. |
| `aiops/` | The review kit that `crewctl review install` copies into a project: method documents, helper scripts and adapters for Crew and the IDE. |
