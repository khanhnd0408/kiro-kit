#!/usr/bin/env python3
"""Write a fake Kiro Crew home with a few hours of made-up usage, in Crew's own file format.

It lets you try `crewctl usage push` and the dashboards without spending credits:

    python3 make_demo_crew_home.py /tmp/demo-crew
    crewctl --crew-home /tmp/demo-crew usage push --dry-run

Every name, number and id in it is invented. Run it again with --append to add a newer hour of activity.
"""

import argparse
import datetime
import json
import os
import random

AGENTS = {"orchestrator-agent": "claude-sonnet-4.5", "python-agent": "qwen3-coder-next", "go-agent": "qwen3-coder-next",
          "devsecops-agent": "deepseek-3.2", "inspector-agent": "claude-sonnet-4.5", "spec-agent": "claude-opus-4.6"}
WORKERS = ["python-agent", "go-agent", "devsecops-agent", "inspector-agent", "spec-agent"]
WORKSPACES = ["payments", "platform", "default"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("crew_home", help="folder to write (created if missing)")
    parser.add_argument("--hours", type=int, default=6, help="how many hours of activity, ending now (default 6)")
    parser.add_argument("--sessions", type=int, default=8, help="orchestrator sessions (default 8)")
    parser.add_argument("--append", action="store_true", help="add to an existing demo folder instead of refusing")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    tokens = os.path.join(args.crew_home, "usage", "tokens")
    sessions = os.path.join(args.crew_home, "crew-log", "sessions")
    if os.path.isdir(tokens) and os.listdir(tokens) and not args.append:
        parser.error(f"{args.crew_home} already has usage files; use --append or another folder")
    os.makedirs(tokens, exist_ok=True)
    os.makedirs(sessions, exist_ok=True)
    rnd = random.Random(args.seed if not args.append else None)
    now = datetime.datetime.now().astimezone()
    start = now - datetime.timedelta(hours=args.hours)
    rows = []

    def usage_row(when, slot, agent, model, credits, surface, context=0, duration=0):
        rows.append({"_type": "tokens", "ts": when.isoformat(), "slot": slot, "app": "", "provider": "acp", "model": model,
                     "input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "cost": 0.0, "credits": round(credits, 6),
                     "turns": 0, "duration_ms": duration or rnd.randint(3000, 60000), "surface": surface, "agent": agent,
                     "context_used": context, "context_window": 200000 if context else 0,
                     "stop_reason": "" if surface.startswith("bg:") else "end_turn"})

    for number in range(args.sessions):
        opened = start + datetime.timedelta(seconds=rnd.randint(0, int(args.hours * 3600 * 0.8)))
        sid = f"demo{rnd.getrandbits(64):016x}"
        slot = f"chat-{number + 1}-{int(opened.timestamp())}"
        workspace = rnd.choice(WORKSPACES)
        log = [{"type": "session", "version": 1, "id": sid, "owner": "default", "agent": "orchestrator-agent", "slot": slot,
                "cwd": "/demo", "createdAt": int(opened.timestamp() * 1000)},
               {"type": "session/opened", "seq": 1, "time": int(opened.timestamp() * 1000), "src": "gateway",
                "data": {"agent": "orchestrator-agent", "slot": slot, "model": AGENTS["orchestrator-agent"],
                         "class": {"memory": "persistent", "workspace": workspace}}}]
        seq = 2
        when = opened
        context = rnd.randint(30000, 45000)
        for _ in range(rnd.randint(3, 9)):
            when += datetime.timedelta(minutes=rnd.uniform(1, 6))
            if when > now:
                break
            context = min(190000, context + rnd.randint(2000, 12000))
            usage_row(when, slot, "orchestrator-agent", AGENTS["orchestrator-agent"], rnd.uniform(0.15, 0.9), "dashboard", context)
            if rnd.random() < 0.6:
                worker = rnd.choice(WORKERS)
                kid = f"{rnd.getrandbits(64):016x}"
                spawned = when + datetime.timedelta(seconds=5)
                finished = spawned + datetime.timedelta(minutes=rnd.uniform(0.5, 12))
                if finished > now:
                    continue
                credits = rnd.uniform(0.05, 0.4) if AGENTS[worker] in ("qwen3-coder-next", "deepseek-3.2") else rnd.uniform(0.6, 3.0)
                log.append({"type": "subagent/spawned", "seq": seq, "time": int(spawned.timestamp() * 1000), "src": "gateway",
                            "data": {"agent_id": kid, "agent": worker, "model": AGENTS[worker]}})
                outcome = "completed" if rnd.random() < 0.85 else rnd.choice(["failed", "stopped"])
                closer = {"agent_id": kid, "ms": int((finished - spawned).total_seconds() * 1000), "credits": round(credits, 6)}
                if outcome != "completed":
                    closer["outcome"] = outcome
                log.append({"type": "subagent/completed" if outcome == "completed" else "subagent/failed", "seq": seq + 1,
                            "time": int(finished.timestamp() * 1000), "src": "gateway", "data": closer})
                seq += 2
                usage_row(finished, f"subagent:{kid}", worker, AGENTS[worker], credits, "subagent", rnd.randint(20000, 120000),
                          int((finished - spawned).total_seconds() * 1000))
        folder = os.path.join(sessions, f"{sid}-{rnd.getrandbits(32):08x}")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "log.jsonl"), "a", encoding="utf-8") as handle:
            for item in log:
                handle.write(json.dumps(item) + "\n")
    when = start
    while when < now:
        usage_row(when, "_bg", "", "auto", rnd.uniform(0.01, 0.6), rnd.choice(["bg:tips", "bg:suggestions", "bg:bg_oneliner"]))
        when += datetime.timedelta(minutes=rnd.uniform(5, 25))
    when = start + datetime.timedelta(minutes=30)
    while when < now:
        usage_row(when, "cron:nightly-scan", "devsecops-agent", AGENTS["devsecops-agent"], rnd.uniform(0.1, 0.5), "cron", 25000)
        when += datetime.timedelta(hours=1)
    rows.sort(key=lambda r: r["ts"])
    by_day = {}
    for item in rows:
        by_day.setdefault(item["ts"][:10], []).append(item)
    for day, items in by_day.items():
        with open(os.path.join(tokens, f"{day}.jsonl"), "a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item) + "\n")
    total = sum(r["credits"] for r in rows)
    print(f"Wrote {len(rows)} usage rows ({total:.2f} credits) and {args.sessions} sessions to {args.crew_home}")


if __name__ == "__main__":
    main()
