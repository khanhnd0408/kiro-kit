# Saving credits: cheap readers, cheap collectors

The idea: an expensive agent (the orchestrator) shouldn't read raw logs itself. A cheap agent reads and returns a short summary, and routine log collection runs on a schedule without a model at all.

This page covers what Crew can and cannot do for that, and the setup I recommend. Checked 2026-10-16 against Kiro's docs and the Crew code installed on one machine. I did not run Kiro, so section 5 lists what to test.

## 1. Can a hook swap a read for a cheap agent? No.

A PreToolUse hook runs a command before a tool call. From the docs, it can **allow** the call or **block** it, and when it blocks, its error text goes to the model. I found nothing that lets a hook replace the tool's result, change its input, or call another agent on the model's behalf.

So a hook can say "don't read that, ask the log reader instead", but it cannot quietly do the swap. It would also cost an extra failed call each time.

Two more reasons not to build on it:

- Crew's own docs say its PreToolUse hook cannot stop a call that Kiro CLI has already approved. Reads like `fs_read` and `grep` are approved by default. Whether the hook even sees them, I don't know.
- A hook that fails or times out blocks every tool call in Crew.

Hooks are still good for guard rails (block `rm`, log every shell command). They are the wrong tool for routing work.

## 2. What works: take the read tools away

An agent can only use the tools in its `tools` list. If the orchestrator has no `fs_read`, `grep` or `glob`, it **cannot** read raw logs. It has to delegate. No hook is needed, and it can't be bypassed.

The four tools Kiro groups as "read" are `fs_read`, `grep`, `glob`, `code` (plus `introspect` and `tool_search`). Leave all of them out.

**The orchestrator** (expensive model, no read tools, can spawn):

```json
{
  "name": "orchestrator",
  "model": "claude-sonnet-4.5",
  "paid_model": "claude-opus-5.5",
  "prompt_file": "prompts/orchestrator.md",
  "tools": [],
  "crew_servers": { "core": { "disabledTools": ["task_run", "workflow_run"] } }
}
```

`crew_servers: core` gives it `spawn_run`, `select_crew` and `send_message`, and crewctl adds the `@kirocrew-core` tool. Its prompt must say, in plain words:

> You cannot read files or logs. To look at any log, file or search result, call `spawn_run` with `agent: "log-reader"` and a precise question. Ask for at most 30 lines, each with a file and line number. Never ask it to paste raw logs.

**The reader** (cheap model, read tools only):

```json
{
  "name": "log-reader",
  "model": "qwen3-coder-next",
  "paid_model": "qwen3-coder-next",
  "prompt_file": "prompts/log-reader.md",
  "tools": ["fs_read", "grep", "glob"]
}
```

Its prompt should enforce the shape of the answer: findings first, at most N lines, file:line for every claim, "none found" is a valid answer. The `log-analyst` example already does this.

Facts from Crew's code and docs that make this work:

- `spawn_run` takes `agent` and `model` overrides, plus `prompt`, `max_turns` and `cwd`.
- A spawn that hands off one task must name a different agent or model, or give a reason such as `bulk_data` or `specialist`. Reading a pile of logs is exactly `bulk_data`.
- A subagent starts with a fresh context and does not see the parent's conversation. The parent receives its report.
- Subagents inherit the session's approval mode and deny rules; you can't set them per subagent.
- A subagent can run up to 3 hours and 1000 tool calls, so give it `max_turns` in the spawn.

## 3. When delegating actually saves credits

Roughly, a credit cost is `tokens x model multiplier`. Reading directly makes the expensive model **pay for the raw output, and then carry it in its context for every later turn**. Delegating makes the cheap model pay for the raw output once, and the expensive model carries only the summary.

Illustration (made-up sizes; the multipliers are from the model notes): a 20,000-token grep result, then 5 more turns on the orchestrator.

| | Orchestrator reads it itself (2.0x) | Cheap reader (0.05x) + summary |
|---|---|---|
| Read the raw output | 20k x 2.0 = 40k | 20k x 0.05 = 1k |
| Start-up context of the spawned session | none | assume 30k x 0.05 = 1.5k |
| Carry it for 5 more turns | 20k x 5 x 2.0 = 200k | summary 500 x 5 x 2.0 = 5k |
| **Total** | **about 240k** | **about 8k** |

That is roughly 30 times cheaper. For a **tiny** read, say 500 tokens, it flips: direct is about 6k, delegated is about 4k plus a slower round trip, so it barely helps.

Rule: delegate **bulk** reads (logs, big diffs, wide searches). Let the orchestrator keep small, cheap lookups if you give it any read tools at all. Prompt caching makes re-reading cheaper than shown, and I don't know how Kiro counts cached tokens. So measure real credits on a few tasks before you trust the table.

## 4. Scheduled collection and summaries

Crew scheduled jobs (cron) can run in three ways:

| Job kind | Model used? | Good for |
|---|---|---|
| `command` (shell) or `script` (Python in `~/.kiro/crew/crons/`) | **None. Zero tokens.** | Fetching logs, running a query, writing a file, checking if anything changed. |
| agent job with `minimal_context` | Yes, on a small context | Summarizing what the collector wrote. |
| plain agent job | Yes, on the full context | Avoid for routine work. |

The warning that matters, from Crew's own cost guide: **a scheduled agent job pays for its whole injected context on every wake**, whether or not there was anything to do. Crew estimates 30,000 to 55,000 tokens for a full wake against about 200 for a minimal one. That is the product's estimate, not a measurement. Crew ships a skill, `cron-cost-optimize`, that audits your jobs for this.

A good shape for your case:

1. **Collector**, a `command` or `script` job every N minutes. It runs the query and writes the logs to a file. Zero tokens. A script can also decide whether anything is worth a summary.
2. **Summarizer**, an agent job on a cheap model, with `minimal_context` on, that reads the file and writes a short summary. Pin the model in the agent's file so you don't repeat it per job:

```json
{
  "name": "log-summarizer",
  "model": "qwen3-coder-next",
  "paid_model": "gpt-5.6-luna",
  "prompt_file": "prompts/log-summarizer.md",
  "tools": ["fs_read"]
}
```

3. The orchestrator reads the one-page summary, never the logs.

A cron job accepts an `agent`, and also a `model` override, so one job can pick a model without a separate agent. Jobs are created from the dashboard Schedule page, from the CLI (`kirocrew cron add`, check `--help` for the flags), or by asking an agent with `cron_add`. crewctl does not manage cron jobs yet.

### Keeping GPT-5.6 Luna under 272K

Luna costs 0.6x up to 272K tokens and 1.2x above. For a summarizer that reads one file, context stays small. But watch for one trap: Crew has a built-in floor of 272K as the window for the GPT-5.6 models, yet the docs say the real window is 1M, and Crew prefers the live model list when it has one. Crew compacts at 70% of whatever window it believes, so if it believes 1M, a long-lived session could grow past 272K and double its cost. Protect yourself:

- Use fresh sessions for scheduled jobs (not `persistent_session`), so the context never builds up.
- On the paid machine, open `~/.kiro/crew/model_windows.json` and look at `gpt-5.6-luna`. If it says 1000000, consider a lower `session.autocompact_pct`, or keep Luna on short jobs only.

## 5. What to test first (nothing here is verified)

1. **Hook probe**, to learn whether hooks see reads at all. Give one cheap agent this hook (match the style of `~/.kiro/agents/kirocrew.json` on that machine, which uses the older object form):
   ```json
   "hooks": { "preToolUse": [ { "matcher": "read", "command": "cat >> /tmp/hook-probe.log" } ] }
   ```
   Ask it to grep something, then read `/tmp/hook-probe.log`. If the file is empty, hooks do not see reads and can't be used to steer them. If it has JSON, you also learn the exact input fields.
2. **Removal test**: apply the orchestrator above and ask it to find an error in a log. It should call `spawn_run` rather than fail.
3. **Cost check**: run the same task with and without delegation and compare credits.
4. **Spawn permissions**: confirm `spawn_run` is allowed from a custom agent (the `crew_servers` assumption in `tools-skills-mcp.md`).

## Sources

- Kiro: [Hook triggers](https://kiro.dev/docs/hooks/types/), [Hook actions](https://kiro.dev/docs/hooks/actions/), [CLI hooks migration](https://kiro.dev/docs/cli/v3/hooks-migration/)
- Crew: [Scheduling](https://kiro.dev/docs/crew/features/cron/), [Subagents](https://kiro.dev/docs/crew/features/subagents/), [Hooks](https://kiro.dev/docs/crew/capabilities/hooks/)
- Crew's installed source, read only: `mcp_tools/spawn.py` (spawn parameters), `mcp_cron.py` (job fields), `builtin_skills/cron-cost-optimize/SKILL.md` (cost guidance), `model_registry.py` (GPT-5.6 windows).

## Measured on a Free-plan machine (Crew 0.8.0, 2026-10-19)

These numbers come from real runs, not estimates. Total spend for the whole test series was under 2.5 of 50 credits.

| What | Result |
|---|---|
| Orchestrator with `tools: []` and only `spawn_run`, `spawn_status`, `spawn_list` | It did not try to read the log. It called `spawn_run` for `ctest-reader` (qwen3-coder-next). |
| Reader answer vs. the log | Correct: 85 payment timeouts, 46 pool exhaustions, 40 `NEW-REGRESSION` lines that appear only in lines 3001-3040. The orchestrator relayed it accurately. |
| Context per turn | Full `kirocrew-core` (83 tools): about 38.6k tokens. With `crew_servers.core.tools` limited to three spawn tools: about 6.8k tokens in a direct CLI run, about 39k inside a Crew session (Crew adds its own prompt). |
| Reader cost driver | It read most of a 3,040-line log into context (about 122k tokens). At 0.05x that is cheap, which is the point: the big read lands on the cheap model. |
| Reader mistakes | It called a tool named `run` twice (does not exist) before switching to `grep`. Name the allowed tools in the reader prompt. |
| preToolUse hook | Fires for read tools. It can block or allow, but cannot swap in a different result. |

Limits you will hit:

- `spawn_run` only works inside a Crew session (dashboard or a channel). From a bare `kiro-cli chat --agent ...` it fails with "execution identity unavailable".
- `kiro-cli chat --no-interactive` cannot approve MCP tools and `--trust-tools` did not cover `@kirocrew-core/...`. Put the tools in the agent's `allowed_tools` so they are pre-approved.
- `claude-sonnet-4.5` shows a rate multiplier of 1.3 in session files.
