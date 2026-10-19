# Incident report: <short title>

| Field | Value |
|---|---|
| Incident id | |
| Severity | SEV1 / SEV2 / SEV3 (state the rule used) |
| Status | investigating / mitigated / resolved / cause not established |
| Window (UTC) | start .. end |
| Services affected | |
| Prepared by / date | |
| Evidence confidence | high / medium / low (from the scoring script, see section 9) |

## 1. Summary
Three lines: what users saw, how long, and the current best explanation. Say "cause not established" if it is.

## 2. Impact
Who and what was affected, measured: error rate, requests failed, latency, users, money, data. Give the number and the query that produced it.

## 3. Timeline (UTC)
| Time | Event | Source (query, trace id, deploy id) |
|---|---|---|

## 4. Evidence collected
### Logs
Query, window, top signatures with counts, and one example each.
### Metrics
Query, window, baseline versus incident values (min / max / when).
### Traces
Trace ids, the slowest or failing span, which service and operation.
### Changes just before the first anomaly
Deploys, config changes, dependency changes, traffic changes (with how you found them).

## 5. Analysis
| # | Hypothesis | Supporting evidence | Evidence against | Check that would confirm or reject it | Status (confirmed / plausible / rejected / untested) |
|---|---|---|---|---|---|

## 6. Cause
Only if a hypothesis is confirmed: the mechanism, the trigger, and why it appeared at that time. Otherwise write "Not established" and list what is missing.

## 7. Where to look next
Ordered list: what to check, where, why, and who can do it. Do not run any change yourself.

## 8. Recommended actions
| Action | Type (mitigation / fix / detection / process) | Owner | Evidence it is needed |
|---|---|---|---|

## 9. Gaps and limits
Data you could not get (retention, sampling, no access), queries that returned nothing, and what that does to confidence.

## 10. Appendix
Exact queries and commands used, in the order run.
