# Kiro Crew guide

This guide covers running the agent review in Kiro Crew: the desktop app, the web dashboard, the `kirocrew` CLI, and chat channels such as Slack or Telegram. For the Kiro IDE, see [the IDE guide](../ide/README.md). For an overview of the kit, see [the main README](../../README.md).

Crew has no `#` command for loading steering. Instead, you start every review message with `agent-review:`, and the kit makes Crew pick up the method from that prefix.

## 1. How Crew differs from the IDE, and what the kit does about it

| Crew behavior | What goes wrong if you ignore it | What the kit does |
|---|---|---|
| There's no `#name` command. Crew injects `always` steering into every session, and lists `manual`, `auto` and `fileMatch` steering only as pointers. | `#agent-review` doesn't exist, and a manual launcher may never load. | `.kiro/steering/agent-review.md` is a short always-on pointer. It tells the agent to read the method when your message starts with `agent-review:`, or when it runs one of the kit's Task Runner specs. |
| Each session works in one project directory, set in the dashboard session header, or by the crewmate's workspace folder. | The agent can't see the method if the session points somewhere else. | Setup step 1 sets the project directory. |
| Sessions compact at 70% of the context (`session.autocompact_pct`). | Long chats lose early details. | Chat mode keeps `_progress.md` and runs one phase per chat. The Task Runner gives every task a fresh session. |
| The Task Runner (`kirocrew run TASK.md`): <ul><li>plans the spec;</li><li>runs each task in a fresh session, in a git worktree on branch `kirocrew/task/<id>`;</li><li>has an independent reviewer check each diff;</li><li>retries and checkpoints;</li><li>runs independent tasks in parallel, three at a time.</li></ul> | It's a good fit for Phases 2–5, but a worktree only contains committed files. | `crew/TASK-review.md` describes Phases 2–5 as 12 tasks. You commit the kit before running it. REFERENCE_ROOT lives outside the repository, so the worktree can still read it. |
| Crew Workflows are Python scripts that Crew's own agent writes. The API isn't published, and the Workflows app is opt-in. | A hand-written script would be guesswork. | `crew/workflow-brief.md` is a plain-English brief for Crew to turn into a workflow. It's optional. |
| Hooks are global (dashboard, or `~/.kiro/crew/hooks.json`). <ul><li>The automatic triggers are `AgentSpawn`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse` and `Stop`; file-save triggers don't fire in Crew 0.8.</li><li>A `PreToolUse` hook that crashes or times out blocks every tool call.</li></ul> | The IDE's save hook can't be reused, and a broken guard can freeze every Crew session. | `~/.kiro-agent-review/bin/crew_hooks.py` has three subcommands: `inject`, `stop` and `guard`. It does nothing outside a review project, and it never crashes. The `stop` hook keeps the agent working until citations pass, and blocks at most twice for the same problems. |
| Crew doesn't read `permissions.yaml`. It uses approval modes, a denied-commands list and an OS sandbox. | The IDE permission rules have no effect. | Use Interactive approval for Phase 1. The Task Runner's worktree keeps your checkout safe. The guard hook is optional. |
| Custom agents run as sub-agents may get no tools (Kiro issue #11150), and Crew documents few agent fields. | A custom reviewer agent may silently fail. | The Crew setup uses the default agent. |
| The Gateway can run on another machine. | Files on your laptop are invisible to a remote Gateway. | Install the kit on the Gateway host. |

## 2. Requirements

- **Host:** run everything on the machine where Crew's Gateway runs. The project, the kit and `~/.kiro-agent-review/` must all be on that machine.
- **Crew:** Kiro Crew with the default Kiro CLI backend, signed in (`kiro-cli login`). Check it with `kirocrew doctor`.
- **Tools:** `python3` and `git` on the Gateway host.
- **Reference repo:** a clone of the public `system_prompts_leaks` repository.
- **Model:** Claude Opus 5.5, picked from the model chip in the composer.

## 3. Install

```bash
git clone <reference-repo-url> system_prompts_leaks   # if you don't have it yet
./aiops/install.sh /path/to/your/observability-project --reference-source /path/to/system_prompts_leaks
```

`--surface crew` is the default. `--reference-source` is optional when the clone sits next to this repository or in `~/Projects/`.

The installer copies these into your project:

- `.kiro/steering/agent-review.md`: the always-on pointer;
- `docs/agent-review-method/`: the rules, phases 1–6, appendices A–G, and `crew/` (the Task Runner specs and the workflow brief);
- `tools/agent_review/`: `check_citations.py` and `guard.py`, standard library only, read-only, no network.

Outside the project, it:

- copies 34 reference files into `~/.kiro-agent-review/reference`, from the pinned commit `b64ee93`, so every catalog line number matches;
- installs `~/.kiro-agent-review/bin/crew_hooks.py`;
- registers the project in `~/.kiro-agent-review/projects.txt`.

It finishes by running four self-tests and the catalog check, then prints the exact hook commands for your machine.

Then fill in `docs/agent-review-method/inputs.md`: models in use, tracing, where past incidents live, and known pain points.

## 4. Configure Crew (one time)

1. **Set the project directory.** Pick the project in the dashboard session header. Without a pick, a session starts in the crewmate's workspace folder (`~/.kiro/crew/workspace` for the `default` workspace). There is no `project_dir` setting in Crew 0.8.0: `kirocrew config set project_dir` fails with "Unknown key". For chat channels such as Slack and Telegram, give the crewmate a workspace whose folder is your project (Settings in the dashboard), since they have no session header.
2. **Set approval.** Under Settings → Security, choose Interactive for Phase 1, so you see every tool call. Keep `agent.sandbox` at `auto` or `strict`.
3. **Commit the kit on a review branch.** The Task Runner's worktree only contains committed files.
   ```bash
   cd /path/to/your/project && git checkout -b agent-review
   git add docs/agent-review-method tools/agent_review .kiro/steering/agent-review.md docs/agent-review/.review-mode
   git commit -m "Add agent-review kit"
   ```
4. **Add the hooks (optional, recommended).** Open the dashboard at Agent Capabilities → Hooks → New. Use the exact commands that `install.sh` printed; they contain absolute paths, because hooks get a minimal environment.

   | Name | Trigger | Matcher | Command | Effect |
   |---|---|---|---|---|
   | agent-review-inject | UserPromptSubmit | contains `agent-review:` | `<python3> -I ~/.kiro-agent-review/bin/crew_hooks.py inject` | Puts the rules and inputs into the context whenever your message starts with `agent-review:`. |
   | agent-review-stop-check | Stop | contains `agent-review:` | `<python3> -I ~/.kiro-agent-review/bin/crew_hooks.py stop` | When a reply ends with the status line, checks citations in deliverables changed in the last two hours. If a check fails, the agent has to keep working. After two attempts on the same problems it stops blocking and writes `docs/agent-review/tmp/unresolved-citations.md`. |
   | agent-review-guard | PreToolUse | `*` | `<python3> -I ~/.kiro-agent-review/bin/crew_hooks.py guard` | While `docs/agent-review/.review-mode` exists, blocks writes outside `docs/agent-review/`, network, git and install commands, and MCP and web tools. Create it disabled, click Test, and only then enable it. A failing `PreToolUse` hook blocks every tool call in Crew. It also can't stop a call that Kiro CLI already approved, so treat it as a second layer, not the main control. |

   The inject hook is optional, because the pointer steering already sends the agent to the rules. Use it if you see the agent skip the rules.

## 5. Smoke tests

| # | Do this | Pass if | If it fails |
|---|---|---|---|
| C1 | In a new chat on the project, send `agent-review: Name the six phases of the method and the deliverables folder.` | It names Phases 1–6 and `docs/agent-review/`. | Check the session's project directory. The steering file must start with `---` on line 1, and edits apply from the next session. |
| C2 | Send `agent-review: List <REFERENCE_ROOT>/OpenAI/Codex and show lines 41-49 of plan_mode.md there.` Use the absolute path from `inputs.md`. | It shows them. | Check REFERENCE_ROOT in `inputs.md`, and run `kirocrew doctor`. |
| C3 | Send `agent-review: Run python3 -I tools/agent_review/check_citations.py --self-test` | It prints `self-test ok`. | Install `python3` on the Gateway host. |
| C4 | From the project folder, run `kirocrew run docs/agent-review-method/crew/TASK-smoke.md --no-test`. | The task completes, and branch `kirocrew/task/<id>` has `docs/agent-review/_smoke.md` with PASS lines. | Commit the kit (setup step 3), and check REFERENCE_ROOT. |
| C5 | Hooks only. Click Test on each hook in the dashboard. Then send `agent-review: Write docs/agent-review/_c5.md with one line that cites README.md:99999, then end your reply with the status line.` | The stop hook makes the agent continue and fix or remove the citation. Delete `_c5.md` afterwards. | Check that the hook is enabled, the matcher is `agent-review:`, and the python path is right. |
| C6 | Guard only. Send `agent-review: Try to create src/c6.txt.` | It's blocked with an "agent-review guard" message. | The call may have been approved by Kiro CLI before the hook ran. Rely on Interactive approval and worktrees instead. |

## 6. Run the review

### Phase 1, in chat

1. Start a new chat on the project with Claude Opus 5.5, and send `agent-review: Run Phase 1. Stop when Phase 1 is done.` Answer its questions in the same chat.
2. Check the map (see "Check each phase" in the main README), then commit it:
   ```bash
   git add docs/agent-review && git commit -m "Phase 1: system map"
   ```
3. If the context fills up, start a new chat with `agent-review: Resume. Read docs/agent-review/_progress.md first and continue from the first unchecked step. Stop at the end of Phase 1.`

The same messages work from Slack, Telegram and the other channels, as long as the crewmate's workspace folder is the project.

### Phases 2–5, with the Task Runner (recommended)

```bash
cd /path/to/your/project
kirocrew run docs/agent-review-method/crew/TASK-review.md --no-test
kirocrew run status
```

- **Why `--no-test`:** it skips your project's own test command, which a review doesn't need and which may call the network. The spec's acceptance criteria still require the citation checker to pass.
- **Following progress:** use the dashboard and the notifications. The runner writes `TASK_PROGRESS.md` next to the spec, in the worktree.
- **Running unattended:** turn on the per-run auto-approve toggle in the dashboard. Your checkout stays untouched, because every change lands on branch `kirocrew/task/<id>`.
- **Bringing the results in:** check that only `docs/agent-review/` changed, then merge:
  ```bash
  git log --stat agent-review..kirocrew/task/<id>
  git merge kirocrew/task/<id>
  ```
- **After a crash:** rerun the same command to resume. Add `--fresh` to start over.

### Phases 2–5, in chat (alternative)

Use one new chat per message:

| Session | Message |
|---|---|
| 2 | `agent-review: Resume. Read docs/agent-review/_progress.md first. Run Phase 2 only, then stop.` |
| 3 | `agent-review: Resume. Read docs/agent-review/_progress.md first. Run Phase 3 steps 3.1 to 3.3 only, then stop.` |
| 4 | `agent-review: Resume. Read docs/agent-review/_progress.md first. Run Phase 3 steps 3.4 to 3.7 only, then stop.` |
| 5 | `agent-review: Resume. Read docs/agent-review/_progress.md first. Run Phase 4 only, then stop.` |
| 6 | `agent-review: Resume. Read docs/agent-review/_progress.md first. Run Phase 5 only, then stop.` |

### Phases 2–5, as a Crew Workflow (optional)

1. Run `kirocrew app enable workflows`.
2. Send the brief in `docs/agent-review-method/crew/workflow-brief.md` in a chat on the project.
3. Validate the script Crew writes in the Workflows app, and save it as `agent-review`.
4. Run it with `/workflow agent-review`.

### Phase 6: implement the approved items

1. Delete `docs/agent-review/.review-mode`, which turns the guard off.
2. Use one of these:
   - a chat: `agent-review: Run Phase 6 for R-01 only.`
   - for a larger item, its own Task Runner spec, with the tests in its acceptance criteria. See `phase-6-implement.md`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `install.sh` reports "crew_hooks self-test FAILED" | Check the first line of the installer output, `== agent-review kit <commit>`. It should match the latest commit; if not, run `git pull` and install again. Each failed check prints its exit code and output, followed by an environment block. Paste everything from "FAILED" down when you ask for help. To rerun only this test, use `python3 -I ~/.kiro-agent-review/bin/crew_hooks.py self-test --project /path/to/project --verbose`. |
| The agent ignores the method | Start the message with `agent-review:`. Check the session's project directory. Steering edits apply from the next session. Add the inject hook if it keeps happening. |
| The agent can't read REFERENCE_ROOT | Check the path in `inputs.md`, and run `kirocrew doctor`. The sandbox hides only credential folders such as `.gnupg`, `.docker` and `.gcloud`. |
| A Task Runner task can't find `docs/agent-review-method` | Commit the kit on the branch you run from. Worktrees only contain committed files. |
| A task fails review because it changed other files | The method allows writes under `docs/agent-review/` only. Read the step's error, fix what it says, and rerun. Use `--fresh` if it keeps failing. |
| "Loop detected" | The same error happened three times. Read the step's error before rerunning. |
| Every tool call is blocked | The `PreToolUse` guard is failing. Disable it in the dashboard, then use Test to check the python path. |
| The stop hook keeps blocking | It stops after two attempts on the same problems and writes `docs/agent-review/tmp/unresolved-citations.md`. Fix those by hand, or ask the agent again. |
| Hooks never fire | Hooks are global. Check that they're enabled and that the matcher is `agent-review:`. The agent must end its replies with the status line, and the method tells it to. |
| Too many approval prompts | Change the approval mode under Settings → Security, or use per-session Trust for read tools. |

## Sources

Kiro Crew documentation, checked October 14, 2026:

- [Quick start](https://kiro.dev/docs/crew/), [Features](https://kiro.dev/docs/crew/features/), [Interfaces](https://kiro.dev/docs/crew/interfaces/), [Chat](https://kiro.dev/docs/crew/chat/), [CLI reference](https://kiro.dev/docs/crew/interfaces/cli-reference/)
- [Agent capabilities](https://kiro.dev/docs/crew/capabilities/): [Steering](https://kiro.dev/docs/crew/capabilities/steering/), [Skills](https://kiro.dev/docs/crew/capabilities/skills/), [Agents](https://kiro.dev/docs/crew/capabilities/agents/), [Hooks](https://kiro.dev/docs/crew/capabilities/hooks/)
- [Task Runner](https://kiro.dev/docs/crew/features/task-runner/), [Workflows](https://kiro.dev/docs/crew/features/workflows/), [Subagents](https://kiro.dev/docs/crew/features/subagents/), [Agent backends](https://kiro.dev/docs/crew/features/agent-backends/)
- [Configuration](https://kiro.dev/docs/crew/configuration/), [Security](https://kiro.dev/docs/crew/security/), [Troubleshooting](https://kiro.dev/docs/crew/troubleshooting/)
- Kiro Crew's own documentation on memory, skills and hooks (the `docs/` folder of the installed package, for example `steering-and-hooks.md` and `skills.md`)

Known issues:

- Kiro Crew issue 10041: Crew doesn't read permissions.yaml
- Kiro issue 11150: custom agents as sub-agents get no tools
