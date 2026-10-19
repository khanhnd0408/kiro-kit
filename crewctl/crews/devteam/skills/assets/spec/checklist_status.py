#!/usr/bin/env python3
"""Report whether a checklist is complete. Complete means every box is ticked, and nothing else.

    checklist_status.py FILE.md [--require-evidence]

Reads lines like `- [ ] C1 text` and `- [x] C1 text -- evidence: tests/test_x.py::test_y`.
With --require-evidence a ticked item that has no `evidence:` text counts as NOT done.
A file with no checkbox lines is not complete (an empty checklist proves nothing).
Exit codes: 0 complete, 3 something is not done, 2 bad input.
"""
import re
import sys

LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")


def status(text, require_evidence=False):
    items = []
    for number, line in enumerate(text.splitlines(), 1):
        match = LINE.match(line)
        if match:
            ticked = match.group(1).lower() == "x"
            body = match.group(2).strip()
            has_evidence = bool(re.search(r"evidence:\s*\S", body, re.I))
            items.append({"line": number, "text": body, "ticked": ticked, "has_evidence": has_evidence,
                          "done": ticked and (has_evidence or not require_evidence)})
    return items


def main(argv):
    flags = [a for a in argv if a.startswith("--")]
    files = [a for a in argv if not a.startswith("--")]
    if len(files) != 1 or any(f != "--require-evidence" for f in flags):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(files[0], encoding="utf-8") as handle:
            items = status(handle.read(), "--require-evidence" in flags)
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not items:
        print("NOT COMPLETE: no checkbox lines found, so nothing has been checked.")
        return 3
    pending = [i for i in items if not i["done"]]
    print(f"{len(items) - len(pending)}/{len(items)} done")
    for item in pending:
        reason = "not ticked" if not item["ticked"] else "ticked but no evidence"
        print(f"  line {item['line']}: {item['text']}  [{reason}]")
    print("COMPLETE" if not pending else "NOT COMPLETE")
    return 0 if not pending else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
