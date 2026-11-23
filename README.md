# SchoolStack Pro

SchoolStack Pro is a school-management application built as a Django monolith. It combines role-based academic portals with fees and accounting, staff permissions, certificates, communication, library, transport, hostel, and inventory workflows. The product branding in the application is **SchoolsStack**; the Python project is `schoolStackPro`.

Documentation baseline: **2026-11-17**. This documentation describes the inspected repository, not a certification of production behavior. Pending investigations live in [TASKS.md](TASKS.md).

## Documentation map

| Document | Purpose | Update when |
| --- | --- | --- |
| [README.md](README.md) | Orientation, installation, configuration, onboarding | Setup or product scope changes |
| [ARCHITECTURE.md](ARCHITECTURE.md) | App ownership, request flow, data boundaries, infrastructure | Models, routing, services, integrations change |
| [DESIGN.md](DESIGN.md) | Template, component, interaction, and accessibility conventions | UI patterns or shared helpers change |
| [RULES.md](RULES.md) | Engineering and data-integrity requirements | Team conventions change |
| [COMMANDS.md](COMMANDS.md) | Development, testing, maintenance, deployment runbook | A command, flag, or operational procedure changes |
| [TASKS.md](TASKS.md) | Prioritized work, acceptance criteria, verification status | Work starts, changes scope, or completes |
| [MEMORY.md](MEMORY.md) | Durable facts, decisions, caveats, handoff history | A consequential fact or decision is established |
| [AGENTS.md](AGENTS.md) | Instructions for contributors using coding agents | Agent workflow changes |

Existing [agent.md](agent.md), [design_pattern.md](design_pattern.md), and [internal-commands.md](internal-commands.md) remain available as legacy references. The new documents consolidate their useful conventions and explicitly identify deviations in current code.

## Features and ownership

| Area | Primary implementation |
| --- | --- |
| Authentication, school ownership, sessions, branding, licensing, audit, push | `homeApp/` |
| Classes, subjects, teachers, staff, students, parents, attendance, exams, reports, timetable, leave | `managementApp/` |
| Accounts, charges, receipts, refunds, vouchers, payroll, ledger, reconciliation, controls | `financeApp/` |
| Teacher portal and versioned subject notes | `teacherApp/` |
| Student portal and role-specific data access | `studentApp/` |
| Certificate designs, issuance, numbering, QR support | `certificateApp/` |
| Rooms, participants, messages, read receipts, reactions, reports | `chatApp/` |
| Books, copies, members, circulation, reservations, fines | `libraryApp/` |
| Routes, stops, drivers, vehicles, assignments, fee records | `transportApp/` |
| Buildings, rooms, beds, admissions, assignments, fee records | `hostelApp/` |
| Categories, suppliers, stock ledger, assets, allocations | `inventoryApp/` |

These are source-code capabilities; completeness and end-to-end readiness must be validated per workflow.

## Runtime and prerequisites

- Python **3.14** (`.python-version`; `pyproject.toml` requires `>=3.14`).
- Django **5.2.10**; dependencies managed with **uv**, `pyproject.toml`, and `uv.lock`.
- SQLite is the fallback database. MySQL and PostgreSQL are configured alternatives.
- Redis at `redis://127.0.0.1:6379/1` is configured for Django caching.
- Native build libraries may be needed for `mysqlclient` and PDF/image dependencies. CI installs `libcairo2-dev`, `pkg-config`, and `python3-dev`; platform-specific prerequisites still require verification.
- No Node build pipeline is declared. UI uses Django templates, jQuery, Fomantic UI, DataTables, and dashboard charts.

`requirements.txt` differs from the uv dependency set and is not the CI installation source. Use the lockfile workflow unless intentionally updating dependency management.

## Docker setup with PostgreSQL

Docker Compose runs Django, PostgreSQL 17 and Redis 7 using the Alpine database and cache images. From the repository root, create a dedicated configuration and generate two secrets. Run the generator once for each empty value in the copied file.

```sh
cp -n .env.docker.example .env.docker
openssl rand -hex 32
```

Set `SECRET_KEY` and `DB_PASSWORD` in `.env.docker`, then start the stack.

```sh
./start.sh --db postgres --docker-up
```

The launcher reads `.env.docker` by default.

The example stores uploads, application files and PostgreSQL data under `~/docker-apps/schoolstackpro/production/{media,data,postgres}` on the host.

Open [SchoolStack on localhost](http://127.0.0.1:8000/). Set `APP_PORT` in the Docker environment file if port 8000 is occupied. PostgreSQL starts with an empty database on first launch. See the [Docker runbook](COMMANDS.md#docker-compose) for account creation, storage and schema upgrades.

For named VPS environments, reviewed-image releases, backup rehearsals and the portable generator, see the [Docker deployment guide](DOCKER_DEPLOYMENT_GUIDE.md).

## Local setup

Run from the repository root. Use a disposable development database when evaluating commands.

```sh
uv sync --frozen
```

For a fresh checkout, copy the [development template](.env.example) into the repository root and set `SECRET_KEY` to a unique generated value (see [COMMANDS.md](COMMANDS.md)). Keep an existing local file when updating.

```sh
cp -n .env.example .env
```

Django reads the root `.env` when settings initialize for management commands, background resync, ASGI and WSGI. Exported variables take precedence. Restart running application processes after edits. Use separate secrets and production settings on the server.

Start Redis using your local installation or service manager, then:

```sh
uv run python manage.py check
uv run python manage.py makemigrations --noinput
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`. Migration generation is included because the repository currently ignores numbered migration Python files and tracks only migration package initializers. This is an existing limitation, not a recommended long-term migration strategy. Review generated migrations; do not regenerate history against an existing production database blindly.

## Initial school onboarding

A Django superuser alone does not establish all school-domain records. Use the available administration/application flows to configure and verify:

1. The appropriate user groups (`Admin`, `Owner`, `Staff`, `Student`, `Teaching`) and linked owner account.
2. School details and ownership.
3. School session dates, active/current session, and school license state.
4. Classes, subjects, teacher assignments, students, and parents.
5. Staff role/action permissions for management access.
6. Finance settings and fee setup before posting financial activity.
7. Optional library, transport, hostel, inventory, and certificate configuration.

Exact minimum onboarding fields and ordering are not yet covered by a verified clean-install smoke test; track that work as T-008. Demo seed commands are for disposable datasets, not production onboarding.

## Configuration reference

Source of truth: `schoolStackPro/settings.py` and the relevant integration module. Values below describe code defaults, not live deployment values.

| Setting/input | Behavior |
| --- | --- |
| `SECRET_KEY` | Environment-supplied; configure before using Django |
| `DEBUG` | Defaults false; local HTTP should use true |
| `ALLOWED_HOSTS` | Comma-separated; defaults localhost, loopback, testserver |
| `DB_ENGINE` (legacy `db`) | `mysql` or `postgres` (case-insensitive); all other values select SQLite |
| `SQLITE_PATH` | Defaults to `db.sqlite3` in the repository root |
| `DB_NAME` | MySQL defaults to `schoolstack`, PostgreSQL to `schoolsstack` |
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Backend connection inputs. Set credentials explicitly. The default password is empty. |
| `DB_CONN_MAX_AGE` | Defaults 0 |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins. Unset retains the two existing production origins. An empty value clears them. |
| `USE_X_FORWARDED_HOST`, `TRUST_PROXY_SSL_HEADER` | Default false; enable only with the intended proxy configuration |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | Default to the inverse of DEBUG |
| `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, HSTS flags | Defaults false/0/false; production configuration needs review |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT` | SMTP backend default; port 587 |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Mail credentials and sender; keep secrets outside docs |
| `EMAIL_USE_TLS`, `EMAIL_USE_SSL` | Default true/false |
| `PASSWORD_RESET_TIMEOUT` | Default 86400 seconds |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_ADMIN_EMAIL` | Web push keys and contact URI |
| `PERFORMANCE_LOG_ENABLED` | Defaults true |
| `PERFORMANCE_QUERY_LOG_ENABLED` | Defaults to DEBUG |
| `PERFORMANCE_SLOW_REQUEST_MS`, `PERFORMANCE_QUERY_WARNING_COUNT` | Defaults 800 ms and 50 queries |

Set `REDIS_URL` for the cache, `STATIC_ROOT` and `MEDIA_ROOT` for storage, and `WRITE_LOG_ON_FILE` and `WRITE_LOG_ON_CONSOLE` for logging. Relative storage paths resolve from the repository root. `TIME_ZONE='Asia/Kolkata'` and `USE_TZ=False` are existing application assumptions.

## Validation and deployment

CI checks Django, generates local migrations, runs Django tests, and collects static files. See [COMMANDS.md](COMMANDS.md) for exact commands and filesystem effects. Production deployment is an SSH workflow triggered by pushes to `main` or manual dispatch; it resets the remote checkout, installs dependencies, generates/applies migrations, collects assets, and restarts the configured service.

The ASGI entry point is `schoolStackPro.asgi:application`; WSGI is `schoolStackPro.wsgi:application`. Default static and media output directories are siblings of the checkout, not children. Production hosting, backup/restore, and private-media policy need deployment-specific validation.

## Contributing

Read [AGENTS.md](AGENTS.md), [RULES.md](RULES.md), and the relevant domain sections before changing code. Preserve school/session isolation and server-side permission checks. Record completed work and actual validation evidence; do not mark planned checks as passed. These files stay current through contributor updates, not an automatic tracking service.
