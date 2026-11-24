# Task Register

Baseline: 2026-11-17. This is a manually maintained work register. Items below come from source inspection; proposed remediation is not a claim of user approval or a delivery schedule. No implementation task is currently marked in progress.

## Status and priority

Statuses: `proposed` → `ready` → `in progress` → `done`; use `blocked` only with a concrete dependency. `ready` means scope and acceptance are agreed. Priorities: P1 = security/integrity/reproducibility concern; P2 = reliability or maintenance; P3 = polish. Owners remain unassigned until someone takes the work. Completion requires evidence, not only code changes.

## Open work

| ID | Priority | Status | Owner | Work / source evidence |
| --- | --- | --- | --- | --- |
| T-001 | P1 | proposed | unassigned | Establish migration delivery policy: `.gitignore` ignores numbered migrations; CI/deploy generate them |
| T-002 | P1 | proposed | unassigned | Review tracked `.env.dev` and ignore policy; values were not inspected in documentation work |
| T-003 | P1 | done | unassigned | Honor configured CSRF origins while preserving defaults |
| T-004 | P1 | proposed | unassigned | Review school and record-level authorization for session-authenticated uploads |
| T-005 | P2 | proposed | unassigned | Reconcile legacy requirements with uv project/lock dependencies |
| T-006 | P2 | proposed | unassigned | Replace account-specific mutating permission scripts with isolated fixtures/tests |
| T-007 | P1 | proposed | unassigned | Validate deployment backups, restore/rollback, and post-deploy health verification |
| T-008 | P2 | proposed | unassigned | Verify a clean-install onboarding path and minimum school configuration |
| T-009 | P2 | proposed | unassigned | Expand coverage of role portals and operational workflows |
| T-010 | P2 | proposed | unassigned | Consolidate duplicate global loading-helper definitions in the base template |
| T-011 | P2 | proposed | unassigned | Normalize database/cache environment inputs and clarify static storage configuration |

## Acceptance criteria and investigation notes

### T-001 — Reproducible migrations

Evidence: only migration `__init__.py` files are tracked; numbered Python migrations are ignored. Both workflows call `makemigrations --noinput`.

Agree the repository policy, establish a reviewed migration baseline compatible with deployed histories, and validate both a clean database and an upgrade of representative existing data. CI should detect unintended model drift without synthesizing release history. Do not delete deployed migration history or force-add arbitrary generated migrations as an incidental fix. Depends on knowledge of existing deployment migration state.

### T-002 — Environment-file hygiene

Evidence: `git ls-files` includes `.env.dev`; `.gitignore` excludes `.env` and environment variants except the shareable template. No conclusion about exposed credentials can be drawn without authorized inspection.

Review whether tracked values are safe examples, replace sensitive configuration with placeholders if necessary, refine ignore rules without losing useful example files, and rotate credentials only if exposure is confirmed. Verify no secrets appear in the resulting diff/history remediation output. Record sanitized findings only.

### T-003 — Trusted origins

Settings now load the trusted-origin list once. Configuration regression tests cover defaults, custom lists and explicit empty values.

Define intended defaults and environment override behavior, test local/staging/production origin handling, and retain CSRF protection. Demonstrate that configured origins are honored and unintended origins remain rejected.

### T-004 — Media access policy

Django now requires a session for local uploads and marks responses private and no-store. A signed-in user with another record’s upload URL can still access it.

Classify uploaded/generated content, define public versus authenticated access, implement appropriate authorization/cache headers, and test unauthenticated/cross-school access plus path traversal. Verify both deployed serving mode and proxy behavior. This is a risk to investigate, not a completed exploit assessment.

### T-005 — Dependency authority

Evidence: `requirements.txt` omits dependencies declared in `pyproject.toml` and has different versions for shared entries.

Choose a maintained compatibility/export policy or retire the duplicate path explicitly. Validate a frozen install on supported Python and record native dependency prerequisites. Avoid incidental upgrades unrelated to resolving drift.

### T-006 — Permission diagnostics

Evidence: root permission scripts fetch an account-specific username and update authorization records.

Move reusable checks to fixtures in an isolated test database, remove dependence on personal accounts, preserve method/action coverage, and prove the diagnostics leave configured application data unchanged. Keep Playwright-specific requirements separate from unit tests.

### T-007 — Deployment recovery

Evidence: deploy resets tracked remote files, generates/applies migrations, collects with `--clear`, and restarts; no explicit health/rollback stage is shown.

Document backup ownership/location/retention without secrets, demonstrate restoration in a disposable environment, define code/schema rollback limits, and verify a post-deploy health criterion. Confirm service entry point and private-media behavior. Requires deployment-specific context; no deployment was performed by the documentation task.

### T-008 — First-school onboarding

Run the documented setup from a clean checkout against disposable data; record native prerequisites, migrations, Redis behavior, groups/profile setup, session/license prerequisites, and first successful role logins. Verify one academic and one finance workflow using synthetic data. Update README.md with a proven minimum onboarding sequence.

### T-009 — Coverage gaps

Evidence: student and transport test modules are placeholders; no standard `tests.py` was found for chat, certificates, hostel, or library in the inspected file inventory. Some library and chat behavior is covered indirectly by finance/management tests.

Prioritize risk-based tests: cross-school/session denial, role-specific reads/writes, posting/stock/occupancy invariants, certificate visibility, and chat participant access. Record new coverage by behavior; avoid asserting that file presence proves coverage. Existing test results remain unverified for this documentation-only task.

### T-010 — Shared loading helpers

Evidence: multiple top-level `showLoading`, `showUpdateLoading`, and `hideLoading` definitions appear in the base template.

Identify active call signatures and form scoping, consolidate without breaking callers, and validate add/update/validation/network-failure behavior for single and multiple forms across role shells. Do not replace the UI stack as part of this cleanup.

### T-011 — Configuration consistency

Database and cache inputs are configurable. The remaining storage review concerns `STATICFILES_STORAGE` and an explicit `STORAGES` mapping.

Choose and test environment contracts with backward compatibility, verify effective static storage under the installed Django version, and update README/COMMANDS. Do not assume the legacy static-storage setting is effective merely because it exists. Validate behavior with installed runtime or official documentation before implementation.

## Completed work

| ID | Date | Outcome | Verification |
| --- | --- | --- | --- |
| DOCKER-008 | 2026-11-24 | Reuse Debian package downloads and locked Python dependencies across image rebuilds | On local ARM64 Docker, both APT stages shared archives, the updated image built in 228 s, an unchanged repeat took 4 s, and headless Chromium produced a PDF. VPS logs later showed a PyPI timeout during http-ece build dependency resolution. Verified default and overridden uv network settings with Compose and imported http-ece 1.2.1 and pywebpush 2.3.0 in the rebuilt local image. VPS retry remains unverified. |
| DOCKER-007 | 2026-11-19 | Activate the eight requested Docker and web settings and honor the selected image and listener | Five script tests passed. Verified the configured release image, resolved Compose settings and Gunicorn arguments in dry runs. |
| DOCKER-006 | 2026-11-19 | Reduce the web image size while retaining certificate and report exports | Rebuilt the verified 706,844,208-byte ARM64 image, checked certificate and report PDFs, migration status and live readiness, then removed the superseded 1,052,585,649-byte image. |
| DOCKER-005 | 2026-11-18 | Match the Hansika host mount layout for SchoolStack | Launcher and direct Compose resolved all three host paths. PostgreSQL 16 to 17 restore verified across 125 tables and sequence values. Original volume retained. Three Compose configuration tests passed. |
| DOCKER-005 | 2026-11-24 | Generate a project-named image with a unique tag on each launcher deployment | Three dry runs verified tag format, uniqueness and repository overrides. Compose configuration and mocked controlled-release tests passed. |
| DOCKER-004 | 2026-11-18 | Align local and Docker environment variable names with the reference deployment | Four Django settings tests, three Compose tests and a Docker launcher dry run passed. |
| DOCKER-005 | 2026-11-24 | Generate a project-named image with a unique tag on each launcher deployment | Three dry runs verified tag format, uniqueness and repository overrides. Compose configuration and mocked controlled-release tests passed. |
| DOCKER-004 | 2026-11-24 | Use one root .env for Compose, launcher and controlled releases while retaining existing deployment storage and credentials | Compose config, default launcher dry-run, five configuration tests and six mocked release scenarios passed. Web recreated from the existing image, reported healthy and verified a VAPID signature on the restored stack. |
| DOCKER-003 | 2026-11-18 | Port the Hansika controlled release, Docker launcher, readiness probes, portable generator and environment-specific storage to SchoolStack | An isolated PostgreSQL 16 deployment backed up and restored a synthetic school database, completed fresh and restored migration checks, verified candidate image identity and served the persisted logo. SQLite staging and custom port passed. Ten Django tests, Compose configuration cases, six release failure/success scenarios, generator config and overwrite refusal passed. |
| DOCKER-005 | 2026-11-24 | Generate a project-named image with a unique tag on each launcher deployment | Three dry runs verified tag format, uniqueness and repository overrides. Compose configuration and mocked controlled-release tests passed. |
| DOCKER-004 | 2026-11-24 | Use one root .env for Compose, launcher and controlled releases while retaining existing deployment storage and credentials | Compose config, default launcher dry-run, five configuration tests and six mocked release scenarios passed. Web recreated from the existing image, reported healthy and verified a VAPID signature on the restored stack. |
| DOCKER-003 | 2026-11-24 | Mount the existing VAPID signing key read-only in web and preserve the existing public key | Five Compose tests passed. Running web reached readiness and signed and verified a local VAPID token with the matching public key. |
| DOCKER-002 | 2026-11-18 | Match the reference local and Docker launcher and support host-backed uploads | Bash syntax, three settings tests and a local launcher check passed. Docker down/up retained a synthetic admin multipart logo, its generated thumbnails, and its PostgreSQL record. Verified the HTTP response bytes and host files. |
| DOCKER-005 | 2026-11-24 | Generate a project-named image with a unique tag on each launcher deployment | Three dry runs verified tag format, uniqueness and repository overrides. Compose configuration and mocked controlled-release tests passed. |
| DOCKER-004 | 2026-11-24 | Use one root .env for Compose, launcher and controlled releases while retaining existing deployment storage and credentials | Compose config, default launcher dry-run, five configuration tests and six mocked release scenarios passed. Web recreated from the existing image, reported healthy and verified a VAPID signature on the restored stack. |
| DOCKER-003 | 2026-11-24 | Mount the existing VAPID signing key read-only in web and preserve the existing public key | Five Compose tests passed. Running web reached readiness and signed and verified a local VAPID token with the matching public key. |
| DOCKER-002 | 2026-11-23 | Serve persistent uploads at an environment-selected prefix with session authentication under ASGI and WSGI | Docker Desktop PostgreSQL stack reached readiness. Verified anonymous HTTP denial and authenticated retrieval at /uploads/ before and after web recreation with explicit mounts. Four Compose configuration tests and focused Django tests passed. Object-level authorization remains T-004. |
| DOCKER-001 | 2026-11-18 | Add a PostgreSQL Compose runtime, Redis, persistent media and a frozen-dependency ASGI image | On Docker Desktop ARM64, fresh migration and rebuild passed. Verified homepage, static assets, media persistence, Redis read/write, non-root Chromium PDF, Django check and all 21 settings/homeApp tests. |
| ENV-001 | 2026-11-18 | Load root dotenv across Django entry points, preserve exported overrides and document service settings | Django check and ASGI/WSGI initialization passed. All 21 configuration and homeApp tests passed with disposable SQLite tables built from models. The default test run lacked generated migrations. |
| DOC-001 | 2026-11-17 | Replaced minimal README and created seven linked project-maintenance documents; preserved legacy reference files | Source inspection; local Markdown link, required-file, and whitespace checks. No Django tests or deployment run for this documentation-only change |

## Task entry template

```text
ID / title:
Priority / status / owner:
Created / updated:
Problem and source evidence:
Scope and exclusions:
Dependencies / blocker:
Acceptance criteria:
Implementation references:
Validation command or manual procedure:
Actual result and environment:
Remaining limitations:
Documentation updated:
```

When finishing, move the entry to completed work with evidence and date. When deferring, keep the reason and next concrete action. Track bugs separately from hypothetical enhancements and avoid turning the task register into an unverified feature wish list.
