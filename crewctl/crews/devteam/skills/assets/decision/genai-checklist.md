# GenAI decision checklist

Use for any decision about a GenAI application: model choice, RAG versus fine-tuning versus prompting, agent design, guardrails, rollout.
Fill one checklist per decision as JSON for `score_decision.py` (see `score_decision.py example`). Weights: 3 = decisive, 2 = important, 1 = nice to have.
`blocker: true` means "do not proceed if not met". A "yes" needs evidence: a file and line, a test, a measurement, a link.

| ID | Question | Weight | Blocker |
|----|----------|--------|---------|
| G1 | Is the task written down with a measurable success criterion? | 3 | yes |
| G2 | Is there a labelled evaluation set, at least 100 cases, with cases from real usage? | 3 | yes |
| G3 | Has a non-LLM baseline (rules, search, classic model) been measured on that set? | 3 | |
| G4 | Does the chosen design beat the baseline by more than the 95% interval width (use `wilson`)? | 3 | |
| G5 | Is every factual answer traceable to a source (retrieval hit, tool result, document)? | 2 | |
| G6 | Has a test with hostile input been run (prompt injection in documents and tool output, jailbreak, data exfiltration)? | 3 | yes |
| G7 | Are tool permissions the minimum, and are writes and deletes gated by a person or a policy? | 3 | yes |
| G8 | Is personal or confidential data handled per policy (what leaves the boundary, retention, logging)? | 3 | yes |
| G9 | Are latency (p50/p99) and cost per request measured against a budget? | 2 | |
| G10 | Is there a fallback when the model fails, times out, or refuses? | 2 | |
| G11 | Are prompts, model versions and eval results versioned so a regression can be found? | 2 | |
| G12 | Is there tracing of each step (inputs, outputs, tokens, tool calls) in production? | 2 | |
| G13 | Is a regression eval run on every prompt or model change? | 2 | |
| G14 | Has a human reviewed a sample of failures, and were the failure modes written down? | 1 | |
| G15 | Is a model upgrade or provider outage plan documented? | 1 | |

Statistics to report with any quality claim (never a bare percentage):
- n (cases), successes, rate, and the 95% interval from `score_decision.py wilson K N`.
- When comparing two designs on the same cases, report the cases where they disagree and how each side did there. Overlapping intervals mean "no demonstrated difference".
- n below 30 is anecdotal. Say so.

Report the confidence exactly as the script prints it. Do not add words like "fairly confident" and never invent a percentage.
