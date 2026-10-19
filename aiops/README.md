# Agent-review kit for Kiro

This kit runs a structured review of a LangGraph/LangChain incident-investigation system: an orchestrator plus logs (VictoriaLogs), trace (Tempo), metrics (VictoriaMetrics) and code (GitLab) agents, and a KB. The review compares the system with patterns from OpenAI Codex and Anthropic Claude Code. It produces a system map, scorecards, verified findings, an eval plan and draft fixes.

The method is the same everywhere. Small adapters fit it to the Kiro surface you use.

| You use | Guide | How you start the review |
|---|---|---|
| Kiro Crew: desktop app, dashboard, `kirocrew` CLI, Slack, Telegram and other channels | [Crew guide](adapters/crew/README.md) | Start a message with `agent-review:`. Phases 2–5 run unattended with `kirocrew run`. |
| Kiro IDE | [IDE guide](adapters/ide/README.md) | Type `#agent-review`. Phases 2–5 can run as a Kiro workflow. |

## Quick start (Kiro Crew)

```bash
git clone <reference-repo-url> system_prompts_leaks                  # the public reference repository of that name
git clone <this-repo-url> kiro-kit
./kiro-kit/aiops/install.sh /path/to/your/project --reference-source ./system_prompts_leaks
```

Run these on the Gateway host. Then follow sections 4–6 of the [Crew guide](adapters/crew/README.md):

1. Set the project directory.
2. Commit the kit on a review branch.
3. Run the smoke tests.
4. Send `agent-review: Run Phase 1. Stop when Phase 1 is done.`

For the IDE, add `--surface ide` and follow the [IDE guide](adapters/ide/README.md).

## What's in this repository

| Path | What it is |
|---|---|
| `install.sh` | Installs the method and one adapter into your project, and copies the reference files. Run `./install.sh --help` for options. |
| `project/docs/agent-review-method/` | The method: rules, phases 1–6, the pattern catalog (A), domain checklist (B), rubric (C), templates (D), examples (E), schemas (F) and writing rules (G). |
| `project/tools/agent_review/` | `check_citations.py` checks that every `file:line` citation points at a real line. `guard.py` keeps the project read-only during review mode. |
| `adapters/crew/` | Crew files: <ul><li>an always-on pointer steering file;</li><li>Task Runner specs (`TASK-review.md`, `TASK-smoke.md`);</li><li>a workflow brief;</li><li>`crew_hooks.py`, installed to `~/.kiro-agent-review/bin/`.</li></ul> |
| `adapters/ide/` | IDE files: <ul><li>a manual steering launcher;</li><li>the `agent-reviewer` agent;</li><li>v1 hooks;</li><li>workflow recipes.</li></ul> |
| `reference-files.txt` | The 34 reference files the method cites. |
| `legacy/single-file-prompt.md` | The original single-file prompt, kept for reference. |

## How the method keeps a weaker harness on track

- **One phase per session,** with a progress file in chat mode, or a fresh session per task in the Task Runner and workflows. Context compaction can't lose earlier work.
- **Exact sources:** 71 catalog patterns, each with a `path:line` citation into the reference files. The agent reads sources instead of recalling Codex or Claude Code from memory.
- **Mechanical checks:** the citation checker runs after every deliverable. Hooks can enforce it, and the guard keeps the project read-only until Phase 6.
- **Fill-in templates, a rubric and examples,** so findings stay concrete: a `file:line` location, a failure scenario, and an observed or inferred label.
- **Independent verification:** a separate pass tries to refute the top findings before any recommendation is made.

## Check each phase (about 5 minutes each)

- **Phase 1:**
  - every agent is in the agent table with the right model;
  - the Mermaid graph matches how you think the system flows;
  - every prompt and tool is listed;
  - `git status` shows changes only under `docs/agent-review/`.
- **Phase 2:**
  - most "In this project" lines name a `file:line`, or say "not found (searched: ...)";
  - spot-checked entries are marked `verified` or `corrected`.
- **Phase 3:**
  - every finding has a `file:line`, a failure scenario, and an observed or inferred label;
  - `verification.md` exists;
  - open two cited lines yourself and check them.
- **Phase 4:**
  - cases start from an alert or a user report;
  - negative cases are included;
  - there's a record-and-replay plan, a hallucinated-evidence check and a KB on/off test.
- **Phase 5:**
  - every recommendation links to findings and to a metric;
  - every draft names the file it replaces.

`python3 -I tools/agent_review/check_citations.py` re-checks every citation and required field at any time.

## Reference material

The method quotes short excerpts from the public `system_prompts_leaks` repository, which is CC0-licensed. The excerpts were checked against commit `b64ee93`. `install.sh` copies the 34 cited files into `~/.kiro-agent-review/reference` on your machine, outside your project, so they are never committed to it. When that commit exists in your clone, it copies from the commit itself, so every line number matches.

## Uninstall

1. Remove these from the project:
   - `docs/agent-review-method/` and `tools/agent_review/`;
   - `.kiro/steering/agent-review.md`;
   - on the IDE, also `.kiro/agents/agent-reviewer.md`, `.kiro/hooks/agent-review.json` and `.kiro/workflows/agent-review*.workflow.yaml`.
2. In Crew, delete the `agent-review-*` hooks in the dashboard.
3. Run `rm -rf ~/.kiro-agent-review`.
4. Keep `docs/agent-review/` if you want the results.
