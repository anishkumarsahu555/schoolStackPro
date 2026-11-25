# Engineering Rules

These rules consolidate the repository's existing feature guide and set explicit conventions for future changes. They describe required practice, not proof that all legacy code complies. Read AGENTS.md for the contributor workflow and TASKS.md for known exceptions.

## Scope and ownership

1. Fit changes into the Django monolith and owning domain app. Preserve role portal boundaries.
2. Keep views focused on request validation/context/response; extract nontrivial operations into existing domain services or focused helpers.
3. Reuse the current template shell, response helpers, and route patterns. Avoid a parallel framework, repository abstraction, or duplicated helper for one feature.
4. Preserve unrelated working-tree changes and user data. Keep implementation changes focused on the requested work.
5. State assumptions and distinguish current implementation, intended convention, and unverified behavior.

## Authorization and data isolation

- Require authentication and appropriate group/action permissions for protected routes.
- Add explicit permission mappings in `managementApp/access_control.py` for new protected routes and HTTP methods. Verify both page and API behavior.
- Scope lookups and mutations to the authorized school and active academic session where applicable. Validate related foreign keys within that scope.
- Do not treat a supplied object ID or a hidden form value as authorization.
- Apply role/ownership rules to detail, list, export, file, and bulk operations, not only create/update screens.
- Keep backend authorization independent of navigation visibility.
- Preserve license handling and session bootstrap semantics; test permission and license failures separately.

## Models, migrations, and integrity

- Match existing schema naming (`schoolID`, `sessionID`, `studentID`, `teacherID`) where extending established models.
- Reuse appropriate audit/scoping mixins when they fit the domain; inspect older models before assuming common fields.
- Filter soft-deleted business records with `isDeleted=False`. Prefer soft deletion for domains that already use it.
- Validate uniqueness, relationships, state transitions, dates, and amounts server-side.
- Use atomic transactions for multi-record writes. Review signals and audit hooks, including bulk operations that bypass model save behavior.
- Preserve finance posting/reversal, numbering, allocation, and period rules through domain services. Use decimal arithmetic for money.
- Preserve inventory quantity, asset return, bed occupancy, and assignment consistency.
- Add indexes/constraints according to actual query and integrity needs, avoiding speculative additions.
- Model changes need a reviewed migration plan. Current `.gitignore` excludes numbered migrations; do not conceal that fact or silently change migration policy. T-001 tracks reproducible migration delivery.
- Never run fixture imports, demo reset commands, or database flushes against important data as a routine check.

## APIs, queries, and frontend

- Keep HTML views in the role/domain app and asynchronous endpoints in its existing API modules.
- Preserve `SuccessResponse`/`ErrorResponse` and existing table payload contracts unless updating all consumers deliberately.
- Return appropriate HTTP status and actionable messages; avoid leaking credentials, internal SQL, or personal records in errors.
- Use `select_related`, `prefetch_related`, and database aggregation where measured access patterns justify them.
- Include tenant/session scope in cache design and invalidate affected values after writes.
- Do not disable CSRF to make an AJAX call work. Preserve escaping and the existing rich-text sanitization path (`teacherApp/sanitizers.py`).
- Follow DESIGN.md for form states, reset behavior, responsive layouts, themes, and permission-aware actions.

## Secrets and sensitive records

Do not copy environment values, credentials, tokens, keys, database dumps, or real student/staff records into documentation, fixtures, screenshots, logs, or commits. Examples must use placeholders or synthetic data. `.env.dev` is currently tracked and needs a separate review; this documentation baseline did not inspect or reproduce its values. Redact incident details while retaining actionable evidence.

Uploaded media and generated reports may contain personal data. Do not assume public file delivery is acceptable; review the ASGI media path before changing storage or access rules. Keep production backups outside the repository and test restoration in an isolated environment.

## Validation expectations

| Change | Relevant evidence |
| --- | --- |
| Documentation only | Correct source references, working local links, consistent commands/status, whitespace/diff review |
| Model/service behavior | Focused Django tests for success/failure/integrity plus migration review |
| Permissions/routes | Route mapping, denied-action, method-specific and UI visibility tests |
| Finance/operations | Posting/reversal or stock/assignment invariants and transaction failures |
| UI | Changed route/role, loading/error/reset states, mobile and light/dark inspection |
| Deployment/settings | System checks, relevant configuration tests and environment-specific staging verification |

Run checks appropriate to the change and the repository's CI requirements. Do not report tests as passed unless executed successfully. Record command, environment, outcome, and blockers; test presence is not test success. Avoid repeatedly running broad checks after adequate evidence unless a new change or failure warrants it.

## Documentation and completion

For each material change, update the document that owns the affected information. Keep detailed procedures in COMMANDS.md, architecture in ARCHITECTURE.md, UI contracts in DESIGN.md, work state in TASKS.md, and durable rationale in MEMORY.md. Link instead of copying large sections.

A task is done when its acceptance criteria are satisfied, appropriate verification is recorded, and remaining issues are explicit. Do not turn an inferred improvement into an approved delivery commitment. Keep dates, owners, and status honest; use `unassigned` and `not run` when applicable.
