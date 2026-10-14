#!/usr/bin/env python3
"""Check `path:line` citations in the agent-review deliverables.

The agent-review method asks every claim about the code to cite `file:line`
in backticks. This script checks that each cited file exists and each cited
line is in range. It also checks that findings and recommendations have their
required fields. It is read-only, uses only the standard library, and never
touches the network.

Usage (run from the project root):
  python3 -I tools/agent_review/check_citations.py              # all deliverables
  python3 -I tools/agent_review/check_citations.py --only FILE  # one file
  python3 -I tools/agent_review/check_citations.py --changed FILE   # hook mode
  python3 -I tools/agent_review/check_citations.py --catalog    # method vs REFERENCE_ROOT
  python3 -I tools/agent_review/check_citations.py --self-test

Citations whose path starts with `Anthropic/` or `OpenAI/` are resolved under
REFERENCE_ROOT (read from docs/agent-review-method/inputs.md, or --reference).
All other relative paths are resolved from the project root.

Exit codes: 0 no problems, 1 problems found (listed on stderr), 2 usage error.
"""

import argparse
import os
import re
import sys
import tempfile

DELIVERABLE_DIR = os.path.join("docs", "agent-review")
METHOD_DIR = os.path.join("docs", "agent-review-method")
DEFAULT_REFERENCE = os.path.join("~", ".kiro-agent-review", "reference")
REFERENCE_PREFIXES = ("Anthropic/", "OpenAI/")
SKIP_DIRS = {"tmp"}
MAX_BYTES = 50 * 1024 * 1024

BACKTICK = re.compile(r"`([^`\n]+)`")
CITATION = re.compile(
    r"^(?P<path>[^\s`:<>*?|]+):(?P<spec>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)$"
)
EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")

FINDING_FIELDS = ("- Basis:", "- Evidence:", "- Failure scenario:")
RECOMMENDATION_FIELDS = ("- Problem:", "- Change:", "- Measure:")


def looks_like_path(path):
    if "://" in path:
        return False
    return "/" in path or EXTENSION.search(path) is not None


def parse_spec(spec):
    numbers = []
    for part in spec.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            numbers.extend([int(start), int(end)])
        else:
            numbers.append(int(part))
    return numbers


class LineCounter:
    def __init__(self):
        self._cache = {}

    def count(self, path):
        if path not in self._cache:
            if os.path.getsize(path) > MAX_BYTES:
                self._cache[path] = None
            else:
                with open(path, "rb") as handle:
                    data = handle.read()
                lines = data.count(b"\n")
                if data and not data.endswith(b"\n"):
                    lines += 1
                self._cache[path] = lines
        return self._cache[path]


def find_reference_root(root, override):
    if override:
        return os.path.abspath(os.path.expanduser(override))
    inputs = os.path.join(root, METHOD_DIR, "inputs.md")
    if os.path.isfile(inputs):
        with open(inputs, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.match(r"\s*REFERENCE_ROOT:\s*(\S+)", line)
                if match:
                    return os.path.abspath(os.path.expanduser(match.group(1)))
    return os.path.abspath(os.path.expanduser(DEFAULT_REFERENCE))


def resolve(path, root, reference_root):
    """Return (absolute path, kind) for a cited path."""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return expanded, "absolute"
    if path.startswith(REFERENCE_PREFIXES):
        candidate = os.path.join(reference_root, path)
        if os.path.exists(candidate) or not os.path.exists(os.path.join(root, path)):
            return candidate, "reference"
    return os.path.join(root, path), "project"


def blocks(lines, prefix):
    """Yield (start_line_number, title, block_lines) for headings starting with prefix."""
    start = None
    for index, line in enumerate(lines):
        is_heading = line.startswith("#")
        if is_heading and start is not None:
            yield start + 1, lines[start].strip(), lines[start + 1:index]
            start = None
        if line.startswith(prefix):
            start = index
    if start is not None:
        yield start + 1, lines[start].strip(), lines[start + 1:]


def has_field(block_lines, field):
    return any(line.strip().startswith(field) for line in block_lines)


def lint(display, lines):
    problems = []
    for start, title, body in blocks(lines, "### F-"):
        missing = [field[2:-1] for field in FINDING_FIELDS if not has_field(body, field)]
        if missing:
            problems.append(f"{display}:{start}: finding '{title[4:]}' is missing: {', '.join(missing)}")
        for line in body:
            if line.strip().startswith("- Evidence:"):
                cited = [m for m in BACKTICK.finditer(line) if CITATION.match(m.group(1).strip())]
                if not cited:
                    problems.append(f"{display}:{start}: finding '{title[4:]}' has Evidence without a `file:line` citation")
    for start, title, body in blocks(lines, "### R-"):
        missing = [field[2:-1] for field in RECOMMENDATION_FIELDS if not has_field(body, field)]
        if missing:
            problems.append(f"{display}:{start}: recommendation '{title[4:]}' is missing: {', '.join(missing)}")
    return problems


def check_file(md_path, root, reference_root, counter, reference_only=False, run_lint=True):
    display = os.path.relpath(md_path, root)
    with open(md_path, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().split("\n")
    problems, checked = [], 0
    for number, line in enumerate(lines, 1):
        for match in BACKTICK.finditer(line):
            citation = CITATION.match(match.group(1).strip())
            if not citation:
                continue
            path = citation.group("path")
            if not looks_like_path(path):
                continue
            if reference_only and not path.startswith(REFERENCE_PREFIXES):
                continue
            target, kind = resolve(path, root, reference_root)
            checked += 1
            label = f"`{citation.group(0)}`"
            if not os.path.exists(target):
                where = "REFERENCE_ROOT" if kind == "reference" else "the project"
                problems.append(f"{display}:{number}: {label} - file not found in {where}")
                continue
            if os.path.isdir(target):
                problems.append(f"{display}:{number}: {label} - is a directory, not a file")
                continue
            total = counter.count(target)
            if total is None:
                continue
            bad = [n for n in parse_spec(citation.group("spec")) if n < 1 or n > total]
            if bad:
                problems.append(f"{display}:{number}: {label} - line {bad[0]} is out of range (file has {total} lines)")
    if run_lint:
        problems.extend(lint(display, lines))
    return problems, checked


def deliverables(root):
    base = os.path.join(root, DELIVERABLE_DIR)
    found = []
    for directory, subdirs, files in os.walk(base):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        found.extend(os.path.join(directory, name) for name in files if name.endswith(".md"))
    return sorted(found)


def method_files(root):
    base = os.path.join(root, METHOD_DIR)
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, name) for name in os.listdir(base) if name.endswith(".md"))


def under(path, directory):
    path, directory = os.path.abspath(path), os.path.abspath(directory)
    return path == directory or path.startswith(directory + os.sep)


def run(root, reference_root, files, reference_only=False, run_lint=True):
    counter = LineCounter()
    problems, checked = [], 0
    for path in files:
        file_problems, file_checked = check_file(path, root, reference_root, counter, reference_only, run_lint)
        problems.extend(file_problems)
        checked += file_checked
    return problems, checked


def report(problems, checked, files):
    if problems:
        print(f"agent-review citation check: {len(problems)} problem(s). Fix each one, then run the check again.", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"agent-review citation check: ok ({checked} citations in {len(files)} file(s))")
    return 0


def self_test():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as reference:
        os.makedirs(os.path.join(root, "src"))
        with open(os.path.join(root, "src", "app.py"), "w", encoding="utf-8") as handle:
            handle.write("a = 1\nb = 2\nc = 3\n")
        os.makedirs(os.path.join(reference, "OpenAI"))
        with open(os.path.join(reference, "OpenAI", "x.md"), "w", encoding="utf-8") as handle:
            handle.write("one\ntwo\n")
        scorecards = os.path.join(root, DELIVERABLE_DIR, "scorecards")
        os.makedirs(scorecards)
        sample = "\n".join([
            "# Scorecard: logs",
            "Good project citation `src/app.py:2` and range `src/app.py:1-3`.",
            "Out of range `src/app.py:99`. Missing file `src/missing.py:1`.",
            "Good reference `OpenAI/x.md:1-2`. Bad reference `OpenAI/x.md:9`.",
            "Not citations: `14:05`, `http://host:3100`, `<file:line>`.",
            "### F-LOGS-01 Complete finding",
            "- Basis: observed",
            "- Evidence: `src/app.py:3`: sets c.",
            "- Failure scenario: x → y → z",
            "### F-LOGS-02 Incomplete finding",
            "- Evidence: the tool returns an empty list.",
            "- Failure scenario: x → y → z",
            "### R-01 Recommendation without a measure",
            "- Problem: p",
            "- Change: c",
            "",
        ])
        with open(os.path.join(scorecards, "logs.md"), "w", encoding="utf-8") as handle:
            handle.write(sample)
        problems, checked = run(root, reference, deliverables(root))
        expected = [
            "line 99 is out of range",
            "src/missing.py:1` - file not found",
            "line 9 is out of range",
            "F-LOGS-02 Incomplete finding' is missing: Basis",
            "F-LOGS-02 Incomplete finding' has Evidence without",
            "R-01 Recommendation without a measure' is missing: Measure",
        ]
        missing = [e for e in expected if not any(e in p for p in problems)]
        if missing or len(problems) != len(expected) or checked != 7:
            print("self-test FAILED", file=sys.stderr)
            print(f"  checked={checked} (expected 7)", file=sys.stderr)
            for problem in problems:
                print(f"  got: {problem}", file=sys.stderr)
            for item in missing:
                print(f"  missing expected problem: {item}", file=sys.stderr)
            return 1
    print("self-test ok")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description="Check file:line citations in agent-review deliverables.")
    parser.add_argument("--root", help="project root (default: current directory)")
    parser.add_argument("--reference", help="REFERENCE_ROOT (default: from inputs.md)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--only", metavar="FILE", help="check one file")
    group.add_argument("--changed", metavar="FILE", help="hook mode: check FILE if it is a deliverable")
    group.add_argument("--catalog", action="store_true", help="check the method's reference citations")
    group.add_argument("--self-test", action="store_true", help="run a built-in test")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    root = os.path.abspath(args.root or os.getcwd())
    reference_root = find_reference_root(root, args.reference)

    if args.catalog:
        if not os.path.isdir(reference_root):
            print(f"REFERENCE_ROOT not found: {reference_root}. Run install.sh or set it in {METHOD_DIR}/inputs.md.", file=sys.stderr)
            return 2
        files = method_files(root)
        problems, checked = run(root, reference_root, files, reference_only=True, run_lint=False)
        return report(problems, checked, files)

    if args.changed is not None:
        changed = args.changed.strip().strip('"')
        if "{{" in changed or not changed:
            files = deliverables(root)
        else:
            path = changed if os.path.isabs(changed) else os.path.join(root, changed)
            if not os.path.isfile(path) or not path.endswith(".md") or not under(path, os.path.join(root, DELIVERABLE_DIR)):
                return 0
            if any(part in SKIP_DIRS for part in os.path.relpath(path, os.path.join(root, DELIVERABLE_DIR)).split(os.sep)):
                return 0
            files = [path]
    elif args.only:
        path = args.only if os.path.isabs(args.only) else os.path.join(root, args.only)
        if not os.path.isfile(path):
            print(f"file not found: {args.only}", file=sys.stderr)
            return 2
        files = [path]
    else:
        files = deliverables(root)

    problems, checked = run(root, reference_root, files)
    return report(problems, checked, files)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
