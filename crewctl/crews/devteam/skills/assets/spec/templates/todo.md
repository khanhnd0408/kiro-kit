# Todo: <feature name>        (executes plan.md)

Work top to bottom. Do not start a task until the one it depends on is done.
Each task is small enough for one edit session and names its exact files.

## T1 <short title>      depends on: none      covers: R1
- File: `path/to/file.ext`  (existing | new)
- Change: Describe the edit precisely. Name the function or class, its inputs, outputs and errors.
  Example: "Add `parse_window(text: str) -> tuple[datetime, datetime]`; raise `ValueError` on an empty string."
- Do not: Things to leave alone (other functions, public signatures).
- Comments: Explain WHY, not what. At most one short comment per non-obvious decision; a docstring on each new public function (what it returns, what it raises). No commented-out code. No narration of obvious lines.
- Test first: `tests/test_x.py::test_parse_window_rejects_empty` must fail before the change and pass after.
- Done when: the named test passes and `<command>` shows no new warnings.

## T2 <short title>      depends on: T1      covers: R2
- File:
- Change:
- Comments:
- Test first:
- Done when:
