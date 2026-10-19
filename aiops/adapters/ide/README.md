# Kiro IDE guide

This guide covers running the agent review in the Kiro IDE. For Kiro Crew, see [the Crew guide](../crew/README.md). For an overview of the kit, see [the main README](../../README.md).

In the IDE, Kiro's own features enforce the method's rules: steering, hooks, permissions, a custom agent, and workflows.

## 1. How Kiro's harness differs, and what the kit does about it

| Kiro behavior | What goes wrong in a long review | What the kit does |
|---|---|---|
| Kiro compacts long sessions automatically into summaries. | Details from early files get lost, and a pasted 1,000-line prompt doesn't survive. | The method is split into files. A manual steering file loads only the rules, and the agent reads each phase file when it needs it. It keeps a progress file, and each phase gets its own session. |
| Steering loads by inclusion mode: `always`, `fileMatch`, `manual` (typed as `#name`) or `auto`. | Always-on steering costs context in every chat, including unrelated ones. | `.kiro/steering/agent-review.md` is `manual`. Type `#agent-review` to load it. |
| Untrusted workspaces ignore workspace steering, agents, skills and MCP config. | The kit silently does nothing. | Setup step 1: trust the workspace. |
| Permissions (`permissions.yaml`): without configuration, only workspace reads and read-only git commands run silently; everything else asks. The agent can't write permission files. | Hundreds of approval prompts, and every read outside the workspace prompts. | Setup step 2: paste the rules that `install.sh` prints. |
| Kiro's search tools follow `.gitignore` and stay in the workspace. Workflow steps may run in worktrees made from the launch branch. | A git-ignored reference folder is skipped by search, or missing from a worktree. | The reference files are copied outside the repo, into `~/.kiro-agent-review/reference`. The agent reads them by absolute path, and it never commits them. |
| Hooks: command hooks run locally and use no credits. Exit code 2 from a `PreToolUse` hook blocks the call, and stderr goes back to the agent. File hooks fire only on the agent's own edits. | Rules written as prose can be ignored. | `agent-review-guard` blocks writes outside `docs/agent-review/`, network, git and install commands, and MCP and web tools, during review mode. `agent-review-citation-check` runs on every saved deliverable. |
| Hooks may not fire inside sub-agents or workflow steps (issue #7755). | The citation check might not run automatically in a workflow. | Every phase file and workflow step also runs the checker explicitly. |
| Custom agents called as sub-agents get only orchestration tools, and some users see this even as the main agent. This is issue #11150, still open on Oct 9, 2026. | A custom reviewer silently can't read or write anything. | The `agent-reviewer` agent is optional, and smoke test S5 checks it. If it fails, use the default agent with `#agent-review` (Mode A), or `wf-planner` in workflows. |
| Workflows run each step in a fresh session with handoffs through files. They are opt-in, in Workspace Configuration → Workflows. | Without workflows, the parallel and independent-verifier design isn't available. | `agent-review.workflow.yaml` runs Phases 2–5: catalog, six parallel component reviews, the orchestrator review, a verify loop, comparison and audit, the eval plan, and recommendations. |
| Claude Opus 5.5 in Kiro has a 1M-token context and costs 2.0× credits, compared with Auto at 1.0×. | Large runs get expensive. | Mode A (chat sessions) costs least. Mode B (workflow) costs more but parallelizes the work and verifies it independently. |

## 2. Requirements

- Kiro IDE 1.1 or later. That's needed for `permissions.yaml`, v1 JSON hooks and Markdown custom agents. Workflows also need the builds from September 30, 2026 or later, enabled for your account.
- `python3` on your PATH, for the checker and the guard hooks.
- Claude Opus 5.5 selected in Kiro. Sonnet 5.5 (1.3×) is cheaper, but check its verification results more carefully.

## 3. Install

```bash
./aiops/install.sh /path/to/your/observability-project --surface ide --reference-source /path/to/system_prompts_leaks
```

`--reference-source` is optional when the `system_prompts_leaks` clone sits next to this repository or in `~/Projects/`.

The installer copies these into your project:

- `.kiro/steering/agent-review.md`: the manual launcher, `#agent-review`;
- `.kiro/agents/agent-reviewer.md`: the optional custom agent, with scoped permissions;
- `.kiro/hooks/agent-review.json`: the guard and the citation check;
- `.kiro/workflows/agent-review.workflow.yaml` and `agent-review-smoke.workflow.yaml`;
- `docs/agent-review-method/`: the rules, phases 1–6 and appendices A–G;
- `tools/agent_review/`: `check_citations.py` and `guard.py`, standard library only, read-only, no network.

It also:

- copies 34 reference files into `~/.kiro-agent-review/reference`, or wherever `AGENT_REVIEW_REFERENCE=...` points;
- writes that path into `inputs.md`;
- creates `docs/agent-review/.review-mode`;
- runs the self-tests and checks every reference citation.

It skips existing files unless you pass `--force`, and it never deletes anything.

Then fill in what you know in `docs/agent-review-method/inputs.md`: models in use, tracing, where past incidents live, and known pain points.

If you copied the earlier single-file prompt into your project (for example `docs/agent-review-prompt.md`), delete that copy. The kit replaces it, and a second set of rules would conflict. The original is kept in [`legacy/single-file-prompt.md`](../../legacy/single-file-prompt.md) for reference only.

## 4. Configure Kiro (one time)

1. **Trust the workspace** when Kiro asks. If you dismissed the prompt, find it in Kiro's workspace settings.
2. **Add the permission rules.** Paste the rules that `install.sh` printed into `~/.kiro/settings/permissions.yaml`. They allow:
   - silent reads of the reference folder;
   - writes under `docs/agent-review/`;
   - read-only shell commands and the checker.

   They deny `sed -i`, `find -delete`/`-exec`, and `cat` of `.env` files. If you'd rather not add user-level rules, choose "Always allow → This workspace" on the first few prompts instead.
3. **Pick the model.** Choose Claude Opus 5.5 in the chat model picker.
4. **Set autonomy.** Use Supervised for Phase 1, so you can watch how it behaves. For later phases Autopilot is reasonable, because the guard and the permissions keep writes inside `docs/agent-review/`. The setting is under Settings → Agent → Agent Autonomy.
5. **Check the hooks.** Open the Agent Hooks panel and check that `agent-review-guard` and `agent-review-citation-check` are listed and enabled. If they show a legacy or upgrade badge, your IDE is older than 1.0.
6. **Enable workflows (Mode B only).** Go to Workspace Configuration → Workflows, enable them, and start a new chat.

## 5. Smoke tests

Use a fresh chat for each test. Kiro's Restore doesn't reload agent config.

| # | Do this | Pass if | If it fails |
|---|---|---|---|
| S1 | Send `#agent-review Name the six phases of this method and the deliverables folder.` | It names Phases 1–6 and `docs/agent-review/`. | Trust the workspace. Check that the steering file starts with `---` on line 1. |
| S2 | Send `List <REFERENCE_ROOT>/OpenAI/Codex and show lines 41-49 of plan_mode.md there.` Use the absolute path from `inputs.md`. | It shows them without an approval prompt. | Add the `fs_read` rule from step 4.2. |
| S3 | Send `Run python3 -I tools/agent_review/check_citations.py --self-test` | It prints `self-test ok`. | Check that `python3` is on your PATH, and allow the command. |
| S4 | Send `Create docs/agent-review/tmp/s4.md with one line, then try to create src/s4.txt.` | The first file is created, and the second is blocked with an "agent-review guard" message. Delete the `tmp` folder afterwards. | Check that the hook is enabled and that `docs/agent-review/.review-mode` exists. |
| S5 | Pick `agent-reviewer` in the agent picker, then send `Read docs/agent-review-method/inputs.md and list the tools you have.` | It reads the file and lists read, write and shell tools. | You've hit issue #11150. Use the default agent with `#agent-review` (Mode A), and switch the workflows to `wf-planner` (below). |
| S6 | Mode B only: run `/workflow-run` and pick `agent-review-smoke`. | `docs/agent-review/_smoke.md` shows PASS for steps 1–5. | Switch the agent to `wf-planner` and run it again. If the step can't run shell commands, the checker results come from the hook and from Mode A runs instead. |

To switch both workflow recipes to Kiro's bundled planner agent:

```bash
sed -i 's/agent: agent-reviewer/agent: wf-planner/' .kiro/workflows/agent-review*.workflow.yaml
```

## 6. Run the review

### Mode A: chat sessions (works in any setup)

Start a new chat for each row, and paste the message. `#agent-review` loads the rules each time.

| Session | Message |
|---|---|
| 1 | `#agent-review Run Phase 1. Stop when Phase 1 is done.` Answer its questions in the same chat; it records them in `_progress.md`. |
| 2 | `#agent-review Resume: read docs/agent-review/_progress.md first. Run Phase 2 only, then stop.` |
| 3 | `#agent-review Resume: read docs/agent-review/_progress.md first. Run Phase 3 steps 3.1 to 3.3 only, then stop.` |
| 4 | `#agent-review Resume: read docs/agent-review/_progress.md first. Run Phase 3 steps 3.4 to 3.7 only, then stop.` |
| 5 | `#agent-review Resume: read docs/agent-review/_progress.md first. Run Phase 4 only, then stop.` |
| 6 | `#agent-review Resume: read docs/agent-review/_progress.md first. Run Phase 5 only, then stop.` |

If the context meter gets above about 70%, send `Finish the current step, update _progress.md, and stop.` Then continue in a new chat with `#agent-review Resume: read docs/agent-review/_progress.md first and continue from the first unchecked step. Stop at the end of Phase <N>.`

If S5 passed, you can pick `agent-reviewer` in the agent picker for these sessions instead of typing `#agent-review`. It loads the rules by itself.

### Mode B: workflow for Phases 2–5 (after Phase 1 in Mode A)

1. Run session 1 of Mode A and confirm the system map.
2. Commit the kit and the map on a review branch, so worktree-based steps can see them:
   ```bash
   git checkout -b agent-review
   git add .kiro docs/agent-review-method tools/agent_review docs/agent-review
   git commit -m "Add agent-review kit and system map"
   ```
3. In a chat with Claude Opus 5.5 selected, run `/workflow-run`, pick `agent-review`, and optionally add notes.
4. Follow the run under **Workflows** above the chat. Open a step's tab to see its session, or to answer it if it pauses.
5. When it finishes, read `docs/agent-review/00-summary.md`, and choose the items for Phase 6 in chat.

The workflow runs:

1. the catalog step;
2. six component reviews in parallel (logs, trace, metrics, code, KB, graph);
3. the orchestrator review;
4. an independent verify loop, up to two rounds, that stops when `verification.json` reports nothing unverified;
5. comparison and the prompt audit;
6. the eval plan;
7. recommendations and drafts.

### Phase 6: implement the approved items

1. Delete `docs/agent-review/.review-mode`, which turns the guard off.
2. Use the default agent, not `agent-reviewer`, in a new chat: `#agent-review Run Phase 6 for R-01 only.`
3. For larger items, ask Kiro to turn the recommendation into a Spec first, so each task runs with approvals.

## 7. Check each phase (about 5 minutes each)

- **Phase 1:**
  - every agent is in the agent table with the right model;
  - the Mermaid graph matches how you think the system flows;
  - the prompt inventory lists every prompt;
  - every tool row names its endpoint;
  - `git status` shows changes only under `docs/agent-review/`.
- **Phase 2:**
  - most "In this project" lines name a `file:line` or say "not found (searched: ...)";
  - spot-checked entries are marked `verified` or `corrected`.
- **Phase 3:**
  - every finding has a `file:line`, a failure scenario and an observed or inferred label;
  - `verification.md` exists;
  - open two cited lines yourself and check them.
- **Phase 4:**
  - cases start from an alert or a user report;
  - negative cases are included;
  - there's a record-and-replay plan, a hallucinated-evidence check and a KB on/off test.
- **Phase 5:**
  - every recommendation links to findings and to a metric;
  - every draft names the file it replaces.

You can run `python3 -I tools/agent_review/check_citations.py` yourself at any time. It re-checks every citation and every required field.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `#agent-review` isn't offered | Trust the workspace, and keep the front matter as the very first lines of the file. |
| Every `grep` asks for approval | Add the shell rules from step 4.2. Chained commands are approved one part at a time. |
| The guard blocks something you want | It's on while `docs/agent-review/.review-mode` exists. Delete the flag, do the task, and recreate the flag. |
| Hooks never run | Check the Agent Hooks panel. File hooks fire only on the agent's own edits, not on yours. |
| The custom agent has no tools | That's issue #11150. Use the default agent with `#agent-review`, or `wf-planner` in workflows. Test changes in a fresh chat. |
| A workflow step can't find the method files | Commit the kit to the branch you launch from (Mode B step 2). |
| The agent describes Codex or Claude Code from memory | Reply: `Cite path:line under REFERENCE_ROOT for every pattern, or remove it.` |
| Findings are vague | Reply: `Rewrite with Template T3 and Example E1, then run the citation checker.` |

## 9. Uninstall

1. Remove these from the project:
   - `.kiro/steering/agent-review.md` and `.kiro/agents/agent-reviewer.md`;
   - `.kiro/hooks/agent-review.json` and `.kiro/workflows/agent-review*.workflow.yaml`;
   - `docs/agent-review-method/` and `tools/agent_review/`.
2. Remove the reference copy with `rm -rf ~/.kiro-agent-review`.
3. Remove the rules you added to `~/.kiro/settings/permissions.yaml`.
4. Keep `docs/agent-review/` if you want the results.

## Sources

Kiro documentation, checked October 14, 2026:

- [Steering](https://kiro.dev/docs/steering/)
- [Custom agents](https://kiro.dev/docs/custom-agents/), [Configuration reference](https://kiro.dev/docs/custom-agents/configuration-reference/), [Sub-agents](https://kiro.dev/docs/custom-agents/subagents/), [Built-in agents](https://kiro.dev/docs/custom-agents/built-in/)
- [Hooks](https://kiro.dev/docs/hooks/), [Hook triggers](https://kiro.dev/docs/hooks/types/), [Hook actions](https://kiro.dev/docs/hooks/actions/)
- [Permissions](https://kiro.dev/docs/permissions/)
- [Workflows](https://kiro.dev/docs/workflows/), [Authoring](https://kiro.dev/docs/workflows/authoring/), [Patterns](https://kiro.dev/docs/workflows/patterns/), [Launch post](https://kiro.dev/blog/introducing-workflows/)
- [Agent Skills](https://kiro.dev/docs/skills/)
- [What's new in IDE 1.0](https://kiro.dev/docs/ide/whats-new-v1/)
- [Available models](https://kiro.dev/docs/models/available-models/), [Opus 5.5 in Kiro](https://kiro.dev/blog/opus-5-5/)

Known issues:

- Kiro issue 11150: custom agents as sub-agents get no tools
- Kiro issue 7755: hooks in sub-agents
- Kiro issue 11037: CLI ignores permissions.yaml
