# Phase 2: Pattern catalog from Codex and Claude Code

Goal: a checked catalog of transferable patterns, each mapped to this project.

Inputs: `docs/agent-review/01-system-map.md`, `A-pattern-catalog.md`, REFERENCE_ROOT.

Deliverable: `docs/agent-review/02-pattern-catalog.md` (Template T2 in `D-templates.md`).

## 2.1 Read `A-pattern-catalog.md` in full

It's a catalog of patterns already extracted from REFERENCE_ROOT, each with a source. In this phase you check it, map it to this project, and extend it.

## 2.2 Spot-check the sources

- For each group (A to P), open the cited lines for at least two entries. Read the cited range plus about 10 lines around it. Paths are relative to REFERENCE_ROOT.
- Mark each checked entry `verified`, or `corrected` with the correction written out.
- If any entry in a group turns out wrong, check every entry in that group.
- Entries you didn't open stay `unchecked`.

## 2.3 Read the exemplars

Read these sources and write three to six reuse notes for each in the "Exemplar notes" section of `02-pattern-catalog.md`. You'll reuse their structure when drafting in Phase 5. Paths are relative to REFERENCE_ROOT.

1. `OpenAI/Codex/gpt-6.1-sol.md:6276-6300`, `OpenAI/Codex/gpt-6.1-sol.md:6499` and `OpenAI/Codex/gpt-6.1-sol.md:6515`: the production-log tool and the bounded discovery and read tools, as models for tool descriptions.
2. `Anthropic/claude-code/skills/anthropic-skills/deep-research/SKILL.md:48-123`: decomposition, the brief template, and the bounded extra round.
3. `Anthropic/claude-code/skills/anthropic-skills/deep-research/references/researcher.md:1-76`: the worker loop, stop conditions and notes structure.
4. `Anthropic/claude-code/skills/code-review/max.md:1-162`: diff review angles, verification and the gap sweep.
5. `Anthropic/claude-code/agents/Explore.md:1-50`: the sub-agent definition format.
6. `Anthropic/claude-code/claude-code-opus-5.5.md:346-364` and `Anthropic/claude-code/claude-code-opus-5.5.md:2349-2436`: the delegation contract and the findings schema.
7. `OpenAI/Codex/codex-full.md:1269-1310`: Codex's sub-agent guidance.
8. `Anthropic/claude-code/claude-code-headless-fable-5.1.md:73-101`: working with no user watching, and the final-report rules.
9. `Anthropic/claude-code/skills/workflow-authoring/SKILL.md:120-136`: the quality patterns.
10. `Anthropic/claude-code/skills/deep-research/scripts/workflow-script.js:296-340`: three-outcome verification. Read it; don't run it.

## 2.4 Map every pattern to this project

For every entry, add two lines:

- **Applies to:** orchestrator, logs, trace, metrics, code, KB, graph, or none.
- **In this project:** one line naming `file:line` and what you see there, or `not found (searched: ...)`.

Use `01-system-map.md` first, and search the code only when the map doesn't answer. If a pattern can't apply, for example a coding-only detail, write `N/A` and say why.

## 2.5 Add missing patterns

Add up to 10 patterns you found in step 2.3 that the catalog lacks, as P72 onward, each with a `path:line`.

## 2.6 Write `02-pattern-catalog.md`

- Use Template T2.
- Run `python3 -I tools/agent_review/check_citations.py --only docs/agent-review/02-pattern-catalog.md` and fix every problem.
- In chat mode, update `_progress.md`.
