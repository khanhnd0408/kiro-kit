# Phase 6: Implement only the items I approve

Phase 6 changes source code, so it works differently from Phases 1 to 5.

## 6.0 Before you start (done by me)

- I delete `docs/agent-review/.review-mode`, which turns the guard hook off.
- I use the default agent, not the IDE's `agent-reviewer` agent, which can only write under `docs/agent-review/`.

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

A larger item can run as a structured job instead of a chat:

- **Kiro Crew:** write a Task Runner spec for the item (`## Goal`, `## Requirements`, `## Acceptance criteria`, with the tests to pass) and run it with `kirocrew run <spec>`. The runner works in a git worktree, runs the tests, has an independent reviewer check each diff, and commits on its own branch. Mark risky steps with `force_approval: true`.
- **Kiro IDE:** turn the item into a Kiro spec (`.kiro/specs/<name>/requirements.md`, `design.md`, `tasks.md`), so each task runs with approvals.

Conventions we adopt can go into a steering file, `.kiro/steering/agent-prompting.md`.
