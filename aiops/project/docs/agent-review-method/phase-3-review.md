# Phase 3: Compare, score and audit

Goal: an evidence-based scorecard for each component, a comparison table and a prompt audit, with the top findings verified.

Inputs: `docs/agent-review/01-system-map.md`, `docs/agent-review/02-pattern-catalog.md`, `B-domain-checklist.md`, `C-scoring-rubric.md`, `D-templates.md`, `E-examples.md`.

Deliverables:

- `docs/agent-review/scorecards/<component>.md`, one per component (Template T8);
- `docs/agent-review/verification.md` and `docs/agent-review/verification.json` (Template T9);
- `docs/agent-review/03-comparison.md` (Template T4);
- `docs/agent-review/05-prompt-audit.md`;
- `docs/agent-review/04-agent-scorecards.md`, an index (Template T10).

The components are `logs`, `trace`, `metrics`, `code`, `kb` and `orchestrator`. The LangGraph implementation is reviewed separately as `graph`.

## 3.1 Prepare

- Re-read `01-system-map.md` and the "In this project" lines in `02-pattern-catalog.md`. In chat mode, re-read `_progress.md` too.
- Review in this order: logs, trace, metrics, code, kb, orchestrator. The specialists come first because the orchestrator can only be as good as what they return. In workflow mode, the specialists run in parallel and the orchestrator runs after them.

## 3.2 Review one component at a time

For each component:

1. Read its prompts, tool code and node code in full, in ranges (00-rules.md section 4.3).
2. Walk its checklist in `B-domain-checklist.md`. For each item, write meets, partial, missing or N/A, with the evidence (`file:line`) and a label.
3. Score the dimensions with `C-scoring-rubric.md`, giving one line of reason and evidence per score.
4. Write findings for the biggest gaps (usually three to eight) with Template T3. Build each failure scenario with 00-rules.md section 4.6, and match the level of detail in E1 of `E-examples.md`.
5. Write `docs/agent-review/scorecards/<component>.md` with Template T8.
6. Run `python3 -I tools/agent_review/check_citations.py --only docs/agent-review/scorecards/<component>.md` and fix every problem.
7. In chat mode, tick the component in `_progress.md` before you start the next one, so a context reset loses at most one component.

For the orchestrator, also read the specialist scorecards. Note any place where the orchestrator relies on something a specialist doesn't provide.

## 3.3 Review the LangGraph implementation

- Use the LG checklist in `B-domain-checklist.md`.
- Findings use IDs `F-GRAPH-NN`.
- Write `docs/agent-review/scorecards/graph.md` with Template T8, and run the checker on it.

## 3.4 Write `03-comparison.md`

Use Template T4: one row per dimension. Fill the Codex and Claude Code columns from the catalog, and this project's column from the scorecards.

## 3.5 Run the prompt audit

- Read `Anthropic/claude-code/skills/claude-api/shared/prompt-audit.md` (under REFERENCE_ROOT) in full, in ranges.
- Apply its Steps 0 to 6 to the prompt inventory in `01-system-map.md`.
- The target model is each node's real model. For nodes that aren't on Claude, apply only the general principles and list the Claude-specific rows you skipped. For nodes on OpenAI models, Codex's prompts are the closer style reference. For nodes on Claude models, Claude Code's prompts and the matching section of `model-migration.md` are.
- Write the Step 5 report and the Step 6 diff in `docs/agent-review/05-prompt-audit.md`. Propose only; don't apply anything.

## 3.6 Verify the top findings

Act as an independent verifier. Your job is to try to refute each finding, not to defend it.

1. Take every P0 finding from `docs/agent-review/scorecards/*.md`, plus the 10 most severe of the others.
2. For each one:
   1. Re-open the evidence.
   2. Look for counter-evidence: is it handled elsewhere, guarded upstream, set at runtime by config or environment, or covered by a test?
   3. Mark it CONFIRMED (quote the line that proves it), PLAUSIBLE (say what would confirm it) or REFUTED (quote the line that disproves it).
3. Update each finding's Verdict line in its scorecard file. Move REFUTED findings into that file's "Refuted during verification" section, and say why.
4. Write `docs/agent-review/verification.md` and `docs/agent-review/verification.json` with Template T9.
   - `remaining_unverified` counts the findings you selected but couldn't verify.
   - Set it to 0 when every selected finding has a verdict.
   - Findings that need a live run count as verified with verdict PLAUSIBLE.
5. Run the checker on every file you changed.

## 3.7 Write the index

- Write `docs/agent-review/04-agent-scorecards.md` with Template T10: a score table across components, finding counts by severity, and links to each scorecard file.
- Run the checker on all deliverables: `python3 -I tools/agent_review/check_citations.py`.
- In chat mode, update `_progress.md`.
