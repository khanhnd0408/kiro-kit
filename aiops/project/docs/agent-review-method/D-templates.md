# Appendix D: Templates

Citations go in backticks as `path:line` or `path:start-end`. The checker (`tools/agent_review/check_citations.py`) verifies them. It also checks the required fields of findings (T3) and recommendations (T6).

## T0: `_progress.md` (chat mode only)

```markdown
# Progress
Updated: <UTC time>, after step <ID>

## Steps
- [ ] 1.1 Set up
- [ ] 1.2 Project overview
- [ ] ... (every step ID of Phases 1 to 5; in Phase 3, one box per component)

## Facts (short, with file:line)
-

## Decisions and answers from the user
-

## Assumptions (defaults used when the user didn't answer)
-

## Open questions
-

## Next step
-
```

## T1: `01-system-map.md`

```markdown
# System map
## Summary (5 lines at most)
## Stack (language, LangGraph/LangChain versions, model providers, backends)
## Entry points
## Graph (Mermaid; one diagram per graph)
## Agents
| Agent/node | File | Role (one line) | Model and params | Tools | Input from | Output to |
## State
| Field | Type | Reducer | Written by | Read by |
## Prompt inventory
| ID | Location | Used by | Assembly | Size (lines) | Dynamic variables |
## Tools
| Tool | Agent | Backend and endpoint | Params (defaults, bounds) | Query built by | Time handling | Output shaping | Errors | Read-only |
## KB
## Quality infrastructure
## Mismatches with the user's description
## Open questions (each with options and a recommended default)
```

## T2: Catalog entry (in `02-pattern-catalog.md`)

```markdown
### P13 Narrow-first defaults with enforced bounds  [verified | corrected | unchecked]
- Source: `OpenAI/Codex/gpt-6.1-sol.md:6280`
- Pattern: <one or two sentences>
- Applies to: logs, trace
- In this project: <`file:line` and what you see>  |  not found (searched: <terms>)
```

End the file with an `## Exemplar notes` section (Phase 2 step 2.3).

## T3: Finding

The required lines are Basis, Evidence (with at least one `file:line` citation) and Failure scenario.

```markdown
### F-<COMPONENT>-<NN> <the claim, in under 12 words>
- Component / dimension: <component> / <D-number and name>
- Severity: P0 | P1 | P2
- Basis: observed | inferred
- Evidence: `<file:line>`: <what the code does, in one line>
- Failure scenario: <condition> → <what the code does> → <wrong final result>
- Pattern: <P-numbers>
- Verdict: CONFIRMED | PLAUSIBLE (to confirm: <what>)   (added by Phase 3 step 3.6)
- Fix idea: <one sentence; the details go in the recommendations>
```

## T4: `03-comparison.md`

```markdown
| Dimension | Codex | Claude Code | This project (file:line) | Gap | Severity |
|---|---|---|---|---|---|
```

## T5: Eval case (in `06-eval-plan.md`)

```yaml
id: inc-2026-07-12-checkout-5xx
source: postmortem          # postmortem | fault-injection | seed | synthetic
type: positive              # positive | negative | red-herring
tags: [deploy-regression, checkout]
input:
  alert: "5xx rate > 5% on checkout-api (prod) since 14:05 UTC"
  user_question: "Why is checkout failing?"
window: {start: "2026-07-12T13:50:00Z", end: "2026-07-12T14:30:00Z",
         baseline_start: "2026-07-12T12:50:00Z", baseline_end: "2026-07-12T13:30:00Z"}
fixtures: fixtures/inc-2026-07-12-checkout-5xx/
expected:
  root_cause_service: checkout-api
  culprit_change: {type: commit, sha: "<sha>"}   # or "not-code-related"
  onset_utc: "2026-07-12T14:03:00Z"
  onset_tolerance_min: 5
  fix_category: rollback        # rollback | config | scale | dependency | code-fix
  must_cite: ["<sha>"]
```

## T6: Recommendation (in `07-recommendations.md`)

The required lines are Problem, Change and Measure.

```markdown
### R-<NN> <title> (P0 | P1 | P2, effort S | M | L)
- Problem: <one or two sentences> (findings: <IDs>)
- Change: <what changes, and where>
- Pattern: <P-numbers>
- Expected effect: <what gets better>
- Measure: <eval metric and the expected direction>
- Risk: <what could break>
- Draft: 08-drafts/<file>
```

## T7: `00-summary.md`

```markdown
# Summary
<3 to 5 sentences: the overall verdict, the weakest component, the top risk>
## Scorecard
| Dimension | Orch | Logs | Trace | Metrics | Code | KB |
## Top 10 changes
| # | Change | Fixes | Impact | Effort |
## What I could not verify
## Next step
```

## T8: `scorecards/<component>.md`

```markdown
# Scorecard: <component>
Reviewed: <UTC date> · Basis: static review of <commit SHA or "working tree">

## Checklist (Appendix B)
| Item | Status | Evidence | Basis |
|---|---|---|---|
| LOGS-1 Absolute window | partial | `<file:line>` | observed |

## Scores (Appendix C)
| Dimension | Score | Reason | Evidence |
|---|---|---|---|

## Findings
### F-<COMPONENT>-01 ...   (Template T3)

## Likely works when / likely fails when
- Works when: ...
- Fails when: <condition> → <wrong result>

## Refuted during verification
- <finding ID>: <why it was refuted, with the `file:line` that disproves it>
```

## T9: `verification.md` and `verification.json`

```markdown
# Verification
| Finding | Verdict | Proof or counter-evidence (`file:line`) | To confirm |
|---|---|---|---|
```

```json
{"checked": 14, "confirmed": 8, "plausible": 4, "refuted": 2, "remaining_unverified": 0}
```

## T10: `04-agent-scorecards.md` (index)

```markdown
# Scorecards
| Dimension | Orch | Logs | Trace | Metrics | Code | KB | Graph |
|---|---|---|---|---|---|---|---|

## Findings by severity
| Component | P0 | P1 | P2 | File |
|---|---|---|---|---|
```
