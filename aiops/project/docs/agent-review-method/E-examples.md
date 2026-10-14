# Appendix E: Examples

These examples are illustrative, and the file names in them are made up. Match their level of detail, not their wording.

## E1: Weak and strong findings

Weak: "The logs agent should handle errors better." It has no location, no scenario and no evidence, and there's no way to verify it.

Strong:

```markdown
### F-LOGS-03 Backend errors are reported as "no errors found"
- Component / dimension: logs / D13 Failure handling
- Severity: P0
- Basis: observed
- Evidence: `agents/logs/tools.py:88`: the `except httpx.HTTPError` branch returns `[]`; the return type has no status field.
- Failure scenario: VictoriaLogs returns 502 during the incident → the tool returns `[]` → the agent writes "no error logs in the window" → the orchestrator rules out the logs signal and blames the wrong service.
- Pattern: P56, P18
- Verdict: CONFIRMED (line 88 returns `[]` in the error branch; no caller checks for errors)
- Fix idea: return an outcome field (ok, empty, error, timeout, truncated) and have the prompt treat `error` as a gap.
```

## E2: Weak and strong recommendations

Weak: "Improve the metrics agent prompt."

Strong:

```markdown
### R-04 Compute change points in code and give the model numbers (P1, effort M)
- Problem: the metrics tool returns raw series (up to about 11,000 points per call, `agents/metrics/tools.py:40-61`), and the prompt asks the model to "find anomalies" (F-MET-02).
- Change: add `summarize_series()`, which returns for each series the baseline mean, the incident maximum, the percent change, and the first timestamp above a threshold. Return the 10 series with the largest change. Keep the raw-series tool for drill-down.
- Pattern: P39, P41
- Expected effect: fewer tokens per call and more accurate onset times.
- Measure: onset-time error in minutes, and tokens per metrics call, on the eval.
- Risk: a fixed threshold can miss slow drifts, so keep the drill-down path.
- Draft: 08-drafts/metrics.prompt.md, 08-drafts/tool-descriptions.md
```

## E3: A filled-in delegation brief

```text
Objective:    Decide whether checkout-api's 5xx errors come from its own code or from a downstream dependency.
Incident:     5xx rate on checkout-api (prod) rose from 0.2% to 9% at about 14:05 UTC; users can't pay. First alert 14:07 UTC.
Window:       incident 2026-07-12T13:50:00Z to 2026-07-12T14:30:00Z; baseline 2026-07-12T12:50:00Z to 2026-07-12T13:30:00Z
Known so far: metrics: 5xx onset at 14:03:40Z (query ref M-2). code: checkout-api deployed sha 4f2c9e1 at 14:02:10Z (deployment 8812).
Hypotheses:   H1: the 14:02 deploy broke checkout-api. Confirm: errors only on pods running 4f2c9e1. Refute: errors also on old pods, or onset before 14:02.
              H2: payment-gateway is failing. Confirm: downstream spans time out or error first.
Constraints:  read-only; at most 10 tool calls; at most 20 raw log lines in the report.
Return:       SpecialistReport (F-schemas.md)
```

## E4: Observed and inferred

- **Observed:** "`orchestrator/router.py:142` sends work to the code agent only when the alert text contains the word 'deploy'."
- **Inferred:** "So deploy regressions whose alerts don't mention 'deploy', which is most of them, never reach the code agent. To confirm: replay case INC-03 and check the trace for a code-agent call."
