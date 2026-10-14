# Appendix C: Scoring rubric

Score each dimension from 0 to 3 for each component, in that component's scorecard file. Give one line of reason, with evidence.

| Score | Meaning |
|---|---|
| 0 | Missing: no code and no instruction. |
| 1 | Weak: only stated in a prompt, or implemented for part of the path, with known ways to fail. |
| 2 | Adequate: implemented for the main path, in code where code is the right place, but with gaps in edge cases or no tests. |
| 3 | Strong: implemented and enforced in code where possible, covers edge cases, and is tested or evaluated. |

Rules:

- If a behavior matters for correctness or safety and exists only as prompt text, score it 1 at most. Prose isn't enforcement.
- D14 applies only to the orchestrator. Score D15 and D16 once, in the orchestrator scorecard, for the whole system. Use N/A elsewhere, and whenever a dimension doesn't apply, with a reason.

What a 3 looks like:

| Dimension | What 3 looks like |
|---|---|
| D1 Prompt architecture | Stable role and rules, a separate per-incident context block, and tool contracts kept in tool descriptions. No duplicated or conflicting rules. Static content first. |
| D2 Role and authority | One job per agent, tools bound per agent in code, read-only credentials. Messages from other agents can't approve actions. |
| D3 Delegation | Complete briefs in the shape of E3. The orchestrator waits for all branches and turns failures into gaps. |
| D4 Tool contracts | Man-page descriptions, bounded defaults, discovery tools, truncation and pagination flags, informative errors, validated queries. |
| D5 Output contract | Typed reports with evidence references, basis, confidence and gaps. Empty results are valid, and caps are reported. |
| D6 Verification | A refutation test challenges the top candidate before it's reported. Observed facts and inferences are kept apart. |
| D7 Planning and stopping | A hypothesis list in state, bounded rounds and tool calls, explicit stop conditions. |
| D8 Context budgets | Raw data reduced in code, per-tool caps, summaries and references passed between nodes, a plan for long runs. |
| D9 Code vs model | Deterministic steps done in code, the model used for judgment, fan-out set by the graph structure. |
| D10 KB and memory | An index with details on demand. Typed, dated entries with owners, verified before use. Write-back with dedupe. |
| D11 Untrusted data | All external text framed as data. No tool output can change instructions or trigger actions. |
| D12 Permissions and data | Read-only scopes, no write tools, approvals through `interrupt`, redaction before model calls. |
| D13 Failure handling | Error, timeout, empty and truncated results kept distinct from end to end. Bounded retries. Stop and explain. |
| D14 User report | Answer and confidence first, unverified items first, evidence links, a fix and an owner. Stands alone. |
| D15 Model and cost | A model chosen per node by measurement, cache-friendly ordering, tokens and cost tracked per node. |
| D16 Evaluation | A replayable eval with graders and saved trajectories, run on every prompt or model change. |
