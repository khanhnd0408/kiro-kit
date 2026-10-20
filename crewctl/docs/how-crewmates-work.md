# How a Kiro Crew crewmate is made

Checked on 2026-10-16 against Kiro's docs and the Crew code installed on this machine. I read the code; I did not run `kiro-cli` or `kirocrew`.

## The short version

A crewmate is **two things that point at each other**:

1. **A Kiro agent file**, `~/.kiro/agents/<kiro_agent>.json`. It holds what the agent *is*: prompt, tools, model, MCP servers.
2. **A crew record**, an entry under `agents` in Crew's `~/.kiro/crew/config.json`. It holds what the crewmate *is in Crew*: which agent file it boots, which workspace it works in, its display name, and when the orchestrator should pick it.

The record's `kiro_agent` field is the link. Crew also gives every crewmate its own private memory store when the record is created.

```
config.json  ->  agents.log-analyst  ->  kiro_agent: "log-analyst"  ->  ~/.kiro/agents/log-analyst.json
                                          workspace: "default"             prompt, tools, model
                                          memory_store: (private, automatic)
```

## 1. The agent file

This is a plain JSON file. These are Crew's own small ones, from your machine:

```json
{ "name": "kirocrew-lite", "model": "auto", "tools": [], "mcpServers": {}, "prompt": "" }
```

The fields crewctl writes:

| Field | Meaning |
|---|---|
| `name` | Same as the file name without `.json`. |
| `description` | One line. |
| `model` | A model id, or `auto`. See `docs/model-notes.md`. |
| `prompt` | The system prompt. Crew's docs also allow `file://` here. crewctl writes the text itself. |
| `tools` | The tools the agent may use. `[]` means none. |
| `allowedTools` | Tools that run without asking. |
| `mcpServers` | MCP servers this agent starts. |
| `includeMcpJson` | Whether to also load servers from your global `mcp.json`. |
| `resources` | Files or skills loaded into context (`file://...`, `skill://...`). |
| `permissions` | Allow, ask or deny rules. Needs kiro-cli 2.23.0 or newer. |

Rules that come from Crew's source:

- The file name must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,61}[A-Za-z0-9]` (max 63 characters).
- Names starting with `kirocrew` belong to Crew, which rewrites its own files. Anything else is left alone. Crew's cleanup job only removes temp and backup files.
- Kiro reserves `default`, `vibe`, `spec`, `quick-spec`, `bug-fix`, `plan` and `autonomous`. An agent with one of those ids never runs as written.
- Crew's dashboard "fork a template" writes a private copy under the crewmate's name in the same folder. Don't use the same file name for a crewmate you also fork in the dashboard.

## 2. The crew record

The record has these fields (from Crew's `KiroCrewAgentConfig`):

| Field | Meaning |
|---|---|
| `kiro_agent` | The agent file to boot. Required when you create a crewmate. |
| `workspace` | A named workspace from the `workspaces` section. Default `default`. Crew silently falls back to the default workspace when the name does not exist, so crewctl treats an unknown workspace as an error and offers `crewctl workspace check` to create it, and `crewctl workspace assign WORKSPACE CREWMATE...` to move existing crewmates (`kirocrew agent update NAME --workspace W` for each) (`kirocrew workspace create --name N --dir FOLDER [--copy-from EXISTING]`; the folder is relative to `~/.kiro/crew`, and `--copy-from` copies the files of the existing workspace's folder). |
| `memory_store` | Created automatically for each new crewmate. It cannot be changed or shared later. |
| `member_id` | Assigned by Crew. Never edit it. |
| `display_name` | The label the dashboard shows. The `name` stays the real identity. |
| `description` | Human-readable text. |
| `triggers` | Free text: "when to use this crewmate". Crew's orchestrator reads it to choose. **A crewmate with no triggers is not offered for selection.** |
| `model` | Optional pin. Empty means inherit the agent file's model. |
| `reasoning_effort` | Empty, or `low`, `medium`, `high`, `xhigh`, `max`. |
| `session_color`, `starred`, `avatar` | Presentation. |

The crewmate's **name** is the record's key. It can be any single line of text up to 500 characters (no tabs, no hidden characters, not only dots). `default` is taken by Crew's built-in first crewmate.

### Which model wins

From the record's own help text: the crew record's `model`, then the agent file's pinned `model`, then the global `agent.model`, then `auto`. A model picked for one session overrides all of them. crewctl pins the model in the agent file and leaves the record's `model` empty, so there is one place to change it.

## 3. Three ways to make a crewmate

| Way | What it sets | Notes |
|---|---|---|
| Dashboard: **Agent Capabilities -> Agents**, or **Agent templates -> Enroll as crewmate** | Everything | Turn on **Settings -> Developer -> Feature Previews -> Crew Members** first. |
| `kirocrew agent create --name N --kiro-agent K --workspace W` | Only the name, the agent file and the workspace | Creates the private memory store too. Also: `agent list`, `agent update`, `agent delete`, `workspace create`. |
| `POST /api/agents` on the local gateway | Every field above | Needs the gateway running and an access token. crewctl does not use it. |

`kirocrew agent create` cannot set `display_name`, `description` or `triggers`, but `kirocrew config set agents.<name>.description '"text"'` can (Crew's own CLI, no token; the value must be JSON-quoted or text like `123` or `true` is stored as a number or boolean). crewctl uses that, so `register --run` leaves the record filled in.

## 4. What crewctl does about all this

crewctl manages the **agent files**, because those are where your prompts, tools and models live, and they are plain files. It treats the crew records differently on purpose:

- Creating a record also creates the crewmate's private memory. Crew does that in its own code, under its own lock. Writing `config.json` by hand would skip it and could race with the running gateway.
- So crewctl only **reads** Crew's `config.json` (the `agents` and `workspaces` sections, never the tokens next to them) and works out which `kirocrew` commands are still needed. It prints them, or runs them if you ask (`crewctl register --run`).
- crewctl never starts `kiro-cli`, the gateway, or a model.

## 5. Not verified

I read code and docs but ran nothing, so these are open:

- Whether Crew picks up an agent file edited on disk immediately, or only at the next session. I found no clear statement. After `crewctl apply`, start a new chat session; if you still see the old behavior, restart the gateway.
- Whether kiro-cli rejects unknown keys in an agent file. crewctl only writes keys that Crew's own files use, plus anything you add under `extra_agent_fields`.
- The exact `kiro-cli` version on your machine. `permissions` blocks need 2.23.0 or newer.
- The spellings of the Claude 5.5 model ids (see `docs/model-notes.md`).

## Sources

- [Crew: agents](https://kiro.dev/docs/crew/capabilities/agents/), [agent templates](https://kiro.dev/docs/crew/capabilities/agent-templates/), [CLI reference](https://kiro.dev/docs/crew/interfaces/cli-reference/), [configuration](https://kiro.dev/docs/crew/configuration/)
- [Crew 0.8 release notes](https://kiro.dev/changelog/crew/0-8/) (templates, teams, enrolling a crewmate)
- [Custom agent configuration reference](https://kiro.dev/docs/custom-agents/configuration-reference/)
- Kiro Crew issue 10041 (permissions need kiro-cli 2.23.0 or newer)
- Crew's installed source, read only: `config/sections.py` (record fields), `cli.py` and `cli_commands.py` (agent create/update/delete), `members.py` (name rules), `agent_files.py` (owned and reserved names), `agents_janitor.py`, `dashboard/handlers/agents.py` (create route).

## Capabilities and inheritance (read from Crew 0.8.0's source)

A crewmate's **description**, **triggers**, **display name**, model and reasoning effort live in its Crew record. Its **capabilities** (tools, allowed tools, MCP servers, skills, prompt, model, resources) live in its agent file. The Capabilities page shows the second group.

Crew has two modes for that page, decided per crewmate:

| Mode | When | What the page shows |
|---|---|---|
| `shared` | The crewmate's template is an ordinary file in `~/.kiro/agents/` and nobody enrolled it. This is what crewctl creates. | Every section read from the file, each marked inherited. No parent, so "forked from" is empty. |
| `inherited` | You enrolled the crewmate on the Capabilities page. Crew makes a private copy of the template for that crewmate, records which template it was copied from, and keeps a saved "intent" with your reviewed changes on top. | Rows marked inherited, local or removed, relative to the parent template. |

What enrolling changes, from `agent_capabilities.py` and `agent_state.py`:
- Crew records the lineage (`forked_from`, `private_to`) and the reviewed state in `~/.kiro/crew/agent_model_state.json`, not in the agent file.
- If the private copy is later changed outside the Capabilities page, Crew refuses to start new chats for that crewmate ("agent file changed outside the Capabilities page") until the owner reviews and saves. If the parent template changes, the page shows the change for review.
- Older write paths are refused for an enrolled template (`capabilities_editor_required`).

For crewctl this means: do not enroll a crewmate whose agent file crewctl keeps rewriting, unless you accept reviewing each change on the page. crewctl cannot enroll for you. The Capabilities service needs a preview step that the owner confirms, and that is by design. Skills show on the page only if they are installed (the page matches the `skill://~/.kiro/crew/skills/<name>/SKILL.md` entries against the installed skills).
