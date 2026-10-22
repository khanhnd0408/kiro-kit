# crewctl

Manage Kiro Crew agents from JSON files, with Terraform-style state.

`crewctl` also installs the agent-review kit into a project (`crewctl review install ...`). That is a separate job from creating the crew; see the [repository README](../README.md) for which command belongs to which. Run `crewctl` with no arguments for the overview.

You write one small JSON file per crewmate. crewctl turns each one into the Kiro agent file that Crew uses (`~/.kiro/agents/<name>.json`), remembers what it wrote, and tells you if anything changes outside it. It never starts `kiro-cli`, the Crew gateway or a model, so it costs no credits.

- How a crewmate is made, and what crewctl does and doesn't touch: [docs/how-crewmates-work.md](docs/how-crewmates-work.md)
- Tools, skills and MCP servers for each agent: [docs/tools-skills-mcp.md](docs/tools-skills-mcp.md)
- Crew's built-in skills, writing your own, and `crewctl skills` (builds SKILL.md files from JSON, with state; plus `lint-skills`): [docs/skills-guide.md](docs/skills-guide.md)
- A ready-made ten-crewmate team (orchestrator, GenAI, ML/DL, architecture, Python, Go, Rust, DevSecOps, incident inspector, spec writer): [crews/devteam/README.md](crews/devteam/README.md)
- Saving credits with cheap reader agents and scheduled collection: [docs/saving-credits.md](docs/saving-credits.md)
- Which model to give which agent, with credit costs: [docs/model-notes.md](docs/model-notes.md)

Python 3, standard library only. Tested on 3.12 and 3.13. The code parses as Python 3.8 but has not been run on it.

**Colors.** `crewctl` and every `--help` screen are colored when the output is a terminal: commands in bold cyan, `--options` in green, `PARAMETERS` in yellow, descriptions in plain text. `--no-color` or the `NO_COLOR` variable turns them off; `FORCE_COLOR=1` turns them on when piping into a pager (`less -R`).

**Short command (optional).** From the repository root run `./install.sh` once; it links `bin/crewctl` as `~/.local/bin/crewctl` (no pip, no virtual environment, works with conda). You then type `crewctl <command>` instead of `python3 -m crewctl <command>`, and `export CREWCTL_DIR=/path/to/your/config/folder` replaces `-c <folder>` on every call. Everything in this README works either way.

## Where it runs, and moving between machines

Run crewctl on **the machine where Kiro Crew's gateway runs**, because that is where `~/.kiro/agents/` lives and where Crew reads the agent files. For a remote Crew, run it on the remote host.

You can start on a Free-plan machine and move to a paid one later:

- Creating a crewmate is free. It makes a file and a record, and calls no model.
- *Using* a crewmate whose model your plan doesn't include can fail on the first prompt. So on a Free machine, keep agents on free models.
- Each agent file has two model fields: `"model"` (used while `crewctl.json` says `"plan": "free"`) and an optional `"paid_model"` (used when it says `"plan": "paid"`). Write your paid choice into `paid_model` now.

**Moving to the licensed machine:** change `"plan"` to `"paid"` in `crew/crewctl.json`, then run `plan` and `apply`. Every agent that has a `paid_model` switches to it, and the plan shows the exact diff first. Switching back is the same in reverse.

```json
{ "name": "conductor", "model": "claude-sonnet-4.5", "paid_model": "claude-opus-5.5", ... }
```

### First run on a new machine

Do these in order. Steps 1 to 4 change nothing outside temp folders.

```bash
git clone <this repo> && cd <repo>/crewctl
python3 --version                              # 1. any Python 3
python3 -m unittest discover -s tests          # 2. 109 tests, all in temp folders
python3 tests/crosscheck_with_crew.py          # 3. Crew loads the example files (exit 3 = Crew not found, fine)
python3 -m crewctl models                      # 4. which model ids this machine's Crew offers
```

Step 4 matters most on the paid machine: the **IN YOUR CREW LIST** column shows which of the ids in [docs/model-notes.md](docs/model-notes.md) really exist there. If `claude-opus-5.5` or `claude-sonnet-5.5` is missing or spelled differently, fix the id in `crewctl/models.json`.

Then try the next section with the throwaway folder before pointing crewctl at the real agents folder.

## Try it safely first

This uses a throwaway folder, so nothing in your real `~/.kiro` is touched.

```bash
cd crewctl
export CREWCTL_AGENTS_DIR=/tmp/crewctl-try/agents     # where agent files go
mkdir -p /tmp/crewctl-try && cp -r examples/crew /tmp/crewctl-try/crew
export CREWCTL_DIR=/tmp/crewctl-try/crew              # the config folder, so no -c is needed (do not make an alias: it hides the installed command)

crewctl validate        # check the three example crewmates
crewctl plan            # show what apply would do (writes nothing)
crewctl apply           # type yes; writes 3 files into /tmp/crewctl-try/agents
crewctl status          # one line per crewmate
```

When you are done, run `unset CREWCTL_DIR CREWCTL_AGENTS_DIR` so later commands use your real folders.

Now edit `/tmp/crewctl-try/crew/log-analyst.agent.json` (change the `model`) and run `crewctl plan` again. You'll see a diff. Edit one of the written files in `/tmp/crewctl-try/agents/` by hand and run `crewctl plan`: it flags **DRIFT** and `apply` refuses to overwrite it unless you pass `--force`.

## Use it for real

```bash
python3 -m crewctl -c crew init       # creates crew/ with settings, defaults and a template
cp crew/_template.agent.json crew/my-agent.agent.json
# edit my-agent.agent.json, write its prompt in crew/prompts/my-agent.md
python3 -m crewctl -c crew validate
python3 -m crewctl -c crew plan
python3 -m crewctl -c crew apply      # writes ~/.kiro/agents/my-agent.json
python3 -m crewctl -c crew register   # prints the kirocrew commands still needed
```

`crew/` can live in any repo. Add `crew/.crewctl/` to `.gitignore` (`init` does this); it holds the state and backups.

Set `CREWCTL_DIR=crew` to skip `-c crew`.

On Windows, replace `export X=Y` with `set X=Y` (or `$env:X="Y"` in PowerShell), `/tmp/...` with a folder like `%TEMP%\crewctl-try`, and `alias` with a small `crewctl.cmd`. The tests need a POSIX shell, and the `kirocrew` commands that `register` prints use POSIX quoting. Windows is untested.

## What goes in an agent file

Only `name`, `tools` and a prompt are required. Start from `templates/agent.template.json`, which explains every field.

```json
{
  "name": "log-analyst",
  "display_name": "Log Analyst",
  "description": "Reads log excerpts and reports error patterns.",
  "triggers": "Use when a task needs log lines summarized or compared.",
  "model": "qwen3-coder-next",
  "prompt_file": "prompts/log-analyst.md",
  "tools": ["fs_read", "grep", "glob"]
}
```

| Field | Goes to | Notes |
|---|---|---|
| `name` | crew record and agent file name | Cannot be `default`. Add `kiro_agent` if the name has spaces. |
| `model` | agent file | Used on the free plan. `crewctl models` lists the ids. Rejected if your plan can't use it. |
| `paid_model` | agent file | Optional. Used instead of `model` when `crewctl.json` says `"plan": "paid"`. |
| `prompt`, `prompt_file` or `prompt_files` | agent file | Exactly one. `prompt_files` is a list of files joined in order with a blank line between them, so shared blocks (working rules, language rules) live in one place. Files must be inside the config folder. |
| `tools` | agent file | Required. There is no default; `[]` means none. |
| `allowed_tools`, `mcp_servers`, `include_mcp_json`, `resources`, `permissions`, `hooks` | agent file | Optional. Details in [docs/tools-skills-mcp.md](docs/tools-skills-mcp.md). |
| `mcp_json_servers` | agent file | Names of servers already in your `~/.kiro/settings/mcp.json` (list, or `{name: {"tools": [...]}}`). Nothing is copied from that file. `crewctl mcp list` shows them; `crewctl mcp tools NAME` connects to one and prints its tool names; `crewctl mcp add SERVER CREWMATE...` (or `--all`, `--tools a,b`) and `crewctl mcp remove` edit this field in many files at once. |
| `skills` | agent file (`resources`) | Optional. Paths to `SKILL.md` files; each becomes a `skill://` entry. |
| `crew_servers` | agent file | Optional. `["core"]` lets a crewmate spawn others and use Crew memory; `["cron"]` adds schedules. crewctl finds the command on the machine it runs on. |
| `extra_agent_fields` | agent file | Any other key you want copied through. |
| `display_name`, `description`, `triggers`, `reasoning_effort`, `session_color`, `starred` | crew record | Set by `register --run`, which calls `kirocrew config set agents.<name>.<field>`. A name containing a dot cannot be written that way and is left for the dashboard. |
| `workspace`, `workspace_dir` | crew record | The Crew workspace to work in. It must already exist in Crew: `validate`, `plan`, `apply` and `register` stop with an error if it does not (Crew itself would quietly use the default workspace instead). Create it with `crewctl workspace check` (asks for the name, the folder under `~/.kiro/crew`, and optionally a workspace to copy the files from) or `crewctl workspace create NAME`. If only a workspace is missing, `plan`, `apply` and `register` offer to create it on the spot when you run them in a terminal. To move **existing** crewmates to another workspace, use `crewctl workspace assign WORKSPACE CREWMATE [CREWMATE ...]` (or `--all`; with no names it asks): it creates the workspace if needed, edits the `workspace` line in each crewmate's file (copies of the old files are kept) and runs `kirocrew agent update --workspace` for their Crew records, with one confirmation for the files and one for Crew. Any other change you make in the config files (description, triggers, display name, agent file) is applied to every crewmate at once by `crewctl register --run`; add `--target NAME` (repeatable) to limit it. `workspace_dir` is only the suggested folder name for `check`. |

Values in `crew/_defaults.json` fill in any field an agent file leaves out. Files whose names start with `_` are ignored.

`crew/crewctl.json` has the settings: `"plan"` (`"free"` or `"paid"`; on `"free"`, models that need a paid plan are errors and `paid_model` is ignored), `kirocrew_bin`, `allow_unknown_models`, `skills_dir`, `project_skills_dir`, `crew_server_commands` and `use_crew_servers`. `use_crew_servers: false` builds every agent without Crew's own tool servers (no spawn tools, no `skill_search`); crewctl warns about what each crewmate loses. `crew_server_commands` is a fallback: when Crew does not list its `kirocrew-core` / `kirocrew-cron` servers in `~/.kiro/agents/kirocrew.json`, `python3 -m crewctl crew-servers` explains why and prints the block to paste in. On any plan, `validate` warns if a model is not in that machine's own Crew model list.

## Commands

| Command | What it does |
|---|---|
| `init` | Create a starter config folder. |
| `validate` | Check the config. Changes nothing. |
| `models` | Show the model catalog, and which ids your Crew has. |
| `plan` | Show what `apply` would do. Writes nothing. `--detailed-exitcode` exits 2 if there are changes. |
| `apply` | Write the agent files and update the state. Asks for `yes`; `-y` skips that. |
| `destroy` | Remove the agent files crewctl manages. Never deletes your config files. `--target NAME` for one; `--delete-records` also deletes the Crew records. |
| `status` | One line per crewmate: file state and Crew registration. |
| `register` | Print the `kirocrew` commands still needed (create the record, then set its display name, description and triggers). `--run` runs them. |
| `import NAME` | Adopt an agent file that already exists. |
| `state list`, `show`, `rm`, `mv` | Look at or edit the state. |

Useful flags: `--target NAME` (limit to one crewmate), `--force` (overwrite drifted files; a backup is kept), `--agents-dir`, `--crew-home`, `-v` (show notes).

## Removing crewmates

A crewmate is an agent file plus a Crew record (see [how crewmates work](docs/how-crewmates-work.md)). crewctl removes the file; the record is removed with a `kirocrew agent delete` command, which crewctl prints and only runs if you ask.

**Delete one crewmate** (the Terraform way: the config is the truth):

```bash
rm crew/log-analyst.agent.json          # or delete the file in git
crewctl plan                            # shows: - log-analyst (delete)
crewctl apply                           # removes ~/.kiro/agents/log-analyst.json
crewctl register --run                  # runs: kirocrew agent delete log-analyst
```

`apply` prints the `kirocrew agent delete` line when it finishes, and `register` keeps showing it until the record is gone, so you can't forget it.

**Tear down without deleting your config** (you want to recreate them later):

```bash
crewctl destroy                         # all managed agent files; asks first
crewctl destroy --target log-analyst    # just one
crewctl destroy --delete-records        # also runs kirocrew agent delete for them
```

`destroy` never deletes your `*.agent.json` files. Run `apply` again and the crewmates come back.

Things to know:

- Every file removed is first copied to `crew/.crewctl/backups/<time>/`.
- A file someone edited since the last apply is **drift**, and `apply` / `destroy` refuse to remove it unless you add `--force`.
- crewctl only touches files it created. A hand-made agent in `~/.kiro/agents` is never removed.
- Without `--delete-records` (or `register --run`), the Crew record stays. The crewmate still appears in Crew but points at a file that no longer exists.
- `kirocrew agent delete` removes the record and its team entry. From reading Crew's code, it does not appear to delete the crewmate's private memory. Creating the same name again gives it a fresh memory.
- The built-in `default` crewmate is never touched.
- `crewctl state rm NAME` makes crewctl forget a removed crewmate and stop reminding you about its record.

## How the state works

crewctl compares three things for each crewmate:

| | |
|---|---|
| config | your `*.agent.json` |
| state | `crew/.crewctl/state.json`: the hash of each file it wrote |
| disk | the file in the agents folder right now |

- **Config changed, disk still matches the state:** a normal update.
- **Disk differs from the state:** **drift**. Someone, or Crew's dashboard, edited the file. `apply` stops unless you pass `--force`.
- **File exists but isn't in the state:** **conflict**. crewctl never overwrites a file it doesn't own. Run `import NAME` to adopt it.
- **In the state but no longer in the config:** deleted on `apply`.
- Before any file is overwritten or deleted, a copy goes to `crew/.crewctl/backups/<time>/`.
- Only one run can write at a time (a lock file). `--break-lock` clears a stuck one.

## What crewctl doesn't do

It writes the **agent files** only. The **crew record** (the entry in Crew's own `config.json` that makes it a crewmate) is created by Crew, because Crew also sets up the crewmate's private memory when it does that. So after `apply`:

```bash
python3 -m crewctl -c crew register          # shows, for each crewmate:
#   kirocrew agent create --name log-analyst --kiro-agent log-analyst --workspace default
#   kirocrew config set agents.my-agent.display_name '"My Agent"'
```

Run those yourself, or use `register --run` when you're ready. `kirocrew agent create` only sets the name, agent file and workspace, so crewctl follows it with `kirocrew config set agents.<name>.<field> <json text>` for the display name, description and triggers. Without `triggers`, Crew's orchestrator never offers the crewmate.

## Safety

- The config is checked for credential-looking text (API keys, tokens, private keys) and rejected, so secrets don't end up in a repo. Pass secrets through the environment.
- crewctl reads only the `agents` and `workspaces` parts of Crew's `config.json`. The tokens stored beside them are never read into output.
- Agent files are written with mode 600, like Crew's own.
- Names starting with `kirocrew` and Kiro's reserved ids are refused.

## Tests

```bash
python3 -m unittest discover -s tests          # 109 tests, all in temp folders
python3 tests/crosscheck_with_crew.py          # asks your installed Crew to load the example files (read-only, sandboxed)
```

The tests set `HOME` to a temp folder and use a fake `kirocrew` script, so they cannot touch your real Crew or run Kiro.

## Layout

```
crewctl/
  crewctl/            the program (cli, config, engine, state, mapping, models, models.json)
  templates/          files `init` copies
  examples/crew/      three sample crewmates
  docs/               how crewmates work, model notes, skills guide
  tests/
```
