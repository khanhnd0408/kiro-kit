# Workflow brief: agent review, Phases 2 to 5 (optional)

Kiro Crew Workflows are Python scripts that Crew's agent writes for you from a plain-English description. The script API isn't published, so don't write the script by hand. Send the brief below in a Crew chat whose project is this repository. Then validate the script in the Workflows app, save it to the library as `agent-review`, and run it with `/workflow agent-review`.

You need the Workflows app first: `kirocrew app enable workflows`. If you don't want workflows, use the Task Runner spec `TASK-review.md` instead; it covers the same work.

---

agent-review: Create a Crew workflow named `agent-review` that runs Phases 2 to 5 of the method in `docs/agent-review-method/`. Use the stages and rules below, and every task description in `docs/agent-review-method/crew/TASK-review.md`.

Stages:

1. **catalog:** one agent does task 1 (pattern catalog).
2. **specialist reviews:** fan out six agents in parallel for tasks 2 to 7 (logs, trace, metrics, code, KB, LangGraph). Each one writes only its own scorecard file.
3. **orchestrator review:** one agent does task 8, after all six have finished.
4. **judge-and-verify:** a separate agent does task 9. It must not be any agent that wrote a scorecard, and it tries to refute the findings. Repeat this stage at most twice, and stop as soon as `docs/agent-review/verification.json` has `"remaining_unverified": 0`.
5. **compare-and-audit:** one agent does task 10.
6. **eval plan:** one agent does task 11.
7. **recommendations:** one agent does task 12.

Rules for every agent call:

- Start the prompt with: "You are one step of the agent-review workflow. Read `docs/agent-review-method/00-rules.md` (section 3, Task Runner mode, also applies to workflow steps) and `docs/agent-review-method/inputs.md` first."
- Give each agent only the files its task names, and the paths it must write.
- After each stage, run `python3 -I tools/agent_review/check_citations.py`. If it fails, send the problems back to the same stage once.

Done means:

- every deliverable listed in `TASK-review.md` exists;
- the citation checker exits 0;
- nothing outside `docs/agent-review/` changed.

Before saving, show me the script and a summary of the stages.
