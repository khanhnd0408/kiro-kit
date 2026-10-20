# devteam: ten crewmates for development, security and operations

A ready-to-apply crewctl config. Nothing here is applied until you run it on the machine where Crew is installed. The default `plan` is `free`; change it to `paid` in `crewctl.json` on the licensed machine and every crewmate switches to its `paid_model`.

| Crewmate | What it is for | Free model | Paid model | Can write / run |
|---|---|---|---|---|
| `orchestrator-agent` | The one coordinator. Picks who does what, briefs them, supervises long-running subagents with Crew's built-in monitor loop, verifies results. Read-only. | claude-sonnet-4.5 | claude-sonnet-5.5 | read only; spawns and watches the others |
| `genai-agent` | GenAI application work. Every decision is scored against an evidence checklist by a script. | claude-sonnet-4.5 | claude-sonnet-5.5 | files, shell, web |
| `mldl-agent` | Statistics, classic ML and deep learning, with leakage checks, baselines and intervals. | claude-sonnet-4.5 | claude-sonnet-5.5 | files, shell, web |
| `backend-architecture-agent` | Interviews you until a gate script says the context is enough, then picks technology, architecture, style and folder tree. | claude-sonnet-4.5 | claude-opus-5.5 | files, web (no shell) |
| `python-agent` | Python: tuning, gRPC, GIL, free-threading, subinterpreters, queues, caches. | deepseek-3.2 | claude-sonnet-5.5 | files, shell |
| `go-agent` | Go: clean code, profiling, CPU and kernel-aware optimization. | deepseek-3.2 | claude-opus-5.5 | files, shell |
| `rust-agent` | Rust: safe code, profiling, CPU and kernel-aware optimization. | deepseek-3.2 | claude-opus-5.5 | files, shell |
| `devsecops-agent` | Infrastructure, GitOps pipelines, monitoring, automation, CVE-aware pipeline design. | claude-sonnet-4.5 | claude-sonnet-5.5 | files, shell, web |
| `inspector-agent` | Incident investigation from metrics, logs and traces; fills the incident report template. | claude-sonnet-4.5 | claude-sonnet-5.5 | files, shell (queries are read-only) |
| `spec-agent` | Writes spec, plan, todo and checklist; checks completion with a script. | claude-sonnet-4.5 | claude-sonnet-5.5 | files, shell |

The name `mldl-agent` replaces `ml/dl-agent`, because a slash is not allowed in an agent name.

Go and Rust use the most expensive paid model because low-level optimization is where a weaker model makes the costliest mistakes. Change `paid_model` in their files if the credits matter more.

## How it is put together

```
crews/devteam/
  crewctl.json                 settings (plan)
  *.agent.json                 the ten crewmates
  prompts/*.md                 one prompt per crewmate, plus shared working rules
  skills/*.skill.json          ten skills, built into SKILL.md files by `crewctl skills`
  skills/assets/               scripts, templates and reference documents the skills copy in
```

Skills (all built from the files above):

| Skill | Used by | What it adds |
|---|---|---|
| `genai-decision-checklist` | genai-agent | Weighted checklist; `score_decision.py` computes confidence from evidence and the Wilson interval for quality claims. |
| `ml-experiment-rigor` | mldl-agent | Leakage, baseline and uncertainty checklist; same scoring script. |
| `architecture-interview` | backend-architecture-agent | Question bank, `context_gate.py` (exit 0 only when every topic is answered by the user), decision record template, structure guide. |
| `python-engineering`, `go-engineering`, `rust-engineering` | language agents | Reference documents plus a measure-first workflow. |
| `gitops-cve-pipeline`, `iac-security-review` | devsecops-agent | GitOps and CVE policy references; changed-file lister for IaC reviews. |
| `incident-investigation` | inspector-agent | `collect_evidence.py` (VictoriaLogs, VictoriaMetrics, Tempo; read-only) and the incident report template. |
| `spec-driven-workflow` | spec-agent | The four templates and `checklist_status.py` (exit 0 only when every box is ticked with evidence). |

How the confidence rule works: the agent fills a checklist, a script turns it into `high`, `medium`, `low` or `blocked` by a fixed rule (written in the script), and the agent must report that result unchanged. An item marked yes without evidence counts as unknown. The model never produces the number.

## Put it on a machine

Step-by-step guide with expected output and troubleshooting: [SETUP.md](SETUP.md). The short version:

On a machine where Crew is installed and has started once (it writes `~/.kiro/agents/kirocrew.json`, which crewctl needs to wire the Crew tools):

```bash
./install.sh                                  # once, from the kiro-kit folder: gives you the `crewctl` command
export CREWCTL_DIR="$PWD/crewctl/crews/devteam"    # the team folder, so you do not repeat -c
crewctl validate
crewctl skills plan          # shows the SKILL.md files
crewctl skills apply         # writes them to ~/.kiro/crew/skills
crewctl plan                 # shows the ten agent files
crewctl apply
crewctl register             # prints the kirocrew commands; add --run to execute them
```

`register --run` also fills in each crewmate's display name, description and triggers. To remove everything: `skills destroy`, `destroy --delete-records`.

The agent files point at skills in `~/.kiro/crew/skills/`. If you set another `skills_dir`, edit the paths in the `skills` lists.

## Language rules

The user may write in English or Vietnamese. The agents think, plan, call tools, write files and brief each other in **English only**. Vietnamese appears in exactly two places: the final answer to the user, and anything that asks the user for input (the `ask_question` card with its header, question and option text, `[OPTIONS: ...]` lists, and plain questions). Code, commands, paths, identifiers and quoted log lines are never translated. Files stay English unless the user asks for a Vietnamese file. Progress lines the orchestrator prints during its check loop are not final answers, so they stay English.

The rules live in `prompts/_language.md` and are added to every agent by `prompt_files` (the agent's own prompt, then `prompts/_working-rules.md`, then the language rules last). Edit the one file to change them for all ten. The orchestrator starts every brief to a specialist with `SUBAGENT TASK from orchestrator-agent. Reply in English only; ...`; a specialist that sees that line writes English only and never asks the user. `orchestrator-agent`, `backend-architecture-agent` and `spec-agent` have the `ask_question` tool for the Vietnamese question cards.

## Workspaces

Every crewmate here uses the `default` workspace. If you give one a different `workspace`, it must exist in Crew first; `crewctl validate` stops with an error otherwise. `crewctl workspace check` finds the missing ones and asks for the name, the folder name under `~/.kiro/crew` and (optionally) an existing workspace to copy files from; `plan`, `apply` and `register` offer the same when that is the only problem. To move crewmates that already exist in Crew to another workspace: `crewctl workspace assign my-project python-agent go-agent rust-agent` (or `--all`). It creates the workspace if needed, edits the crewmates' files and updates their Crew records.

## The orchestrator and the monitor loop

There is one orchestrator, on purpose: with several specialists, one crewmate has to decide who works, give each a self-contained brief, stop two of them editing the same file, and check what comes back. You talk to `orchestrator-agent` for anything that needs more than one specialist. Crew's own routing still works for single-specialist requests.

**What Crew does by default.** `spawn_run` returns at once. Its reply tells the caller to end its turn and not to poll, and the result comes back later as a `[Subagent completion event]`. I did not find a 30-minute wait inside `spawn_run` or the subagent code. The 30-minute figures I found are the `wait` tool's maximum (60 to 1800 seconds) and a 1800-second stage timeout; the other limits are 3 hours per subagent run (`subagent_timeout_secs`, 10800) and 4 hours per chat turn (`chat_turn_timeout_secs`, 14400). Check your own `kirocrew config get` values.

**What the orchestrator does instead.** It uses Crew's built-in **monitor loop** (the `monitor_start` tool, described in Crew's `monitor-loops.md`) and never the `wait` tool. After `spawn_run` it arms one loop on its own conversation: `interval_secs` = the check interval, `max_cycles` = the number of rounds, `max_runtime_secs` = a positive time budget (Crew asks for one), `gate = false` so that every interval wakes it, and a message that says what to check and when to stop. Then it ends its turn, and Crew wakes the same conversation on the interval (the countdown survives your own messages and gateway restarts). Each wake it reads `spawn_status` for the unfinished runs (small output), prints one English progress line (`monitor_inspect` gives the cycle number), steers a run that makes no progress for 2 cycles, gives up on one after 5, and never restarts or duplicates work without asking. When every run is finished and verified (including the checklist boxes) it writes the final report in Vietnamese and calls `autonudge_stop` (Crew's way to end a `monitor_start` loop). `monitor_update` changes the interval or the cap without losing the cycle count.

**Settings.** Defaults at the top of `prompts/orchestrator-agent.md`: `CHECK_EVERY_SECONDS = 120` and `MAX_ROUNDS = 60` (2 hours). Override them per request ("check every 30 seconds, 100 rounds"). Crew accepts an interval of 15 to 86400 seconds and up to 1000 cycles; a monitor loop also has a wall-clock budget of 4 hours by default, so seconds times rounds is capped at 14400 unless you ask for a longer budget on purpose. Reaching the cap is a backstop, not a finish: the orchestrator tells you which runs are still going.

**Cost.** Every cycle is a full turn in the orchestrator's conversation, with its whole context (about 39k tokens in the Crew session I measured), so 60 cycles are 60 such turns. Do not use a short interval for a long job. Crew's default of ending the turn and waiting for the completion event costs nothing while waiting; use the loop when you want progress lines and stall handling.

**Why the `wait` tool is switched off, not just left out.** Crew's own general instructions tell every agent: "short task (under 30 minutes): use wait and poll in the current session". The orchestrator's prompt says that rule does not apply to it, and every agent file also lists `wait` under `disabledTools` (as well as leaving it out of the tools it selects), so the tool cannot be used even on a backend that mounts all of Crew's tools. If you still see `wait` calls, see the check list in `SETUP.md` ("If it still waits").

**Where it works.** Crew arms monitor loops in dashboard chat, Slack threads, Discord DMs and Webex. It refuses them in a cron job, a webhook session or a subagent, and while another loop is active in the same conversation. If `monitor_start` is refused, the orchestrator tells you why and falls back to Crew's default (end the turn; the completion event wakes it). It has no shell tool and no script for this.

**Not tested.** I read the loop's behavior from Crew's installed docs and tool descriptions and tested that the orchestrator's files are consistent with them, but I did not run a loop in a live Crew session. Unknowns: how `gate = false` behaves on your install, whether the wake message arrives in the same quality as a user turn, and how the cycle counter reads in `monitor_inspect`. Run one short job (a 30-second interval, 3 rounds) before trusting it with long work.

## Inspector setup

`collect_evidence.py` reads three environment variables: `VICTORIALOGS_URL`, `VICTORIAMETRICS_URL`, `TEMPO_URL` (for example `http://victorialogs:9428`), and optional `VICTORIALOGS_TOKEN`, `VICTORIAMETRICS_TOKEN`, `TEMPO_TOKEN`. Export them in the environment Crew runs the agent from. Do not put tokens in any config file. The script prints only summaries (signature counts, min/max per series, span lists) and masks credential-looking text.

## What is not verified

- No model was called. I have not run any of the ten crewmates, so I do not know how well each prompt performs; expect to tune the prompts after a few real tasks.
- The scripts are tested with Python's standard library only. `collect_evidence.py` was tested against local fake servers that imitate the VictoriaLogs, VictoriaMetrics and Tempo HTTP APIs as documented, not against real ones. Tempo's response shape differs between versions; the script reads both common shapes, but check it on your Tempo before relying on it.
- Whether Crew applies per-agent skill lists, or exposes every installed skill through its skill search, I did not verify. Both work; the lists keep the intent clear.
- Version-specific statements in the Python, Go and Rust references (for example about Python 3.14 and free-threading) come from my knowledge, not from a live check. The prompts tell the agents to verify against the version in use.
- Tool restrictions come from each agent's `tools` list. `inspector-agent` has a shell tool so it can run the collector; its prompt says read-only, but that is an instruction, not a technical lock. Approval prompts remain the safeguard.
