---
name: incident-evidence-brief
description: Use when asked what happened during an incident or alert window. Collects evidence from logs, metrics and traces, then writes a short timeline brief where every claim cites its source. Read-only.
triggers: incident brief, what happened at, alert investigation, outage timeline, post-incident evidence, root cause evidence
---

# Incident evidence brief

Answer "what happened between T1 and T2 on service X" using only data you retrieved.

## Steps

1. Pin the window and the service. If either is missing, ask once. Use UTC.
2. Gather in this order, narrowing each time: error and warning log signatures (count per signature), then metric changes in the same window (rate, errors, latency, saturation), then a few traces for the slowest or failed requests.
3. For each signature or anomaly record: first seen, last seen, count, one example line or span id.
4. Find what changed just before the first anomaly: deploys, config changes, dependency errors. Look at the change record, do not guess.
5. Separate what the data shows from what it suggests. Mark each hypothesis with the check that would confirm it.

## Output

- **Summary** (3 lines max).
- **Timeline** as `time (UTC) | event | source` where source is a log query, metric name or trace id.
- **Hypotheses** with the confirming check for each.
- **Gaps:** data you could not get.

## Gotchas

- Large windows: sample by signature first, never read raw logs end to end.
- Do not name a root cause unless a step in the timeline shows it; say "not established".
