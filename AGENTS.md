# Contributor and Coding-Agent Instructions

## Scope

This file applies to the repository root and descendants unless a more specific instruction file applies. Follow the user's current task and applicable higher-priority instructions. This repository is a Django school-management monolith; preserve its established app, tenancy, role, and template patterns.

## Read before changing code

1. Read README.md for setup and the documentation map.
2. Read RULES.md for engineering and data-integrity conventions.
3. Read the relevant ARCHITECTURE.md and DESIGN.md sections.
4. Read TASKS.md for current work and MEMORY.md for durable caveats.
5. Inspect the nearest existing implementation, its callers, routes, permissions, services, and tests.

Legacy `agent.md` and `design_pattern.md` contain detailed prior conventions. New documents consolidate them; resolve factual discrepancies against code and record the finding. Do not silently claim an intended convention is already implemented.

## Starting a task

- Inspect `git status --short`; preserve unrelated modifications and untracked user files.
- Identify the owning app and the affected request/data flow.
- Use `rg`/`rg --files` for targeted discovery; avoid reading environment secrets, credentials, database contents, or dumps merely for orientation.
- Work in the current checkout unless isolation is needed or requested.
- For substantive tracked work, assign or reuse a TASKS.md ID with scope, acceptance criteria, and status. Do not invent owners, deadlines, approvals, or passed checks.
- Proceed with routine authorized investigation and reversible implementation; ask only when essential information or a consequential decision is missing.

## Implementation checklist

- Preserve school/session/soft-delete filtering and object-level access.
- Map protected route actions and HTTP methods through `managementApp/access_control.py` as appropriate.
- Keep finance logic in `financeApp` even when exposed through management URLs.
- Reuse domain services, transaction boundaries, response contracts, and audit behavior.
- Reuse existing role shells and global UI helpers. Follow DESIGN.md for forms and tables.
- Review model changes and the repository's ignored migration policy before delivery.
- Keep secrets and personal data out of output and test artifacts.
- Do not perform deployment, destructive data reset, or permission-fixture mutation as an incidental validation step.

## Validation

Use COMMANDS.md. For documentation-only changes, validate links, source references, command flags, and the diff; do not run database mutations. For behavioral changes, run focused meaningful tests and required checks. Do not run the root `verify_*permissions*.py` scripts as harmless diagnostics: they modify real configured database permissions and assume a particular existing account.

If execution is blocked, state exactly what ran, what did not, why, and what remains to verify. Never manufacture test results or treat a CI workflow definition as evidence that CI passed.

## Keeping project knowledge current

- README.md: entry point and setup.
- ARCHITECTURE.md: system structure and domain ownership.
- DESIGN.md: UI contract.
- RULES.md: engineering standards.
- COMMANDS.md: verified commands and effects.
- TASKS.md: open/in-progress/blocked/done work and acceptance evidence.
- MEMORY.md: durable facts, decisions, caveats, and concise dated handoffs.

Update affected files within the same change. Keep secrets and transient logs out. Do not append repetitive session narration. When a fact changes, update its authoritative section and record the significant decision rather than leaving contradictory snapshots.

## Handoff

Report the outcome, changed files, validation performed, and material limitations. Keep the user's next step concrete when work remains. Do not commit, push, or deploy unless requested or otherwise authorized by the task context. Documentation maintenance does not provide standing authorization to perform the proposed backlog.
