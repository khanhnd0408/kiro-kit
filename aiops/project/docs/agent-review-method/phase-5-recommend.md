# Phase 5: Recommendations and drafts

Goal: prioritized, measurable changes and ready-to-review drafts.

Inputs: the scorecard files, `verification.md`, `03-comparison.md`, `05-prompt-audit.md`, `06-eval-plan.md`.

Deliverables:

- `docs/agent-review/07-recommendations.md` (Template T6);
- `docs/agent-review/08-drafts/`;
- `docs/agent-review/00-summary.md` (Template T7).

## 5.1 Collect the findings

- Gather findings from `docs/agent-review/scorecards/*.md`, `05-prompt-audit.md` and `03-comparison.md`.
- Use the verdicts in `verification.md`.
- Merge duplicates: findings with the same root cause become one item that lists all their finding IDs.

## 5.2 Prioritize

- **P0:** can produce a wrong root cause or an unsafe action, or hides a failure as "nothing found".
- **P1:** makes results noticeably less accurate, slower or more expensive.
- **P2:** hygiene and maintainability.

Give each item an effort: S (under a day), M (a few days) or L (more). Rank by impact over effort.

## 5.3 Write `07-recommendations.md`

- Use Template T6. Every item cites its findings and says how it will be measured, using a metric from `06-eval-plan.md`.
- Run the checker on it and fix every problem.

## 5.4 Write the drafts in `docs/agent-review/08-drafts/`

Follow `G-writing-rules.md`:

- `orchestrator.prompt.md` and `delegation-brief.md`;
- `logs.prompt.md`, `trace.prompt.md`, `metrics.prompt.md` and `code.prompt.md`;
- `tool-descriptions.md`;
- `schemas.py` (or `.ts`), adapted from `F-schemas.md`;
- `graph-changes.md`, with Mermaid and pseudocode.

Every draft starts with the draft header from `G-writing-rules.md`. Write for the models and library versions found in Phase 1, and in this codebase's style.

## 5.5 Self-review the drafts

For each draft, check:

- Does it fix the findings it claims to fix?
- Does it follow `G-writing-rules.md`?
- Does it keep everything the old prompt needed, such as context and real constraints?

Fix whatever fails. Then run the checker on the drafts folder files you wrote.

## 5.6 Write `00-summary.md`

- Use Template T7, about one page.
- Run the full checker: `python3 -I tools/agent_review/check_citations.py`.

## 5.7 Stop

- In manual mode, send the end-of-session report and ask which recommendations to implement.
- In workflow mode, finish with `send_message` (success), listing the top five recommendations and pointing me to `00-summary.md`.
