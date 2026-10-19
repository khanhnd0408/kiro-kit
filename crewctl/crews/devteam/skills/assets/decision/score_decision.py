#!/usr/bin/env python3
"""Turn a filled-in decision checklist into a confidence level. No judgement, no guessing.

    score_decision.py score FILE.json      read the checklist, print the result as JSON
    score_decision.py wilson K N           95% Wilson interval for K successes out of N trials
    score_decision.py example              print a small example input

Input: {"decision": "...", "items": [{"id": "C1", "question": "...", "weight": 3, "blocker": true,
        "status": "yes|no|unknown|n/a", "evidence": "file:line, test name, measurement, link"}]}

Rules (fixed, so two runs on the same input always agree):
  - A "yes" with no evidence counts as "unknown".
  - coverage = weight of "yes" / weight of all items that apply (not "n/a").
  - If any blocker item is not "yes", confidence is "blocked".
  - high: coverage >= 0.85 and unknown weight <= 10%. medium: coverage >= 0.60 and unknown <= 30%. Otherwise low.
Exit codes: 0 done, 2 bad input.
"""
import json
import math
import sys

STATUSES = ("yes", "no", "unknown", "n/a")
EXAMPLE = {"decision": "Use retrieval-augmented generation for the support bot", "items": [
    {"id": "C1", "question": "Is there a labelled evaluation set of at least 100 cases?", "weight": 3, "blocker": True,
     "status": "yes", "evidence": "evals/support.jsonl (240 cases)"},
    {"id": "C2", "question": "Does it beat a keyword-search baseline on that set?", "weight": 3, "status": "unknown", "evidence": ""},
    {"id": "C3", "question": "Is every answer traceable to a source passage?", "weight": 2, "status": "no", "evidence": ""}]}


def wilson(successes, trials, z=1.96):
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("need 0 <= K <= N and N > 0")
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return {"successes": successes, "trials": trials, "rate": round(p, 4), "low": round(max(0.0, centre - half), 4),
            "high": round(min(1.0, centre + half), 4), "note": "fewer than 30 trials: treat as anecdotal" if trials < 30 else ""}


def score(data):
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not data["items"]:
        raise ValueError("input needs a non-empty 'items' list")
    totals = {"yes": 0.0, "no": 0.0, "unknown": 0.0}
    notes, blockers, seen = [], [], set()
    for index, item in enumerate(data["items"], 1):
        if not isinstance(item, dict):
            raise ValueError(f"item {index} is not an object")
        ident = str(item.get("id") or f"#{index}")
        if ident in seen:
            raise ValueError(f"duplicate id {ident}")
        seen.add(ident)
        status = item.get("status")
        if status not in STATUSES:
            raise ValueError(f"{ident}: status must be one of {', '.join(STATUSES)}")
        weight = item.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError(f"{ident}: weight must be a positive number")
        if status == "yes" and not str(item.get("evidence") or "").strip():
            status = "unknown"
            notes.append(f"{ident}: marked yes without evidence, counted as unknown")
        if item.get("blocker") and status != "yes" and status != "n/a":
            blockers.append(ident)
        if status != "n/a":
            totals[status] += weight
    applicable = sum(totals.values())
    if applicable == 0:
        raise ValueError("every item is n/a; nothing to score")
    coverage = totals["yes"] / applicable
    unknown_share = totals["unknown"] / applicable
    if blockers:
        level = "blocked"
    elif coverage >= 0.85 and unknown_share <= 0.10:
        level = "high"
    elif coverage >= 0.60 and unknown_share <= 0.30:
        level = "medium"
    else:
        level = "low"
    open_items = [str(i.get("id") or f"#{n}") for n, i in enumerate(data["items"], 1) if i.get("status") in ("no", "unknown")
                  or (i.get("status") == "yes" and not str(i.get("evidence") or "").strip())]
    return {"decision": data.get("decision", ""), "confidence": level, "coverage": round(coverage, 3),
            "unknown_share": round(unknown_share, 3), "weights": {k: round(v, 3) for k, v in totals.items()},
            "blockers_not_met": blockers, "open_items": open_items, "notes": notes,
            "rule": "high: coverage>=0.85 and unknown<=0.10; medium: coverage>=0.60 and unknown<=0.30; blocker not met => blocked"}


def main(argv):
    try:
        if len(argv) >= 2 and argv[0] == "score":
            with open(argv[1], encoding="utf-8") as handle:
                print(json.dumps(score(json.load(handle)), indent=2))
        elif len(argv) == 3 and argv[0] == "wilson":
            print(json.dumps(wilson(int(argv[1]), int(argv[2])), indent=2))
        elif argv == ["example"]:
            print(json.dumps(EXAMPLE, indent=2))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
