#!/usr/bin/env python3
"""List the files changed since a base ref, grouped by kind of infrastructure code. Read-only.

    python3 changed_iac.py [BASE]      BASE defaults to origin/main

Prints one group per line block. Exit 0 with files, 3 if nothing relevant changed, 2 on a git problem.
"""
import re
import subprocess
import sys

GROUPS = [
    ("terraform", re.compile(r"\.(tf|tfvars|hcl)$")),
    ("helm", re.compile(r"(^|/)(Chart\.yaml|values[^/]*\.yaml)$|(^|/)charts?/.+/templates/")),
    ("dockerfile", re.compile(r"(^|/)(Dockerfile[^/]*|[^/]*\.dockerfile|docker-compose[^/]*\.ya?ml)$")),
    ("ci", re.compile(r"^\.github/workflows/|(^|/)\.gitlab-ci\.ya?ml$|(^|/)Jenkinsfile$|^\.circleci/")),
    ("kubernetes", re.compile(r"(^|/)(k8s|kubernetes|manifests|deploy)/.+\.ya?ml$")),
]


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip() or "git failed", file=sys.stderr)
        sys.exit(2)
    return result.stdout


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    files = [f for f in git("diff", "--name-only", f"{base}...HEAD").splitlines() if f]
    found = {}
    for path in files:
        for name, pattern in GROUPS:
            if pattern.search(path):
                found.setdefault(name, []).append(path)
                break
    if not found:
        print(f"No infrastructure files changed since {base} ({len(files)} files changed in total).")
        return 3
    for name, paths in found.items():
        print(f"{name} ({len(paths)}):")
        for path in paths:
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
