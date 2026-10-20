# kiro-kit

One command, `crewctl`, for two separate jobs. Pick the job, then the command.

| I want to... | Job | Start here |
|---|---|---|
| Create and manage a team of Kiro Crew agents (crewmates) from JSON files | **Crew** | `crewctl/crews/devteam/SETUP.md` (ten ready-made crewmates) or `crewctl/README.md` |
| Review a project's prompts and agents against the Codex and Claude Code patterns (a structured analysis with scorecards and verified findings) | **Review** | `aiops/README.md` |

## Install the command (once)

```bash
cd crewctl
python3 -m pip install -e .        # or: pipx install -e .
crewctl                            # prints the overview below
```

Keep the repository where it is: the command reads the files here.

## Job 1: the crew

```bash
export CREWCTL_DIR="$PWD/crews/devteam"     # the team folder; replaces -c on every command
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
Crew:    init  validate  plan  apply  destroy  status  register  skills  lint-skills  models  crew-servers  import  state
Review:  review install  |  review check  |  review where
```

`crewctl <command> --help` shows the options of one command.

## Folders

| Folder | What it holds |
|---|---|
| `crewctl/` | The command itself, the ready-made `crews/devteam` team, docs and tests. |
| `aiops/` | The review kit that `crewctl review install` copies into a project: method documents, helper scripts and adapters for Crew and the IDE. |
