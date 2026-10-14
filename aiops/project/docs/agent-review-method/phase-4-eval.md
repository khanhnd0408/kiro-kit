# Phase 4: Design the evaluation

Goal: a plan that turns "does it work well?" into numbers, fitted to this repository.

Inputs: `01-system-map.md`, `04-agent-scorecards.md`, the scorecard files, REFERENCE_ROOT.

Deliverable: `docs/agent-review/06-eval-plan.md`.

## 4.1 Read the method

- Read `Anthropic/claude-code/skills/claude-api/shared/evals/build-eval.md` and `Anthropic/claude-code/skills/claude-api/shared/evals/eval-audit.md` (under REFERENCE_ROOT) in full, in ranges. Note the rules you'll apply.
- Those files mention Claude-specific tooling (report builders, `.claude/hillclimb/` folders, a question tool). Translate these to this project's stack instead of copying them.

## 4.2 Inventory reusable assets

From the map, list the reusable tests, fixtures, recorded responses, datasets and tracing.

## 4.3 Pick the first two flows to evaluate

Pick the weakest specialist from the scorecards, and the end-to-end RCA. Explain the choice in two sentences.

## 4.4 Define the case format

Use Template T5.

- Inputs are what an alert or a user would report (P65).
- Expected outputs depend on the flow: root cause, culprit change or "not code-related", onset time within a tolerance, fix category, and the evidence that must be cited.

## 4.5 Design fixture record and replay

- Wrap each backend tool. In record mode it saves the tool name and normalized parameters with the response. In replay mode it returns the saved response.
- A query with no saved response returns an explicit "fixture miss" error. That counts as a harness gap, not a model failure (P67).
- Say where the wrapper goes in this project's code.

## 4.6 Define the graders, cheapest first

1. **Programmatic checks:**
   - queries are valid (they parse, or replay against the fixture);
   - the root-cause service matches;
   - the SHA or MR matches;
   - the onset time is within tolerance;
   - the report has the required sections.
2. **Hallucinated-evidence check:**
   1. Extract every trace ID, SHA, MR reference, query and quoted log line from the final report.
   2. Search the run's recorded tool outputs for each one.
   3. Count any item that isn't found as hallucinated, and report the rate.
3. **LLM judge** for the explanation:
   - five to eight concrete, checkable criteria;
   - a different model from the one under test;
   - structured output;
   - calibrated on 10 to 20 cases labeled by a human (P68).
4. **Human spot-check:** which cases are checked, and how often.

## 4.7 Define the per-case metrics and failure classes

- Metrics: tool calls per agent, tokens, cost, latency and rounds.
- Failure classes: backend error, timeout, truncated, fixture miss, refusal.

## 4.8 Plan the cases

- List the first 10 cases, each with its source (postmortem, fault injection, seed, synthetic) and type (positive, negative, red herring).
- Include at least two negative cases.
- If PAST_INCIDENTS or STAGING_FOR_FAULTS is unknown, list exactly what you need from me.

## 4.9 Size the eval

Compute the noise floor (P69) for the planned cases and repetitions. Compare it with the smallest improvement worth acting on, and recommend case and repetition counts.

## 4.10 Design the KB ablation

Run the same cases with the KB on and off (P70), and say which metrics should move.

## 4.11 Write `06-eval-plan.md`

- Run the checker on it and fix every problem.
- In chat mode, update `_progress.md`.
