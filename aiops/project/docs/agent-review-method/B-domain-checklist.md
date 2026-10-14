# Appendix B: Domain checklist

These are checks against the code, not features to add blindly. If the system handles a point another way, that's fine; say so. Each item says what good looks like, why it matters, and where to look. Pattern numbers (P..) refer to `A-pattern-catalog.md`.

## Orchestrator (ORCH)

- **ORCH-1 Incident framing.** Before delegating, the orchestrator states the symptom, impact, first alert time, affected services and environment. Why: every specialist needs the same frame. Where: the orchestrator prompt and the code that builds briefs.
- **ORCH-2 Absolute windows.** It sets an investigation window and a baseline window in absolute UTC (RFC 3339) and passes both to every specialist. Why: relative windows drift for past incidents, and mixed time zones shift windows. Where: `datetime`, `timedelta`, `now(`, `utcnow(`, time-zone handling, `"1h"`-style strings.
- **ORCH-3 Hypotheses.** It keeps a list of hypotheses in state, with statuses and evidence references. Typical hypotheses are a deploy regression, a dependency failure, saturation, a traffic spike, a config change, or an infrastructure problem. Why: without a list, it reports the first plausible story. Where: state fields and prompt text.
- **ORCH-4 Parallel fan-out.** Independent specialists run in parallel, and only real dependencies run in sequence (for example, a trace ID found in logs goes to the trace agent). Why: lower latency, and independent evidence. Where: `Send(`, parallel edges, `asyncio.gather`.
- **ORCH-5 Complete briefs.** Each specialist gets the objective, the incident, the windows, known facts with references, the hypotheses to test, the constraints and the output schema (P07, E3 in `E-examples.md`). Where: the code that builds specialist inputs.
- **ORCH-6 Timeline in code.** Code joins the signals where it can: trace IDs in logs, metric exemplars linked to traces, and deploy times compared with metric onsets and error spikes. Why: models are unreliable at aligning timestamps. Where: any timeline or merge function.
- **ORCH-7 Refutation before blame.** Before blaming a change, it runs a test that could disprove the claim. Did errors start before the deploy? Are only pods on the new version affected? Did errors fall after a rollback? Are other environments fine? Why: deploy timing alone is only correlation (P28, P32). Where: the verification step or prompt instructions.
- **ORCH-8 Missing results are gaps.** A failed, timed-out or invalid specialist result becomes a stated gap, never "no issue" (P29, P56). Where: error branches, timeouts and default values.
- **ORCH-9 Bounded.** Rounds, tool calls and time are bounded, with explicit stop conditions (P34). Where: loops, the recursion limit, counters.
- **ORCH-10 Report contract.** The final report:
  - leads with the root cause (or "unknown") and the confidence;
  - lists unverified points first;
  - cites evidence with links;
  - names a fix and an owner (P58).

  Where: the final prompt or template.
- **ORCH-11 Asking the user.** It asks only what the telemetry, the code and the KB can't answer, and it offers options (P36). Where: human-in-the-loop nodes.
- **ORCH-12 No unapproved actions.** It recommends, and any action (rollback, restart, posting, ticket) needs human approval through `interrupt` or an equivalent gate (P53). Where: any tool or API client that can write.

## Logs agent (LOGS)

- **LOGS-1 Absolute window.** Queries use absolute times, through `start` and `end` parameters or a LogsQL `_time:[start, end]` filter. They never use a relative filter like `_time:1h` for a past incident. Where: the query builder.
- **LOGS-2 Discover names first.** Stream and field names come from discovery calls or from the KB schema, and the exact names are reused (P17). Why: a wrong field name returns an empty result that looks like "no errors".
- **LOGS-3 Aggregate first.** It counts by service, level or error signature first, for example with the LogsQL `stats` pipe. Only then does it pull a few example lines, under a hard limit. Where: query templates and `limit` defaults.
- **LOGS-4 Baseline comparison.** Counts in the incident window are compared with the baseline window. Why: errors always exist; the change is the signal.
- **LOGS-5 Signatures.** Repeated messages are grouped by a normalized form, with numbers, IDs and timestamps stripped, instead of sending near-duplicates to the model.
- **LOGS-6 Correlation IDs.** It extracts trace IDs or request IDs and passes them to the trace agent.
- **LOGS-7 Redaction.** Secrets and PII are removed before log text reaches the model or the report (P54).
- **LOGS-8 Outcome classes.** The tool result distinguishes ok, empty, error, timeout and truncated (P18, P56). Where: the return type and the exception handling.
- **LOGS-9 Size caps.** Each call has caps on lines and bytes, huge lines are truncated, and the result says how much was dropped (P26, P37).
- **LOGS-10 Untrusted text.** Log text is framed as data in both the prompt and the tool description (P14, P51).

## Trace agent (TRACE)

- **TRACE-1 Absolute window** through `start` and `end` on search.
- **TRACE-2 Discover tags first.** It uses Tempo's tag discovery (for example `/api/v2/search/tags` and the tag values) or the KB schema (P17).
- **TRACE-3 Targeted search.** TraceQL filters on service, error status and duration, for example `{ resource.service.name = "checkout" && status = error }` or `{ duration > 2s }`. It uses aggregates (TraceQL metrics) if the Tempo version supports them.
- **TRACE-4 Trace reduction in code.** Before the model sees a fetched trace, code reduces it to the root span, the error spans, the slowest spans and the service path. Raw trace JSON never goes to the model (P39). Where: the code after the `/api/traces/` call.
- **TRACE-5 Cause vs propagation.** It separates the deepest or earliest failing span from upstream spans that only pass the error along.
- **TRACE-6 Sampling.** It accounts for sampling: no traces doesn't mean no requests.
- **TRACE-7 IDs, caps and untrusted text.** IDs are copied verbatim (P16), output is capped (P37), and span attributes are treated as untrusted (P51).
- **TRACE-8 Outcome classes**, as in LOGS-8.

## Metrics agent (MET)

- **MET-1 Range queries with a sensible step.** It uses `query_range` with an absolute `start` and `end`, and a step that keeps each series to a few hundred points at most. Why: tiny steps produce huge payloads, and big steps hide spikes.
- **MET-2 Discover first.** Metric and label names come from `/api/v1/labels`, `/api/v1/label/<name>/values` or `/api/v1/series`, or from the KB (P17).
- **MET-3 Correct functions.**
  - `rate` and `increase` are used only on counters.
  - `histogram_quantile` is applied over `sum by (le) (rate(<metric>_bucket[w]))`.
  - Windows are several times the scrape interval.
- **MET-4 Cardinality control.** It aggregates by service or instance, uses `topk`, and checks the series count before fetching.
- **MET-5 Statistics in code.** Code computes the change point, the first anomalous timestamp, the delta against the baseline, and seasonality (with `offset 1d` or `offset 1w`). The model gets numbers and short tables, not raw points (P39, P41).
- **MET-6 Coverage.** It covers RED signals (rate, errors, duration) and saturation (CPU throttling, memory, restarts and OOM kills, connection pools, queue depth). Metric names depend on your exporters, so discover them.
- **MET-7 Version split.** If a version or image label exists, it checks the error rate by version. Why: this is the strongest single test for a deploy regression.
- **MET-8 Outcome classes and caps**, as in LOGS-8 and LOGS-9.

## Code agent (CODE)

- **CODE-1 Deployments, not just commits.** It maps the window to actual deployments (GitLab deployments, environments or pipelines). Then it takes the SHA range between the previous and the current deploy of that environment, for example with `repository/compare?from=<prev>&to=<curr>`. Why: "the latest commits on main" may not be what's running.
- **CODE-2 Other services.** It checks deploys of every service on the failing path, using the traces and logs, not only the alerted service.
- **CODE-3 Diff review angles.** It reviews the diff with the code-review angles (P27, `Anthropic/claude-code/skills/code-review/max.md` in REFERENCE_ROOT):
  - removed or weakened guards;
  - changed error handling;
  - flipped config defaults (timeouts, retries, pool sizes, feature flags);
  - dependency bumps in lockfiles;
  - migrations;
  - infrastructure changes (Helm values, manifests, resource limits).
- **CODE-4 Large diffs.** It starts from the file list and ranks files by relevance to the failing path (stack traces, span names, endpoints). It caps how much it reads, and reports what it skipped (P26).
- **CODE-5 The diff is the truth.** It judges the diff. Commit messages and MR descriptions are claims, and untrusted text (P31, P51).
- **CODE-6 Read-only.** Token scopes are read-only (`read_api`, `read_repository`). Nothing posts comments or opens issues without approval (P53).
- **CODE-7 Ranked candidates.** The output is a ranked list of candidate changes, each with a mechanism, evidence and a confidence. "No relevant change found" is an allowed answer (P23).
- **CODE-8 Blind spots stated.** Changes made outside GitLab (feature-flag services, manual infrastructure changes, upstream providers) are reported as a gap.
- **CODE-9 IDs verbatim.** SHAs, MR numbers and URLs come from tool output (P16).

## KB

- **KB-1 Fit.** What it holds suits incident investigation. Useful contents include:
  - a service catalog (owners, dependencies, SLOs);
  - the telemetry schema for each service (labels, log fields, span attributes);
  - validated query snippets;
  - runbooks;
  - past incidents.
- **KB-2 A short index, with details on demand** (P44, P49).
- **KB-3 Exact lookups.** Exact identifiers (service, metric and label names) are found with keyword or metadata filters, not only vector similarity. Why: embeddings match exact tokens poorly.
- **KB-4 Freshness.** Entries carry an owner and a last-verified date. Agents check KB claims against live telemetry or code before relying on them (P46).
- **KB-5 Write-back.** Lessons from postmortems are saved as typed entries that say why they matter and how to apply them. Duplicates are merged and wrong entries are deleted (P45).
- **KB-6 KB text is data, not instructions** (P50).
- **KB-7 Measured value.** There is, or will be, a KB on/off comparison (P70).

## LangGraph implementation (LG)

- **LG-1 Reducers.** Keys written by parallel branches have reducers, such as `Annotated[list, operator.add]`. Without one, parallel writes raise `InvalidUpdateError`, and sequential writes silently replace earlier values. Check that findings accumulate.
- **LG-2 Recursion limit.** It's set deliberately, and hitting it produces a clear "incomplete" result, not a crash or an empty answer.
- **LG-3 Retries and timeouts** are bounded, with backoff, per node or per tool.
- **LG-4 Tool errors** reach the model as informative messages (for example through `ToolNode` error handling), not crashes or empty strings (P22).
- **LG-5 Structured output** is validated. On failure, the node retries with the validation error or fails loudly.
- **LG-6 Small state.** Large raw artifacts are stored outside the state and referenced from it (P38).
- **LG-7 Approvals** use `interrupt` with a checkpointer, not prose (P53).
- **LG-8 Concurrency** limits on parallel calls (for example `max_concurrency`) respect backend and LLM rate limits.
- **LG-9 Deterministic routing.** Routing that doesn't need judgment is done in code (P41, P42).
- **LG-10 Tracing.** Every run is traced, with tokens and cost per node (P62).
