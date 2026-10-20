# Skills for your crew (DevSecOps and LLM work)

Read from Kiro Crew 0.8.0's installed files on 2026-10-19. Nothing here called a model.

## 1. Two kinds of skill

| | Kiro skill | Crew skill |
|---|---|---|
| Where | a folder in your repo, `.kiro/skills/<name>/SKILL.md` (or `~/.kiro/skills/`) | `~/.kiro/crew/skills/<name>/SKILL.md` |
| Who gets it | only agents that list it (crewctl `skills`) | the whole crew, found through `skill_search` and by trigger words |
| Frontmatter | `name`, `description` | `name`, `description`, `triggers`, optional `always`, `repo_scope`, `inject_on_trigger`, `pinned` |
| Made by | you (file in git) | you in the dashboard Skills tab, or Crew's `crystallize` skill, or Crew itself after a session |

Same file format for both: a `SKILL.md` with a `---` frontmatter block, then markdown. One file can serve both if it has `name`, `description` and `triggers` (Kiro ignoring `triggers` is expected from its docs, not tested here).

**Recommendation for your setup:** keep your DevSecOps and LLM skills in the application repo under `.kiro/skills/`, list them per agent in crewctl, and let Crew's built-in skills stay as they are. They are versioned with the code, reviewed in pull requests, and each agent only carries what it needs.

## 2. What ships with Crew (35 built-in, plus 3 from apps)

**Useful for you**

| Skill | What it does | Note |
|---|---|---|
| `security-conductor` | Runs a vulnerability audit on one target as a supervised fleet: auditors find, verifiers re-check each finding, fixers patch, two human gates. Scope comes from a rules-of-engagement file. | Only for targets you are authorised to test. Spawns many agents, so it is credit-heavy. Read its `rules-of-engagement.json` first. |
| `ops-mission-control` | Triage alarms, pages and monitors using an incident board and a knowledge ledger. | Closest to your observability work. |
| `five-whys` | Guided root-cause style questioning. | Cheap, no tools. |
| `cron-cost-optimize` | Audits your scheduled jobs and moves them to zero-token or minimal-context modes. | Run it after you add cron jobs. |
| `goal-conductor` (`goal-ledger-conductor` is a deprecated alias) | Runs a big goal as many sessions tracked in a work ledger. | For long clean-up campaigns. |
| `pipeline-conductor` | Manages an issue/PR pipeline. | |
| `llm-council` | Asks several model families the same question and has a chairman merge the answers. | Several models per question, so expensive. Good for reviewing a design, not for routine work. |
| `blocked-by-policy` | What to do when a Crew safety policy blocks a tool call. | Loaded when a block happens; it tells the agent not to route around the block. |
| `crystallize` | Turns the current session into a reusable skill (as a candidate you approve, or live only if you ask for "live"). | The easiest way to draft a Crew skill. |
| `debug-gateway`, `kirocrew-commands`, `session-summaries` | Crew diagnosis, CLI reference, summary panel. | |
| `web-browse`, `web-verify`, `browser-auth` | Open pages, verify UI changes, log in to sites. | Only if an agent needs a browser. |

**Probably not for you:** `papyrus-*` (LaTeX writing), `pptx-maker`, `theme-pack-authoring`, `widgets`, `artifacts`, `image-authoring`, `frontend-design-workflow`, `ios-simulator-preview`, `computer-use`, `meetings`, `explain-for`, `feature-request`, `kirocrew-dev`, `browser-recording`, `web-preview`. The installed folder also has `artifact-deploy`, `dashboard` and `deploy-web`.

Built-ins are copied into `~/.kiro/crew/skills/` and refreshed on upgrade; do not edit them there. To change one, copy it under a new name.

## 3. Writing a skill

Start from `templates/skill-template/SKILL.md`. Three examples for your role are in `examples/skills/`:

- `iac-security-review`: review Terraform, Kubernetes, Dockerfile and CI changes; read-only.
- `llm-agent-run-review`: review a LangGraph or LLM agent run for loops, wasted tokens, injection in tool output, unsafe tool use.
- `incident-evidence-brief`: build a sourced timeline from logs, metrics and traces.

Rules that matter:

1. **Folder name = `name`.** Lowercase letters, digits and single hyphens, up to 64 characters.
2. **The description is the trigger.** Start with "Use when ..." and name the situation. Up to 1024 characters. A vague description means the skill is never picked, or picked for the wrong job.
3. **`triggers`** (Crew only): 3 to 8 specific phrases, the way a person would ask. Crew uses them only when `skills.max_triggered` is above 0, and the default (your config too) is 0. With 0, the agent sees a summary of each skill at the start and loads one when its description fits, so the description does the work.
4. **Keep it short.** Crew refuses project skills over 24,750 bytes; anything past about 8 KB is context you pay for on every load. Put long reference material in `references/` and link it.
5. **Prose first.** Steps, the exact output shape, and a Gotchas list from real mistakes. Add a script only for a fixed, error-prone chain of commands (Crew's own auto-skills limit scripts to Python under 4 KB).
6. **State the limits** the skill works within: read-only, which tools it needs, when to stop and ask.
7. **`always: true` is expensive:** that skill goes into every turn. Avoid it unless the text is a few lines.
8. **No secrets, ever.** Skills are loaded into prompts and often shared.
9. **Quote a description that contains `: `.** Crew reads it as written, a strict YAML parser rejects the whole file.

Check files before using them:

```bash
python3 -m crewctl lint-skills .kiro/skills        # errors exit 1
```

It checks the name rules, description, triggers, size and obvious credentials. It does not judge whether the instructions are good; test the skill with a real task.

## 4. Generating skills from a template (`crewctl skills`)

You fill in one JSON file per skill. crewctl writes the `SKILL.md` into the right folder, checks it, and remembers what it wrote, the same way it handles crewmates.

```bash
python3 -m crewctl -c ./crew skills new incident-triage   # makes crew/skills/incident-triage.skill.json from the placeholder file
# edit it: description, triggers, steps, output, gotchas
python3 -m crewctl -c ./crew skills plan --show           # shows the SKILL.md it would write, plus lint results
python3 -m crewctl -c ./crew skills apply                 # writes it
python3 -m crewctl -c ./crew skills destroy --target incident-triage
```

The placeholder file is `templates/skill.template.json` (`crewctl init` also puts a copy in `skills/_template.skill.json`). A worked example is `examples/crew/skills/iac-security-review.skill.json`.

| Field | Meaning |
|---|---|
| `name`, `description` | Required. `name` becomes the folder name. |
| `triggers` | List of phrases (Crew only, used only if `skills.max_triggered` > 0). No commas inside one phrase. |
| `destination` | `"crew"` (default): the shared Crew skills folder. `"project"`: your repo's `.kiro/skills`, set by `project_skills_dir` in `crewctl.json` (a relative path starts at the config folder, for example `"../.kiro/skills"`). |
| `title`, `goal`, `when_to_use`, `steps`, `output`, `gotchas` | Fill the sections of the SKILL.md. `steps` is required unless you use `body_file`. |
| `body_file` | A markdown file (inside the config folder) used as the whole body instead of the sections. Put the frontmatter only in the JSON. |
| `files` | `{"references/runbook.md": "docs/runbook.md"}`: extra files copied into the skill folder, scripts included (see section 5). Sources must be inside the config folder. |
| `always`, `repo_scope` | Crew options. Avoid `always`. |

Where it writes: `skills_dir` in `crewctl.json` (default `<crew home>/skills`, which is `~/.kiro/crew/skills`) for `"crew"`, and `project_skills_dir` for `"project"`. The environment variables `CREWCTL_SKILLS_DIR` and `CREWCTL_PROJECT_SKILLS_DIR` override them.

Safety rules, the same as for agents:

- A folder that crewctl has no record of creating **in this config folder** is not overwritten, even with `--force`. The record is kept per config folder (in `.crewctl/skills.json`), so a new or copied config folder starts with no record. If the folder's files are exactly what the config would write, crewctl adopts it without writing anything. If they differ, `crewctl skills apply --adopt` takes it over after saving the old files. Crew's built-in skills (folders that carry a `.builtin-skill-provenance` file) are never adopted, so pick another name for your own.
- If a file crewctl wrote was edited by hand, `apply` stops and shows it. `--force` overwrites it and keeps a copy under `.crewctl/backups/`.
- Removing a spec and running `apply` deletes only the files crewctl wrote.
- `apply` runs the lint first and refuses on errors (bad name, no description, over the size cap, a credential).

**Reload.** There is no reload command and no hook is needed. Crew caches its skill list for about 60 seconds and then re-reads the folder in the background (read from Crew's source, not tested). The dashboard's own Skills tab actions refresh at once; a file written from outside waits for that timer. A new skill should therefore show up within about a minute and a half. If it doesn't, open the Skills tab. I did not use Crew's HTTP API to force a refresh because it needs a gateway token.

To hand a generated skill to one agent only, use `destination: "project"` and list its path in that agent's `skills` (section 6).

## 5. Skills that run scripts

Yes, a skill folder can hold scripts or programs, and Crew's own skills do (`security-conductor/scripts/`, `ios-simulator-preview/scripts/`). What matters is who starts them.

**A skill never runs anything by itself.** The agent reads `SKILL.md`, and a step in it says "run `python3 <skill folder>/scripts/x.py`". The agent then runs that with its shell tool. So:

1. The agent must have a shell tool (`execute_bash`, the `shell` group) in its `tools`, and its approval setting must let the call through. A read-only agent cannot run the script.
2. It is the model that decides to run the step, so it is likely but not guaranteed. Write the step as an instruction with the exact command and say what the exit codes mean.
3. The agent needs the folder path. In a spec, write `{{skill_dir}}` and crewctl fills in the real folder on the machine where you run `skills apply`. This is how Crew's own skills do it (`SKILL_DIR=...` then `python3 "$SKILL_DIR/scripts/..."`).

Declare the files in the spec:

```json
"steps": ["Run `python3 {{skill_dir}}/scripts/changed_iac.py origin/main` and read the grouped file list."],
"files": {
  "scripts/changed_iac.py": "skills/scripts/changed_iac.py",
  "scripts/other.sh": {"source": "skills/scripts/other.sh", "executable": true}
}
```

A file gets the execute permission if the source file has it, or if you set `"executable"` yourself. A working example is `examples/crew/skills/iac-security-review.skill.json` with `examples/crew/skills/scripts/changed_iac.py`. The script only lists changed files by type; the model still reads and judges them.

**When it must run every time, do not rely on the skill.** Two options that do not depend on the model deciding:

- A Crew **hook** on `UserPromptSubmit` (Hooks tab in the dashboard) with a matcher such as `contains: terraform|k8s`. It runs your command on matching messages, and whatever it prints is added to that turn's context. Use absolute paths and set any environment variable it needs inside the command, because hooks get only a small allowlisted environment. Timeout is 1 to 300 seconds.
- A Crew **cron** `command`/`script` job, which runs on a schedule with no model call at all and costs no credits.

A hook reacts to the message, not to the skill being loaded. There is no "run when this skill loads" event.

**Write scripts so an agent can use them:**

- Plain source (Python or shell), small, readable in a review. `lint-skills` warns on binary files, because nobody can see what they do.
- Read-only unless the skill's whole point is to change something. Say so in the SKILL.md.
- Print short, structured output. The agent pays tokens for every line.
- Use distinct exit codes and explain them in the skill (0 ok, 3 nothing to do, 2 tool error).
- No credentials in the script or the skill. Read them from the environment of the tool that already has them.

**Treat a skill with a script as code.** It will run with the agent's rights. Review it like a pull request. Crew checks scripts in skills that it writes itself (Python only, under 4 KB, no network, no destructive commands), but it does not check ones you write, so that review is yours. Whether the script runs inside Crew's sandbox depends on your `agent.sandbox` setting; on this machine the sandbox was unavailable, so I could not test it.

## 6. Giving a skill to an agent

Kiro skill, per agent, in crewctl (`log-analyst.agent.json`):

```json
{
  "skills": [".kiro/skills/incident-evidence-brief/SKILL.md"],
  "tools": ["fs_read", "grep", "glob"]
}
```

Relative paths resolve from the session's working folder, so run the crewmate in the repo that holds `.kiro/skills/`. For a Crew-wide skill, create it in the dashboard Skills tab, or ask a Crew session: "crystallize this as a skill".

## 7. Safety

A skill is instructions the agent follows, so it is a way to steer an agent. Treat skill files like code:

- Write them yourself or review every line before use, including ones from GitHub or skills.sh (Crew has providers for both).
- Crew shows new auto-generated skills as **pending** and waits for your approval. Keep it that way; do not use "live" mode except for skills you wrote.
- A skill should never tell an agent to widen its own permissions, disable a hook, or work around a policy block.
- Give review skills read-only tools. The skill text does not enforce that; the agent's `tools` list does.

## Not verified

- That a `SKILL.md` written by `crewctl skills apply` shows up in Crew within about a minute and a half. The timing comes from Crew's source. Check the Skills tab once on your first run.
- That Kiro ignores unknown frontmatter keys such as `triggers`.
- That an agent really runs the example script when it follows the skill. The script itself I ran on a scratch git repo; the agent step I have not.
- Whether the three example skills trigger well in practice. They are drafts to adjust to your stack.
