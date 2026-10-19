#!/usr/bin/env python3
"""Say whether enough is known about a project to design its architecture.

    context_gate.py init               print an empty context file (JSON) to fill in
    context_gate.py check FILE.json    print what is still missing

Each topic holds {"answer": "...", "source": "user" | "assumed"}. Only an answer the user gave (or confirmed)
counts. An "assumed" answer is listed as still needing confirmation, so a guess never passes the gate.
Exit codes: 0 ready, 3 not ready, 2 bad input.
"""
import json
import sys

TOPICS = {
    "goal": "What problem does the system solve, and for whom?",
    "users_and_scale": "Number of users, requests per second (average and peak), growth in 12 months",
    "data": "Kinds of data, volume now and later, retention, who owns it",
    "latency_and_availability": "Latency targets (p50/p99) and availability target; what a failure costs",
    "consistency": "Where strong consistency is required, where eventual is fine",
    "integrations": "Systems it talks to, protocols, who controls their contracts and limits",
    "team": "Team size, languages and tools they know, on-call capacity",
    "runtime_environment": "Where it runs (cloud, on-premises, edge), orchestration, who operates it",
    "security_and_compliance": "Authentication, sensitive data, regulations, audit needs",
    "budget_and_timeline": "Cost ceiling, deadline, what can be cut first",
    "existing_system": "What exists today, what must be kept, migration constraints",
    "observability_and_operations": "Logging, metrics, tracing, alerting, deployment frequency, rollback expectations",
}


def check(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    missing, unconfirmed = [], []
    for topic, question in TOPICS.items():
        entry = data.get(topic)
        answer = str((entry or {}).get("answer", "")).strip() if isinstance(entry, dict) else ""
        source = (entry or {}).get("source") if isinstance(entry, dict) else None
        if not answer:
            missing.append({"topic": topic, "ask": question})
        elif source != "user":
            unconfirmed.append({"topic": topic, "assumed": answer, "ask": f"Confirm or correct: {answer}"})
    return {"ready": not missing and not unconfirmed, "missing": missing, "needs_confirmation": unconfirmed,
            "answered": len(TOPICS) - len(missing) - len(unconfirmed), "total": len(TOPICS)}


def main(argv):
    try:
        if argv == ["init"]:
            print(json.dumps({t: {"answer": "", "source": "user"} for t in TOPICS}, indent=2))
            return 0
        if len(argv) == 2 and argv[0] == "check":
            with open(argv[1], encoding="utf-8") as handle:
                result = check(json.load(handle))
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 3
        print(__doc__, file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
