# Inputs

Fill in what you know before Phase 1. Leave a value blank and the agent will discover it from the code. `install.sh` sets REFERENCE_ROOT.

```text
REFERENCE_ROOT:      ~/.kiro-agent-review/reference
DELIVERABLE_DIR:     docs/agent-review/
REPORT_LANGUAGE:     English, plain and short sentences
PROJECT_LANGUAGE:    <Python | TypeScript | detect>
MODELS_IN_USE:       <provider/model per node; blank = detect from code>
AGENT_TRACING:       <LangSmith | Langfuse | OpenTelemetry | none | unknown>
PAST_INCIDENTS:      <where postmortems or incident tickets live, or "none">
STAGING_FOR_FAULTS:  <yes/no: can faults be injected in a non-prod environment?>
KB_NOTES:            <what the KB holds and how agents use it; blank = discover>
KNOWN_PAIN_POINTS:   <anything I have seen go wrong>
```

REFERENCE_ROOT is a folder outside this repository. It holds a curated copy of the Codex and Claude Code files that the method cites. Every `Anthropic/...` and `OpenAI/...` path in the method is relative to it.
