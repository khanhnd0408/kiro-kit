# Agent review: Phases 2 to 5

## Goal

Complete Phases 2 to 5 of the agent review defined in `docs/agent-review-method/`. Use the confirmed system map in `docs/agent-review/01-system-map.md`. Write every deliverable under `docs/agent-review/` and change nothing else in the repository.

## Context

- Every task in this spec runs in Task Runner mode. Before anything else, read `docs/agent-review-method/00-rules.md` (section 3, "Task Runner mode", applies) and `docs/agent-review-method/inputs.md`.
- Each task names its phase file and the files it reads. Earlier tasks' results are only in those files.
- REFERENCE_ROOT in `inputs.md` is a folder outside the repository. Read it by absolute path, and never run anything in it.
- The project's source code is read-only. Don't edit, delete or move anything outside `docs/agent-review/`.
- Don't run the project's tests, install packages, call live systems, or use the network.
- After writing a deliverable, run `python3 -I tools/agent_review/check_citations.py --only <file>` and fix every problem it reports.
- End each task's final message with the status line `agent-review: <task> done` (or `blocked`, with the reason).

## Requirements

Run these tasks in order. Tasks 2 to 7 don't depend on each other and can run in parallel. Every other task depends on all the tasks before it.

1. **Pattern catalog.**
   - Do Phase 2 (`phase-2-catalog.md`, steps 2.1 to 2.6).
   - Write `docs/agent-review/02-pattern-catalog.md`.
2. **Review the logs component.**
   - Do Phase 3 step 3.2 (`phase-3-review.md`) for the logs component only.
   - Use the LOGS checklist in `B-domain-checklist.md`, the rubric in `C-scoring-rubric.md`, Templates T3 and T8, and E1.
   - Write `docs/agent-review/scorecards/logs.md` only.
3. **Review the trace component.**
   - Same as task 2, with the TRACE checklist.
   - Write `docs/agent-review/scorecards/trace.md` only.
4. **Review the metrics component.**
   - Same as task 2, with the MET checklist.
   - Write `docs/agent-review/scorecards/metrics.md` only.
5. **Review the code component (GitLab).**
   - Same as task 2, with the CODE checklist.
   - Write `docs/agent-review/scorecards/code.md` only.
6. **Review the KB.**
   - Same as task 2, with the KB checklist.
   - Write `docs/agent-review/scorecards/kb.md` only.
7. **Review the LangGraph implementation.**
   - Do Phase 3 step 3.3, with the LG checklist.
   - Write `docs/agent-review/scorecards/graph.md` only.
8. **Review the orchestrator.**
   - Read every file in `docs/agent-review/scorecards/` first.
   - Do Phase 3 step 3.2 for the orchestrator, with the ORCH checklist, and score D15 and D16 for the whole system.
   - Write `docs/agent-review/scorecards/orchestrator.md`.
9. **Verify the findings.**
   - Act as an independent verifier, and try to refute the findings.
   - Do Phase 3 step 3.6 over `docs/agent-review/scorecards/*.md`.
   - Write `docs/agent-review/verification.md` and `docs/agent-review/verification.json`.
   - Update Verdict lines, and move refuted findings into each scorecard's "Refuted during verification" section.
10. **Compare and audit.**
    - Do Phase 3 steps 3.4, 3.5 and 3.7.
    - Write `docs/agent-review/03-comparison.md`, `docs/agent-review/05-prompt-audit.md` and `docs/agent-review/04-agent-scorecards.md`.
11. **Eval plan.**
    - Do Phase 4 (`phase-4-eval.md`).
    - Write `docs/agent-review/06-eval-plan.md`.
    - If PAST_INCIDENTS or STAGING_FOR_FAULTS is unknown, list exactly what's needed from the user.
12. **Recommendations and drafts.**
    - Do Phase 5 steps 5.1 to 5.6 (`phase-5-recommend.md`).
    - Write `docs/agent-review/07-recommendations.md`, the drafts in `docs/agent-review/08-drafts/`, and `docs/agent-review/00-summary.md`.

## Acceptance criteria

- Each task's files exist and follow the templates in `docs/agent-review-method/D-templates.md`.
- `python3 -I tools/agent_review/check_citations.py` exits 0 after every task.
- For every task, `git diff --name-only HEAD~1` lists only paths under `docs/agent-review/`.
- `docs/agent-review/verification.json` reports `"remaining_unverified": 0`.
- `docs/agent-review/00-summary.md` names the weakest component and the top 10 changes, each linked to findings.
