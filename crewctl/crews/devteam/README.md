# devteam: ten crewmates for development, security and operations

A ready-to-apply crewctl config. Nothing here is applied until you run it on the machine where Crew is installed. The default `plan` is `free`; change it to `paid` in `crewctl.json` on the licensed machine and every crewmate switches to its `paid_model`.

| Crewmate | What it is for | Free model | Paid model | Can write / run |
|---|---|---|---|---|
| `orchestrator-agent` | The one coordinator. Picks who does what, briefs them, supervises long-running subagents with a timed check loop, verifies results. Read-only. | claude-sonnet-4.5 | claude-sonnet-5.5 | read only; spawns and watches the others |
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
cd crewctl
python3 -m pip install -e .                 # once: gives you the short `crewctl` command
export CREWCTL_DIR="$PWD/crews/devteam"    # the team folder, so you do not repeat -c
crewctl validate
crewctl skills plan          # shows the SKILL.md files
crewctl skills apply         # writes them to ~/.kiro/crew/skills
crewctl plan                 # shows the ten agent files
crewctl apply
crewctl register             # prints the kirocrew commands; add --run to execute them
```

`register --run` also fills in each crewmate's display name, description and triggers. To remove everything: `skills destroy`, `destroy --delete-records`.

The agent files point at skills in `~/.kiro/crew/skills/`. If you set another `skills_dir`, edit the paths in the `skills` lists.

## The orchestrator and the check loop

There is one orchestrator, on purpose: with several specialists, one crewmate has to decide who works, give each a self-contained brief, stop two of them editing the same file, and check what comes back. You talk to `orchestrator-agent` for anything that needs more than one specialist. Crew's own routing still works for single-specialist requests.

**What Crew does by default.** `spawn_run` returns at once. Its reply tells the caller to end its turn and not to poll, and the result comes back later as a `[Subagent completion event]`. I did not find a 30-minute wait inside `spawn_run` or the subagent code. The 30-minute figures I found are the `wait` tool's maximum (60 to 1800 seconds) and a 1800-second stage timeout; the other limits are 3 hours per subagent run (`subagent_timeout_secs`, 10800) and 4 hours per chat turn (`chat_turn_timeout_secs`, 14400). Check your own `kirocrew config get` values.

**What the orchestrator does instead.** Its prompt overrides the "end your turn" advice and runs a loop: `spawn_run`, then up to `MAX_ROUNDS` times: `wait` for `CHECK_EVERY_SECONDS`, then `spawn_status` (small output) to see elapsed time, turns and the last tool, and one status line to you. It counts rounds by putting "round r of N" into the `wait` reason, which the tool echoes back, so the count survives. It steers a run that makes no progress for 2 rounds, gives up waiting after 5 and tells you, and never restarts or duplicates work without asking. When a run finishes it checks the result (including the checklist boxes) before reporting.

**Settings.** Defaults are 300 seconds and 24 rounds (2 hours), at the top of `prompts/orchestrator-agent.md`. Override them per request ("check every 120 seconds, 30 rounds"). The seconds must be 60 to 1800, and seconds times rounds is capped at 14400.

**Cost.** Every round is a model turn with the orchestrator's context (about 39k tokens in the Crew session I measured), so a 24-round loop is 24 or more such turns. Do not poll faster than the job needs. Crew's default of ending the turn costs nothing while waiting; use the loop when you need progress lines, stall handling, or the default misbehaves for you. For long loops, `claude-haiku-4.5` as the orchestrator's `paid_model` is cheaper; I kept Sonnet because coordination and verification are judgment tasks.

**Not tested.** I did not run the loop. Unknowns: whether a completion event ends a `wait` early, how `spawn_status` behaves for a run that finished between checks, and whether your install still ends the turn despite the prompt. Run one short job (a 60-second wait, 3 rounds) before trusting it with long work. The tools the loop uses (`wait`, `spawn_status`, `spawn_run` and the rest) are pre-approved in `allowed_tools` so the loop never stops on an approval prompt; remove them from that list if you want to approve each spawn.

## Inspector setup

`collect_evidence.py` reads three environment variables: `VICTORIALOGS_URL`, `VICTORIAMETRICS_URL`, `TEMPO_URL` (for example `http://victorialogs:9428`), and optional `VICTORIALOGS_TOKEN`, `VICTORIAMETRICS_TOKEN`, `TEMPO_TOKEN`. Export them in the environment Crew runs the agent from. Do not put tokens in any config file. The script prints only summaries (signature counts, min/max per series, span lists) and masks credential-looking text.

## What is not verified

- No model was called. I have not run any of the ten crewmates, so I do not know how well each prompt performs; expect to tune the prompts after a few real tasks.
- The scripts are tested with Python's standard library only. `collect_evidence.py` was tested against local fake servers that imitate the VictoriaLogs, VictoriaMetrics and Tempo HTTP APIs as documented, not against real ones. Tempo's response shape differs between versions; the script reads both common shapes, but check it on your Tempo before relying on it.
- Whether Crew applies per-agent skill lists, or exposes every installed skill through its skill search, I did not verify. Both work; the lists keep the intent clear.
- Version-specific statements in the Python, Go and Rust references (for example about Python 3.14 and free-threading) come from my knowledge, not from a live check. The prompts tell the agents to verify against the version in use.
- Tool restrictions come from each agent's `tools` list. `inspector-agent` has a shell tool so it can run the collector; its prompt says read-only, but that is an instruction, not a technical lock. Approval prompts remain the safeguard.
