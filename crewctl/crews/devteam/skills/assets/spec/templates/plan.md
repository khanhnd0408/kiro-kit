# Plan: <feature name>        (implements spec.md, requirements R1..Rn)

## 1. Current state
What the code does today, with file paths and line numbers you actually read.

## 2. Approach
The chosen design in a few sentences, and the alternative you rejected with the reason.

## 3. Where the code goes
| Change | File | New or existing | Reason |
|------|------|------|------|
| Add X | path/to/file.ext | existing | same responsibility as Y |
| Add Z | path/to/new_file.ext | new | no existing module owns this; keeps file under ~400 lines |

Rule for new files: create one only when no existing module owns the responsibility, or the existing file would pass ~400 lines or mix two responsibilities.

## 4. Functional requirements -> work
| Requirement | Work that satisfies it |
|------|------|

## 5. Non-functional requirements -> work
Performance, security, reliability, observability, compatibility. Give the number and how it will be measured.

## 6. Tests
| Test | File | Covers requirement | Type (unit / integration / e2e) |
|------|------|------|------|
Name edge cases and failure cases, not just the happy path.

## 7. Risks and rollback
What could go wrong, how to detect it, how to undo the change.

## 8. Order of work
Reference todo.md. Say what can run in parallel.
