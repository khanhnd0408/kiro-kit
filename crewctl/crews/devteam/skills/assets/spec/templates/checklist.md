# Checklist: <feature name>

Rule: the work is complete only when EVERY box is ticked. A ticked box needs evidence: the test name, the command and its result, or the file and line. An unticked box means the work is not finished. Never delete an item to make the list pass; if an item no longer applies, mark it `[x]` and write `evidence: not applicable because ...`.

## Requirements
- [ ] R1 <requirement text> -- evidence:
- [ ] R2 <requirement text> -- evidence:

## Tasks
- [ ] T1 <title> -- evidence:
- [ ] T2 <title> -- evidence:

## Quality gates
- [ ] G1 New and changed tests pass -- evidence:
- [ ] G2 Existing tests still pass -- evidence:
- [ ] G3 Linter and type checker report nothing new -- evidence:
- [ ] G4 No unrelated files changed (diff reviewed) -- evidence:
- [ ] G5 Docs or comments updated where behaviour changed -- evidence:
- [ ] G6 No secrets, debug output or commented-out code added -- evidence:
