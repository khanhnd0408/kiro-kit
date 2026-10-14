# Phase 6: Implement only the items I approve

Phase 6 changes source code, so it works differently from Phases 1 to 5.

## 6.0 Before you start (done by me)

- I delete `docs/agent-review/.review-mode`, which turns the guard hook off.
- I use Kiro's default agent, or a Spec session for larger items, not the `agent-reviewer` agent, which can only write under `docs/agent-review/`.

## 6.1 Restate the plan

Restate the approved item, the files you'll change, and how you'll test it.

## 6.2 Make the smallest change

Make the smallest change that implements the item, in the codebase's style. Start from the matching draft in `docs/agent-review/08-drafts/`.

## 6.3 Test

Update or add tests, and run the tests that need no network.

## 6.4 Run the eval, if approved

If an eval exists and I approve the run, run it before and after the change, and report both numbers.

## 6.5 Record the result

Mark the item as done or partial in `docs/agent-review/07-recommendations.md`, with notes, and update `_progress.md`.

## 6.6 Report

Report the files changed, the test results, the eval results (or "not run"), and anything left open.

For a larger item, I may ask you to turn it into a Kiro spec first (`.kiro/specs/<name>/requirements.md`, `design.md`, `tasks.md`), so each task runs with approvals. Conventions we adopt can go into a steering file, `.kiro/steering/agent-prompting.md`.
