# Tools, skills and MCP servers for each agent

Everything here goes in the agent's `*.agent.json`. Run `crewctl plan` any time to see the exact agent file it will write; nothing is written until `apply`.

Checked on 2026-10-16 against Kiro's docs and the Crew code installed on one machine. What I could and couldn't verify is listed at the end.

## The short version

| You want | Field | Example |
|---|---|---|
| Limit what the agent can do | `tools` (required) | `["fs_read", "grep", "glob"]` |
| Let some tools run without asking | `allowed_tools` | `["fs_read"]` |
| Give it an MCP server (an outside tool) | `mcp_servers`, or `mcp_json_servers` for one already in `~/.kiro/settings/mcp.json` | see section 3 |
| Let it delegate to other crewmates, remember things, schedule | `crew_servers` | `["core", "cron"]` |
| Give it a skill (a written procedure) | `skills` | `[".kiro/skills/log-triage/SKILL.md"]` |
| Fine-grained shell and file rules | `permissions` | see section 1 |

Start with the smallest set. An agent can only do what its `tools` list names.

## 1. Tools

`tools` is an allowlist. There is no default; `[]` means no tools at all.

**Built-in tools** you can name (these are the ones Crew's own agents use):

| Tool | What it does |
|---|---|
| `fs_read`, `grep`, `glob` | Read files, search text, find files. Safe for analysts. |
| `fs_write` | Create and edit files. |
| `execute_bash` | Run shell commands. The most powerful one; restrict it with `permissions`. |
| `code` | Symbol search and code intelligence. |
| `web_fetch`, `web_search` | Read a URL; search the web. |
| `introspect`, `session`, `report`, `tool_search` | Kiro/Crew helpers. |

Kiro's docs also list `subagent`, `delegate`, `knowledge`, `thinking` and `todo`, plus the wildcards `@builtin`, `@mcp` and `*`. Kiro groups tools with short tags, which are also used by hook matchers:

| Tag | Tools |
|---|---|
| `read` | `fs_read`, `grep`, `glob`, `code`, `introspect`, `tool_search` |
| `write` | `fs_write`, `code` |
| `shell` | `execute_bash` |
| `web` | `web_fetch`, `web_search` |
| `subagent` | `use_subagent` |

Note that `code` is in both `read` and `write`, so the `read` tag is wider than just file reads. Writing the exact names is clearer.

**MCP tools** are named `@server` (all tools from that server) or `@server/tool` (one tool).

Rules of thumb:

- Analysts that only read: `["fs_read", "grep", "glob"]`.
- A reviewer that needs git: add `execute_bash`, then allow only the commands it needs:

```json
"tools": ["fs_read", "grep", "glob", "execute_bash"],
"permissions": { "rules": [
  { "capability": "shell", "match": ["git log*", "git show*", "git diff*"], "effect": "allow" },
  { "capability": "shell", "match": ["git push*", "rm *"], "effect": "deny" },
  { "capability": "fs_write", "effect": "deny" }
]}
```

  `permissions` needs kiro-cli 2.23.0 or newer. Deny always beats allow. `crewctl` checks the rule format but not your kiro-cli version.
- `"*"` allows every tool including write and shell. crewctl warns; avoid it.
- `allowed_tools` is a convenience for tools that should not ask each time. It does not grant a tool the agent doesn't have in `tools`.
- Crew has its own approval modes and a list of denied commands on top of this (Settings -> Security).

## 2. Skills

A skill is a folder with a `SKILL.md`: a written procedure the agent loads when it is relevant. Only the name and description are read up front; the full text loads when the request matches.

```
your-app-repo/.kiro/skills/log-triage/SKILL.md
```

```markdown
---
name: log-triage
description: How to group error logs into signatures and compare an incident window with a baseline. Use when analyzing log files.
---
1. Strip numbers, IDs and timestamps to get a signature...
```

Rules from Kiro's docs: `name` must equal the folder name (lowercase letters, digits, hyphens, up to 64 characters). `description` is required, up to 1024 characters, and it is what decides when the skill is used. A folder may also have `scripts/`, `references/` and `assets/`.

Give an agent its skills with `skills`. Each item becomes a `skill://` entry in the agent file's `resources`:

```json
"skills": [".kiro/skills/log-triage/SKILL.md", ".kiro/skills/*/SKILL.md"]
```

- A specific path gives that agent exactly that skill; a glob (`*`) gives it all of them. Different agents can list different skills, which is how you keep each one focused.
- Custom agents do **not** load skills on their own; if it isn't listed, the agent doesn't have it.
- `~/` works for your home folder (`~/.kiro/skills/...`), which Kiro's docs describe.
- Keep skills in the **application repo** that the crew works on, so they are versioned with the code. A relative path like `.kiro/skills/...` is resolved from the workspace the session runs in.

You don't need to wire up any server for skills. When an agent has `skill://` resources, Crew itself removes them from Kiro's native start-up and adds a `skill_search` tool from its own server, so a large skill library doesn't slow every start. (Seen in Crew's code; it applies to the kiro-cli backend in the version I read.)

**Not the same thing:** Crew has its own skills (`~/.kiro/crew/skills/`, with `triggers`), which Crew learns or you add in the dashboard. Those belong to Crew as a whole, not to one agent. Use `skills` here for per-agent skills.

## 3. MCP servers

Add servers to one agent in `mcp_servers`, then name them in `tools`:

```json
"tools": ["fs_read", "@metrics/query", "@metrics/query_range"],
"mcp_servers": {
  "metrics": {
    "command": "metrics-mcp-server",
    "args": ["--read-only"],
    "env": { "METRICS_URL": "http://localhost:8428", "TOKEN": "${METRICS_TOKEN}" },
    "disabledTools": ["delete_series"]
  },
  "gitlab": {
    "url": "https://gitlab.example.com/api/v4/mcp",
    "headers": { "Authorization": "Bearer ${GITLAB_TOKEN}" }
  }
}
```

- A server is either local (`command`, `args`, `env`) or remote (`url`, `headers`). Exactly one of `command` or `url`.
- **Never write a token into the file.** Use `${NAME}` and set the variable in the environment of the machine. crewctl rejects text that looks like a real credential. Kiro only expands variables you have approved in its "Mcp Approved Env Vars" setting.
- `disabledTools` hides tools you don't want this agent to see, and is the safest way to make a powerful server read-only. `autoApprove` and `timeout` also pass through.
### Using a server that is already in `mcp.json`: `mcp_json_servers`

If the server is configured in your own `~/.kiro/settings/mcp.json` (the file Kiro's MCP settings edit), name it instead of repeating its definition:

```json
"mcp_json_servers": ["fetch"]
```
or only some of its tools:
```json
"mcp_json_servers": { "victorialogs": { "tools": ["query", "hits"] } }
```
crewctl checks that each name exists in the file and is not `disabled`, sets `includeMcpJson` to true, and adds `@fetch` (or `@victorialogs/query`, ...) to the agent's `tools`. **It copies nothing:** the command, URL, environment and tokens stay in `mcp.json`, and Kiro reads them from there when the agent starts. Two things to know: `includeMcpJson` makes Kiro load every server in that file for this agent, but the agent can only use the tools its `tools` list names; and a tool you want to run without asking must also be listed in `allowed_tools`.

Two commands help you choose:
```bash
crewctl mcp list                 # the servers in mcp.json (kind, enabled or disabled) and which crewmates use them
crewctl mcp tools victorialogs   # starts or calls that one server, as Kiro would, and prints its tool names
```
`mcp tools` shows the exact command it is about to run and asks first (or `-y`). It prints no environment values, and it stops the server again as soon as it has the tool list. Use a different file with `"mcp_json"` in `crewctl.json` or `CREWCTL_MCP_JSON`. Only the global file is read, not a workspace `.kiro/settings/mcp.json`.

- `"include_mcp_json": true` also loads every server from your global `~/.kiro/settings/mcp.json` and workspace `.kiro/settings/mcp.json`. The default is `false`, so the agent only gets what you list. Leave it off unless you want that.
- If a server of the same name is defined in several places, the agent file wins.
- The examples above use made-up server names. Use the real command for your servers.

## 4. Crew's own servers: `crew_servers`

Crew ships two servers that give an agent Crew abilities:

| Server | Short name | What it gives |
|---|---|---|
| `kirocrew-core` | `core` | Spawn and manage other crewmates (`spawn_run`, `select_crew`, `route_crew`), send messages, remember and recall (`learn_add`, `memory_recall`), run a task spec (`task_run`), workflows, knowledge search, `skill_search`. |
| `kirocrew-cron` | `cron` | Schedules: `cron_add`, `cron_list`, `cron_pause`, and so on. |

A plain agent file doesn't have them. **An agent that must hand work to other crewmates needs `core`**; a conductor is the usual case.

```json
"crew_servers": ["core"]
```

crewctl adds the `kirocrew-core` server and the `@kirocrew-core` tool for you. It looks up the right command on the machine it runs on (in `~/.kiro/agents/kirocrew.json`, which Crew writes), so no install path goes into your repo and it keeps working after Crew moves or updates. Only `command` and `args` are copied. If that file has no `kirocrew-core` entry on a machine, run `python3 -m crewctl crew-servers`; it explains the usual causes and prints a `crew_server_commands` block for `crewctl.json` that supplies the command by hand.

To keep a powerful agent in check, hide tools it doesn't need:

```json
"crew_servers": { "core": { "disabledTools": ["task_run", "workflow_run", "cron_remove"] } }
```

Crew does not allow auto-approving its own tools, so `autoApprove` is refused here.

Give `core` only to agents that need it. A reader like a log analyst should not be able to spawn other agents or write memory.

## 5. Hooks, and cheap readers

To save credits by sending reads to a cheap agent, see [saving-credits.md](saving-credits.md). Short version: a hook can block a tool call but cannot swap in another agent's answer; removing the read tools from the expensive agent and having it spawn a cheap reader is the dependable way.

## 6. A worked example

```json
{
  "name": "conductor",
  "model": "claude-sonnet-4.5",
  "paid_model": "claude-opus-5.5",
  "prompt_file": "prompts/conductor.md",
  "tools": ["fs_read", "grep"],
  "crew_servers": { "core": { "disabledTools": ["workflow_run"] } },
  "skills": [".kiro/skills/incident-brief/SKILL.md"]
}
```

```json
{
  "name": "metrics-analyst",
  "model": "deepseek-3.2",
  "prompt_file": "prompts/metrics-analyst.md",
  "tools": ["fs_read", "@metrics"],
  "mcp_servers": { "metrics": { "command": "metrics-mcp-server", "args": ["--read-only"], "env": { "TOKEN": "${METRICS_TOKEN}" } } },
  "skills": [".kiro/skills/promql-patterns/SKILL.md"]
}
```

## What I verified, and what I didn't

Verified, by reading Crew's installed code or Kiro's docs:

- The tool names in section 1 (from Crew's own agent files) and the `kirocrew-core` / `kirocrew-cron` tool lists in section 4.
- The tool groups in section 1 (Kiro's hooks page), the `skill://` forms, the SKILL.md rules, and the MCP server fields and `${VAR}` syntax (Kiro docs).
- Crew's skill handling: it strips `skill://` resources and mounts a `skill_search` tool (Crew code).
- That crewctl finds and copies the real `kirocrew-core` and `kirocrew-cron` commands (a read-only run on one machine).

Not verified. I did not run Kiro, so treat these as things to try with one agent first:

- That a custom crewmate under the kiro-cli backend really needs `crew_servers` for spawning. Crew's own agent files list these servers explicitly, so I assumed so.
- That a relative `skill://.kiro/skills/...` resolves from the session's workspace. The docs only show that form in an example.
- That `${VAR}` expansion works in sessions that Crew launches (Kiro's docs describe it for Kiro itself).

Suggested first test: make one agent with one skill and `crew_servers: ["core"]`, run `crewctl plan`, apply, then ask it in Crew to name the skills and Crew tools it has.

## Sources

- [Kiro: MCP configuration](https://kiro.dev/docs/mcp/configuration/), [Agent skills](https://kiro.dev/docs/skills/), [Custom agent configuration reference](https://kiro.dev/docs/custom-agents/configuration-reference/), [Built-in tools](https://kiro.dev/docs/reference/built-in-tools/)
- Crew's installed source, read only: `agent.py` (managed MCP servers), `acp/skill_projection.py` (skill handling), `mcp_tools/*.py` (tool names), and `~/.kiro/agents/kirocrew.json`.
