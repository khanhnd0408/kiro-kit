# Appendix F: Report schemas

These are starting shapes for the drafts in Phase 5. Adapt the names and fields to the codebase; they aren't requirements.

```python
class Evidence(BaseModel):
    kind: Literal["log", "trace", "metric", "commit", "merge_request", "pipeline", "kb"]
    ref: str            # exact ID, SHA or query copied from a tool result, never invented
    excerpt: str        # a few lines at most
    link: str | None    # Grafana Explore, Tempo or GitLab URL when available

class Finding(BaseModel):
    claim: str                                    # one sentence
    basis: Literal["observed", "inferred"]
    evidence: list[Evidence]
    services: list[str]
    onset_utc: datetime | None
    mechanism: str                                # condition or state, then the symptom
    confidence: Literal["high", "medium", "low"]
    verdict: Literal["CONFIRMED", "PLAUSIBLE"] | None = None   # set by the verify step

class QueryRecord(BaseModel):
    backend: str
    query: str
    start: datetime
    end: datetime
    outcome: Literal["ok", "empty", "error", "timeout", "truncated"]
    summary: str

class Gap(BaseModel):
    what: str
    reason: Literal["no_data", "backend_error", "timeout", "truncated", "out_of_scope", "budget"]

class SpecialistReport(BaseModel):
    agent: Literal["logs", "traces", "metrics", "code"]
    status: Literal["complete", "partial", "failed"]
    findings: list[Finding]   # an empty list is a valid answer
    gaps: list[Gap]
    queries: list[QueryRecord]
```

The delegation brief uses the shape of E3 in `E-examples.md`: objective, incident, window, known so far, hypotheses, constraints, return.
