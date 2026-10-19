# Spec: <feature name>

Status: draft | agreed          Owner: <who decides>          Last updated: <date>

## 1. Request, as given
Quote the user's words. Do not reword here.

## 2. Goal
One paragraph: the outcome and who benefits. State what will be true when this is done.

## 3. Scope
- In scope:
- Out of scope (name things a reader might assume are included):

## 4. Requirements
Number every requirement so the plan and checklist can point to it. Each must be testable.

| ID | Type (functional / non-functional) | Requirement | How it is verified |
|----|------|------|------|
| R1 | functional | The system shall ... | test name or manual check |
| R2 | non-functional | p99 latency under 200 ms at 100 requests/s | load test |

## 5. Inputs, outputs and data
Formats, ranges, error cases, what happens on bad input.

## 6. Constraints
Languages, frameworks, existing interfaces that must not change, deadlines, security rules.

## 7. Assumptions
Each one is a risk. Mark `confirmed by user` or `unconfirmed`.

## 8. Open questions
Questions that block the plan. Ask the user. Do not guess.

## 9. Acceptance
The conditions under which the user would call this finished.
