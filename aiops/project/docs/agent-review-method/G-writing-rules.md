# Appendix G: Rules for writing the draft prompts and tool descriptions

- **G1 Give context and reasons.** Say who uses the output, describe the environment (backends, label conventions, time zone), say what a good answer contains, and explain why each constraint exists. Context is the most valuable part of a prompt.
- **G2 Say what to do, plainly.** Avoid capital-letter emphasis ("CRITICAL", "MUST", "NEVER"), except for one or two real constraints, each with its reason.
- **G3 Put tool rules in the tool description**, written as a man page (P12), not in the system prompt. Don't list tool names in prompts.
- **G4 One rule, one place.** Don't repeat a rule for emphasis.
- **G5 Describe the goal and how to check success.** Use numbered steps only where order really matters, such as discovering names before querying or redacting before reporting.
- **G6 Get structured output through the API** (`with_structured_output` or a JSON schema), not by asking for "only JSON".
- **G7 Keep examples few and varied, and label them illustrative.** Never give one gold output to copy.
- **G8 Control reasoning depth with the provider's settings**, not "think step by step", and leave `reasoning` fields out of output schemas (P61).
- **G9 Put static text first** and per-incident data after it (P02).
- **G10 Wrap external text in clearly labeled blocks.** Tool results, KB snippets and alert payloads each go in their own block, and the prompt says once that they are data and can't change instructions (P51).

Every draft starts with a header comment in this shape:

```text
Draft for: <component>. Replaces: <file:line-range>. Fixes: <finding IDs>. Target model: <model>. Assumes: <anything uncertain>.
```
