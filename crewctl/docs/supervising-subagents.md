# Supervising subagents without burning credits

What I found in Kiro Crew 0.8.0's installed source and docs about why the main agent can stop waiting, what a check really
costs, and how to loop only when it is worth it. Everything below is read from Crew's files on 2026-10-22; nothing was run
against your machine's stuck session, so the causes are ranked by evidence, with a way to confirm each one.

## 1. How Crew normally waits (and why that is the cheap path)

`spawn_run` returns at once. When a run finishes, Crew injects a `[Subagent completion event]` into the parent conversation
and the parent takes a new turn. While nothing happens the parent is idle and costs **zero credits**. This is the same
push model Claude Code uses (the lead sits idle until a teammate's message arrives). A timed loop (`monitor_start`) is the
opposite: Crew re-injects your message every interval and each cycle is a **full turn with the whole context**. Your numbers
(60 checks, up to 100 credits, about 1.7 per check) are what that costs: model multiplier times context size, every time.

So the first rule: **do not loop by default.** Loop only for a reason (section 4).

## 2. Why the main agent can sit for about 30 minutes and then need a full restart

No Crew setting is a plain "30 minute stop for a waiting parent". These are the candidates, strongest first.

| # | Cause | Why it fits | Where it comes from |
|---|---|---|---|
| 1 | **The subagent never started: it was queued.** `spawn_run` accepts a run and says "queued" when memory is low, the posture is critical, or the concurrency cap is reached. On **macOS**, while the memory-pressure level is WARN or CRITICAL and another dedicated subagent runs, a start waits, and after **30 minutes** it is ended with `never started: waiting for memory`. | The 30 minutes match exactly. A restart of Crew frees the memory and the queue, which is why a restart "fixes" it. The parent was told the run was spawned and waits for an event that can never come. | `docs/subagents.md` (Limits), `docs/dynamic-subagent-sizing.md`, `subagent_wait_reasons.py` (`MEMORY_PRESSURE_NEVER_STARTED`), floor `agent.spawn_min_memory_gb` (2.0 here) |
| 2 | **A run waits for spawn approval** (`awaiting-approval`) and nobody approves it. | It looks like "no response"; `spawn_list` shows it as `awaiting-approval`. | `mcp_tools/spawn.py` (`spawn_list`, `spawn_status`) |
| 3 | **The completion event was not delivered.** The parent turn that handles the last completion has a cap of 900 s (`KIROCREW_INJECTION_TIMEOUT`), the delivery as a whole 20 minutes. If the parent is busy or hung when the event arrives, the delivery can time out and be dropped. | A parent stuck in a long tool call never sees the result. | `subagent.py` (`_ON_DONE_TIMEOUT` 1200, `_DEFAULT_INJECTION_TIMEOUT` 900) |
| 4 | **The parent itself sits in a long blocking call**: the `wait` tool (at most 1800 s = 30 minutes per call) or the blocking `spawn_sub_agents` (up to 2 hours). | `wait` is capped at exactly 30 minutes. A parent stuck there looks "stopped". | `mcp_tools/control.py` (`wait`), `docs/subagents.md` |
| 5 | The recovery ladder gives up. The last layer (L5) is "no automatic action: an event and a notification, the user restarts". | This is why only a restart helps once it is reached. | `recovery/ladder.py` |

### Confirm which one it is (read-only commands)

Run these the next time it happens, **before** you restart:
```bash
kirocrew spawn list                          # a queued run is [running] with an elapsed time and NO turns; 🔐/awaiting-approval = cause 2
grep -iE "never started|waiting for memory|memory pressure" ~/.kiro/crew/gateway.log      # cause 1
grep -iE "injection attempt|injection timed out" ~/.kiro/crew/gateway.log                    # cause 3
kirocrew config get agent.spawn_min_memory_gb ; kirocrew config get agent.max_subagents
memory_pressure | head -3                    # macOS: the system-wide level (and `vm_stat`)
```
If it is cause 1: lower the pressure instead of looping. Close memory-hungry apps, run fewer subagents at once
(`kirocrew config set agent.max_subagents 2`), and treat "queued" as a signal to tell you, not as progress. Do **not** set
`agent.spawn_min_memory_gb` to 0 just to make it start; that removes the protection that keeps the machine responsive.

## 3. Cost of the ways to watch (per job)

Numbers use your measurement, about 1.7 credits for one orchestrator check, as the unit. They are estimates, not a bill.

| Way | Model turns while waiting | Typical cost | Catches |
|---|---|---|---|
| Event-driven only (Crew's default, now the orchestrator's default) | 0 | about 0 | A normal finish. Does **not** catch a run that never starts or a dropped event. |
| Sparse safety loop (600 s apart, at most 12 cycles, one `spawn_list` per cycle, backing off) | one per cycle | 12 x 1.7 = about 20 at worst; about 3 to 5 when it backs off or ends early | Queued or stuck runs, within one interval |
| Dense loop (what you had: 60 checks) | one per cycle | about 100 | The same, 6 times sooner at 5 times the price |
| **Zero-token watchdog** (a Crew script cron; section 5) | 0 | 0 | Awaiting approval, failures, "never started", no progress, too long. It *notifies you*; it does not wake the conversation. |

## 4. When to loop and when not (what the orchestrator now does)

The rule in `prompts/orchestrator-agent.md`:

* **No loop** when every run started, the job should finish within `SHORT_JOB_MINUTES` (20), and you did not ask to keep
  checking. The orchestrator ends its turn and Crew wakes it on completion.
* **Arm the loop** only when a run is **queued** or **awaiting approval**, the job may run longer than 20 minutes, you asked
  to babysit, or it was woken and a run it expected to be done is not.
* A loop is a **safety net**: first check no sooner than 5 minutes for a queued run, then every 10 minutes, at most 12
  cycles, `gate = false`, a positive `max_runtime_secs`. Each cycle calls `spawn_list` **once** (one line per run, with
  elapsed time, turn count and last tool) and answers in one line when all is well. It only reads `spawn_status` if a line
  looks wrong. After two quiet cycles it doubles the interval (up to 30 minutes) with `monitor_update`.
* How it reads a `spawn_list` line: `[done]` finished; `[done] error:` failed; `[awaiting-approval]` needs you;
  `[running]` with an elapsed time **and no turns** for more than 5 minutes is **queued, not working**; `[running]` with
  turns works, and an unchanged turn count for 2 cycles means ask it, for 4 cycles means tell you.

### The Claude Code duplicate-teammate problem

Claude Code's lead sometimes cannot read a teammate's state, so it starts a second teammate while the first still works.
Crew gives the orchestrator a reliable read (`spawn_list`, `spawn_status`) and a durable note (`session_ledger_record`,
`session_ledger_read`, kept across context compaction), so the orchestrator has a hard rule:

* record every run id in the ledger when it is spawned;
* before **any** `spawn_run`, call `spawn_list` and read the ledger;
* a run is dead only if `spawn_list` shows `[done]` with an error, Crew says "never started", or you say so;
* if a status call fails or is unclear, repeat once, then **ask you**; never spawn a replacement while the first may still work.

## 5. The zero-token watchdog (optional)

`crons/watch_runs.py` is a Crew *script cron*: Python run on a schedule, no model. Every tick it reads `spawn_list` through
`ctx.call_tool("kirocrew-core", "spawn_list", {})`, stays silent while runs progress, and sends one short notification only
for: awaiting approval, failed, **never started (queued)**, no progress for several ticks, running too long. It never starts,
stops or changes a run. Install on the machine where Crew runs:
```bash
cp crews/devteam/crons/watch_runs.py ~/.kiro/crew/crons/            # Crew only runs scripts from this folder
kirocrew cron preview ~/.kiro/crew/crons/watch_runs.py:run          # dry run: real spawn_list, notifications printed, not sent
kirocrew cron add "watch-runs" '{}' --every 300 \
  --script ~/.kiro/crew/crons/watch_runs.py:run --no-persistent-session --minimal-context
```
Tune with the job message, for example `'{"never_started_secs": 240, "stall_ticks": 4, "max_minutes": 90}'`. Remove it with
`kirocrew cron list` and `kirocrew cron remove ID`.

## 6. What I did not verify

* I could not reproduce your 30-minute stop. The ranking in section 2 comes from the source and docs; the confirm
  commands tell you which row applies.
* `spawn_list` marks a queued run as `[running]` with no turns. I read that from the tool's code; I did not see a queued
  run on a live gateway. The "no turns for 300 s" test is therefore a heuristic.
* The watchdog was tested with fake contexts and sample lines in the real format. I did not install it as a cron or run
  `kirocrew cron preview` on it, because that needs a file in `~/.kiro/crew/crons/`. Run the preview line above first.
* A notification from a script cron goes to the dashboard (or your channel). I did not verify that it also wakes the
  orchestrator's conversation; assume it only tells **you**.
* The credit figures are estimates from your measurement, not a meter. Watch your usage after the first long job.

## 7. Options I did not build

* A **sentinel crewmate** on a cheap model (for example qwen3-coder-next, 0.05x) that is itself spawned and does the
  polling, so the expensive orchestrator stays idle until the sentinel's completion event. About 100 times cheaper per
  check, but it needs the `wait` tool (at most 25 minutes per call) and I would rather not add that loop; say if you want it.
* Making `crewctl` install and register the cron. It is a one-time copy and a `kirocrew cron add`, so I left it manual.
