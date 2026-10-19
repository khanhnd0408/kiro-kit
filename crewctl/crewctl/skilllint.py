"""Check SKILL.md files against the rules Kiro and Crew apply. Reads files, changes nothing."""

import os
import re
from dataclasses import dataclass

from .config import SECRET_PATTERNS

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
CREW_BODY_CAP = 24_750          # Crew refuses a project skill larger than this
SOFT_BODY_LIMIT = 8_000         # every load of the body costs context
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)\Z")


@dataclass
class Finding:
    level: str      # error | warn
    path: str
    message: str


def _frontmatter(text):
    """Return (fields, ok). Same shape Crew reads: fence at column 0, keys at column 0."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return {}, False
    fields = {}
    for line in lines[1:]:
        if line.rstrip() == "---":
            return fields, True
        match = KEY_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2).strip().strip("'\"")
    return fields, False


def lint_file(skill_file):
    out = []
    folder = os.path.basename(os.path.dirname(os.path.abspath(skill_file)))

    def add(level, message):
        out.append(Finding(level, skill_file, message))

    try:
        raw = open(skill_file, "rb").read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        add("error", f"cannot read as UTF-8: {error}")
        return out
    fields, closed = _frontmatter(text)
    if not fields and not closed:
        add("error", "no frontmatter: the file must start with a line '---' at column 0")
        return out
    if not closed:
        add("error", "frontmatter is not closed with a second '---' line")

    name = fields.get("name", "")
    if not name:
        add("error", "missing 'name'")
    else:
        if name != folder:
            add("error", f"name '{name}' must equal the folder name '{folder}'")
        if not NAME_RE.match(name) or len(name) > MAX_NAME:
            add("error", f"name '{name}' must be lowercase letters, digits and single hyphens, up to {MAX_NAME} characters")

    description = fields.get("description", "")
    if not description:
        add("error", "missing 'description' (it decides when the skill is used)")
    else:
        if len(description) > MAX_DESCRIPTION:
            add("error", f"description is {len(description)} characters; the limit is {MAX_DESCRIPTION}")
        if not re.search(r"\buse (?:when|this|for|after|before)\b", description, re.I):
            add("warn", "description does not say when to use the skill; start from 'Use when ...' so it triggers reliably")
        if ": " in description:
            add("warn", "description contains ': ' - Crew reads it, a strict YAML parser rejects it; wrap the value in quotes")

    triggers = [t for t in re.split(r"[,;]", fields.get("triggers", "")) if t.strip()]
    if not triggers:
        add("warn", "no 'triggers'; Crew uses them only when skills.max_triggered is above 0 (Kiro ignores the field), so the description does the work")
    elif len(triggers) > 12:
        add("warn", f"{len(triggers)} triggers; 3-8 specific phrases match better than a long list")

    if fields.get("always", "").lower() == "true":
        add("warn", "always: true puts this skill into every session turn; keep it very short or drop it")

    size = len(raw)
    if size > CREW_BODY_CAP:
        add("error", f"{size} bytes; Crew refuses project skills over {CREW_BODY_CAP}")
    elif size > SOFT_BODY_LIMIT:
        add("warn", f"{size} bytes; move reference material into references/ and link it")

    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            add("error", "looks like a credential; skills are loaded into prompts and shared, remove it")
            break

    skill_root = os.path.dirname(os.path.abspath(skill_file))
    for ref in sorted(set(re.findall(r"\bscripts/[A-Za-z0-9_./-]+", text))):
        ref = ref.rstrip(".")
        if not os.path.isfile(os.path.join(skill_root, ref)):
            add("warn", f"mentions {ref}, which is not in the skill folder")
    for root, dirs, names in os.walk(skill_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, skill_root)
            if rel == "SKILL.md":
                continue
            try:
                body = open(path, "rb").read()
                body.decode("utf-8")
            except UnicodeDecodeError:
                add("warn", f"{rel} is a binary file; nobody can review what it does. Prefer a script with readable source")
                continue
            except OSError:
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(body.decode("utf-8")):
                    add("error", f"{rel} looks like it holds a credential")
                    break
    return out


def find_skill_files(paths):
    found = []
    for path in paths:
        if os.path.isfile(path):
            found.append(path)
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if "SKILL.md" in files:
                found.append(os.path.join(root, "SKILL.md"))
    return sorted(found)


def lint_paths(paths):
    files = find_skill_files(paths)
    findings = []
    for file in files:
        findings.extend(lint_file(file))
    return files, findings
