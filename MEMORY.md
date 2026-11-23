# Project Memory

Purpose: preserve durable context across contributors and sessions. This file is manually maintained and contains no automatic observation of runtime or deployment state. Baseline: **2026-11-17**.

## How to use this file

Read this before work that depends on prior decisions. Record stable facts and their source, consequential decisions and rationale, and concise handoff notes. Correct superseded facts in place and retain significant decision history. Keep procedural detail in COMMANDS.md and work status in TASKS.md. Never store credentials, environment contents, real student/staff records, raw logs, or database dumps here.

## Verified repository facts

| Fact | Source / consequence |
| --- | --- |
| Python 3.14 and Django 5.2.10 are declared | `.python-version`, `pyproject.toml`; old generated settings comments mention Django 4.2 but do not identify the current dependency |
| uv lockfile is the workflow installation source | CI/deploy use `uv sync --frozen`; legacy requirements diverge |
| Architecture is a Django monolith | Project settings and URL configuration include eleven local apps |
| School/user foundations live in homeApp | `homeApp/models.py`, auth/session/license modules |
| Student and teacher domain profiles live in managementApp | Portal app names do not imply model ownership |
| Finance implementation is separate from management URL exposure | `financeApp/api/` and route ownership tests |
| Current-session payload has explicit school and session IDs | `homeApp/session_utils.py`; display year is not the filtering identity |
| School-scoped mixins are available but not universal | `homeApp/model_mixins.py` and older explicit model definitions |
| Redis location comes from `REDIS_URL` | `schoolStackPro/settings.py`, local port 6379/database 1 |
| `db` selects the backend | Case-insensitive mysql/postgres values; otherwise SQLite; `USE_MYSQL` does not control selection |
| Django reads the repository-root `.env` | Settings initialize dotenv with exported variables taking precedence |
| Numbered migrations are not tracked | `.gitignore` and tracked-file inventory; generation occurs in workflows |
| Default static/media output sits outside the checkout | Sibling static_cdn/media_cdn paths in settings |
| ASGI callable has an explicit module path | `schoolStackPro.asgi:application`; package-level application is not defined |
| Timezone handling uses naive datetimes | Asia/Kolkata with USE_TZ false |
| Permissions include staff action/method mappings | `managementApp/access_control.py`, middleware, tests |
| Root permission scripts are mutating and account-specific | Do not use as routine read-only diagnostics |

## Established implementation conventions

Preserve the existing role-specific apps, shared template shell, Fomantic/jQuery/DataTables interactions, scoped ORM access, and soft-delete behavior. Put nontrivial business logic in domain services and keep transactions coherent. Reuse audit metadata and permission maps. These conventions came from the existing `agent.md` and `design_pattern.md` and were consolidated in RULES.md and DESIGN.md.

No decision has been made here to introduce a SPA, split services, add a queue, change database engines, or redesign domain naming. Such changes require an explicit task and rationale.

## Decision record

### D-001 — Split repository knowledge by responsibility (2026-11-17)

Context: the user requested README, MEMORY, ARCHITECTURE, TASKS, DESIGN, RULES, COMMANDS, and AGENTS files to track the project in depth.

Decision: use README as the navigation/setup entry point; maintain the other files as architecture, UI contract, engineering rules, operations, backlog, durable history, and agent workflow. Link related details instead of making every file a full copy.

Consequences: contributors must update relevant documents alongside changes. These Markdown files are not a daemon or external task tracker. Completion/status claims require recorded evidence.

### D-002 — Preserve legacy references and label uncertainty (2026-11-17)

Context: existing `agent.md`, `design_pattern.md`, and `internal-commands.md` contain useful conventions; the original README included incomplete setup and risky host-specific operational snippets.

Decision: preserve the legacy files, consolidate useful content, use source-verified commands in COMMANDS.md, and move unresolved technical issues into proposed tasks. Replace the old README with project orientation and safer scoped setup instructions.

Consequences: legacy material may drift; use current code for factual conflicts and update the authoritative document. This documentation change does not authorize implementing every proposed task.

### D-003 — Use PostgreSQL in Docker (2026-11-18)

Compose selects the existing PostgreSQL backend and uses Redis for the Django cache. Docker media uses a host bind mount when MEDIA_DATA_DIR is set and preserves the original media volume when unset. The controlled release script pins the application image ID and rehearses against a disposable PostgreSQL instance using a custom-format database dump. The Docker environment is separate from the local dotenv configuration. Numbered migration history must accompany schema upgrades until T-001 is resolved. See [COMMANDS.md](COMMANDS.md#docker-compose).

## Known caveats and unknowns

- Migration history and live database state require reconciliation before changing migration delivery (T-001).
- `.env.dev` is tracked; its contents were not read or copied for this task (T-002).
- An explicit `CSRF_TRUSTED_ORIGINS` replaces the default list. A blank value clears it.
- ASGI public media serving needs privacy/cache review (T-004).
- Requirements/lockfile drift and account-specific diagnostics need cleanup (T-005/T-006).
- Actual systemd configuration, production backend, proxy rules, scheduled jobs, backup restore, and deployment health have not been verified (T-007).
- Clean onboarding and many end-to-end workflows are not validated by this source review (T-008/T-009).
- Shared loading helpers have duplicate definitions; effective static storage configuration also needs investigation (T-010/T-011).

Do not convert an unknown into a fact simply because a command or feature exists in source.

## Validation baseline

The documentation task inspected configuration, dependency declarations, workflows, model inventories, routes, service locations, command definitions, permission scripts, test declarations, and shared UI conventions. It did not run application tests, install dependencies, connect to production, inspect personal records, or mutate a database. Test coverage descriptions refer to existing test code, not passing results.

Documentation verification: all eight requested files exist, relative Markdown file links resolve, expected command flags were checked against source, and whitespace/diff checks were performed. The two pre-existing untracked template `.DS_Store` files were left untouched.

## Handoff log

### 2026-11-17 — Documentation baseline

Created the requested project knowledge structure and expanded README. Preserved existing implementation/design references. Added an explicitly proposed backlog with acceptance criteria for issues found during source inspection. No application behavior changed. Next contributor should choose an authorized task, recheck relevant source and working-tree state, and record actual validation when implementation begins.

## Future decision template

```text
ID / date / title:
Context and evidence:
Options considered:
Decision and rationale:
Consequences and compatibility:
Validation:
Related task / files:
Supersedes (if applicable):
```
