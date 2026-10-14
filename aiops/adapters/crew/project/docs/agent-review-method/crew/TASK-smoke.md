# Agent review: setup check

## Goal

Confirm that a Task Runner session can read the agent-review method and REFERENCE_ROOT, run the citation checker, and write under `docs/agent-review/`. Change nothing else.

## Requirements

1. Write `docs/agent-review/_smoke.md` with one PASS or FAIL line for each of these checks, plus one line of detail:
   1. Read `docs/agent-review-method/inputs.md` and note the REFERENCE_ROOT value.
   2. List `<REFERENCE_ROOT>/OpenAI/Codex`.
   3. Read lines 41 to 49 of `<REFERENCE_ROOT>/OpenAI/Codex/plan_mode.md`.
   4. Run `python3 -I tools/agent_review/check_citations.py --self-test`.
   5. Run `python3 -I tools/agent_review/check_citations.py --catalog`.
   6. Name the tools you have.

   If a check fails, write FAIL and continue with the rest.
2. End your final message with `agent-review: smoke done`.

## Acceptance criteria

- `docs/agent-review/_smoke.md` exists and has a line for each of checks 1 to 6.
- `git diff --name-only HEAD~1` lists only `docs/agent-review/_smoke.md`.
