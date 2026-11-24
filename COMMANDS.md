# Commands and Operational Runbook

Baseline: 2026-11-17. Run from the repository root. Commands are drawn from source and workflows; listing a command does not mean it was executed in this documentation task. Review environment/database selection first. Substitute example IDs with verified development IDs.

## Docker Compose

Use Docker Engine with Compose v2.24.4 or later and the environment file from the [Docker setup](README.md#docker-setup-with-postgresql). Run these commands in the same checkout so Compose reuses the project volumes.

```sh
docker compose config --quiet
./start.sh --db postgres --docker-build
./start.sh --db postgres --docker-up
docker compose ps -a
./start.sh --db postgres --docker-logs
docker compose exec web python manage.py createsuperuser
./start.sh --db postgres --docker-down
```

The launcher reads `.env` by default.

Use `--port 8101` to override the Docker host port. Run `./start.sh --help` for local options, including `--prod`, `--migrate`, `--check-migrations` and `--dry-run`. For example, `./start.sh --env ./private.env --db postgres --check --no-run` checks the selected local configuration. The production launcher uses Gunicorn with Uvicorn workers to serve the project's ASGI routes, including uploaded media.

The Docker launcher builds the image, checks Django and model drift against the selected PostgreSQL service, and stops the web process before applying migrations. The migration and media directory initialization jobs must exit successfully before web starts. The web health check calls `/healthz` and requires Django to execute a database query. The example bind-mounts `POSTGRES_DATA_DIR` at `/var/lib/postgresql/data` and `DATA_DIR` at `/data`. Legacy configurations without host path settings use named volumes. The example binds `MEDIA_DATA_DIR` on the host to Django's `/srv/media`; set it to a dedicated absolute path on the VPS, for example `/srv/apps/schoolstackpro/production/media`. Existing files retain their relative paths and URLs. The initialization job prepares the mount directory for UID 10001. Existing subdirectories must already permit that UID to write wherever new uploads will be saved. Both database and uploads survive `--docker-down`.

Local media responses now require a signed-in session and send `Cache-Control: private, no-store`. When upgrading, purge any proxy cache for the media prefix. Previously downloaded files and fresh browser cache entries can outlive the change.

Existing deployments with `MEDIA_DATA_DIR` unset keep their named `media` volume. To switch one to a host directory, stop web writes, copy the contents of its `/srv/media/.` to the chosen host directory using `docker compose cp`, verify the copy and permissions, then set the variable and start the stack. Changing the mount alone does not copy files. Back up the database and uploaded files together. PostgreSQL initialization credentials apply when its volume is empty. Changing the password on an initialized stack requires updating the database role as well.

The image includes migration files present in the build checkout and runs `makemigrations` during the build to support a fresh installation. Because numbered migrations are ignored by Git, retain the exact generated history before replacing an image after model changes. For a stack bootstrapped from a clean checkout, extract the migration files from its running image into a clean release checkout (example for one app below, repeat for each app with a migration package). Review the subsequent incremental migrations and test against a restored database before upgrading. Rebuilding initial migrations from a changed schema can leave existing tables out of sync.

```sh
docker compose cp web:/app/financeApp/migrations/. ./financeApp/migrations/
```

The example cookie settings support loopback HTTP. When routing the loopback web port through an HTTPS reverse proxy, set the public `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and secure cookie settings for that site. Add SMTP and VAPID settings to the same file as needed. If using a different environment filename, supply its path as both `APP_ENV_FILE` and Compose's `--env-file`.

See the [Docker deployment guide](DOCKER_DEPLOYMENT_GUIDE.md) for release locking and image tags, named VPS environments, the optional SQLite stack, and `release.sh` and `dockerGenerator.sh` procedures.

### Docker web push

Set `VAPID_KEY_FILE` in the private Docker environment file to the absolute host path of the existing P-256 signing PEM. Keep it in a private directory outside the checkout and upload tree. The container user (UID/GID 10001) needs read access. Set `VAPID_PUBLIC_KEY` to the matching URL-safe base64 public key and `VAPID_ADMIN_EMAIL` to a contact URI. Reusing the key pair preserves existing subscriptions.

The launch and release scripts include `docker-compose.vapid.yml` when `VAPID_KEY_FILE` is set. For direct Compose commands that create or recreate web, include `-f docker-compose.yml -f docker-compose.vapid.yml` (also include `-f docker-compose.sqlite.yml` for SQLite). For the default PostgreSQL stack on macOS or Linux, setting `COMPOSE_FILE=docker-compose.yml:docker-compose.vapid.yml` in the Docker environment file also enables the secret for direct Compose commands. The secret appears only in web at `/run/secrets/vapid_private_key`.

## Environment and dependencies

```sh
uv sync --frozen
uv run python --version
uv run python -m django --version
```

`uv sync --frozen` installs the locked environment. It may need network access and platform build libraries. Dependency changes should update `pyproject.toml` and `uv.lock` deliberately; `uv lock` changes the lockfile and should not be part of a routine frozen install. `requirements.txt` is divergent legacy material.

Generate a local secret, then place it in an untracked `.env` rather than a committed document:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Django reads the repository-root `.env` with python-dotenv. For one-off local validation, exported environment variables take precedence over ordinary dotenv loading:

```sh
SECRET_KEY=local-check-only DEBUG=true db=sqlite uv run python manage.py check
```

This is a disposable check configuration, not a production secret. Set `SQLITE_PATH` to choose a disposable database path.

## Development bootstrap

```sh
uv run python manage.py check
uv run python manage.py makemigrations --noinput
uv run python manage.py migrate --plan
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

Effects: migration generation writes files; `migrate` changes schema/data; `createsuperuser` writes an account; `runserver` starts a local development service. Numbered migrations are currently ignored by Git. On a new checkout, generation precedes migration; on an established database, inspect history and the migration plan first.

## Checks and tests

```sh
uv run python manage.py check
uv run python manage.py showmigrations
uv run python manage.py test --noinput
uv run python manage.py test managementApp.tests --noinput
uv run python manage.py test homeApp.tests financeApp.tests teacherApp.tests inventoryApp.tests --noinput
uv run python manage.py test managementApp.tests.StaffAccessPermissionTests --noinput
uv run python manage.py test managementApp.tests.SessionImportRolloverTests --noinput
uv run python manage.py test financeApp.tests.FinanceApiRouteOwnershipTests --noinput
```

Django tests normally create a test database; backend permissions and service dependencies still matter. Prepare local migrations as CI does if working from a fresh checkout. Do not substitute production credentials for test setup. A missing Redis service can affect cache-using paths. `studentApp/tests.py` and `transportApp/tests.py` currently contain placeholders; their existence is not coverage.

CI order: frozen sync → check → makemigrations → test → collectstatic. No repository lint/type-check command is declared in the inspected project configuration.

## Assets and application serving

```sh
uv run python manage.py collectstatic --noinput
uv run uvicorn schoolStackPro.asgi:application --host 127.0.0.1 --port 8002
uv run gunicorn schoolStackPro.wsgi:application --bind 127.0.0.1:8002
```

Choose the serving entry point appropriate to the deployment; do not start both on the same port. ASGI and WSGI share the session-authenticated upload route. By default static collection writes to sibling `../static_cdn`; uploads use `../media_cdn`. These may require separate filesystem permissions.

CI/deploy use `collectstatic --noinput --clear`, which clears existing collected assets first. Use that variant only for an intended rebuild of the correct static output directory. The former README's `schoolStackPro:application` entry point did not match the defined ASGI callable; use the explicit module above.

## Management command discovery

```sh
uv run python manage.py help
uv run python manage.py help process_pending_fee_resyncs
uv run python manage.py help resync_session_fee_periods
uv run python manage.py help backfill_finance_document_numbers
uv run python manage.py help backfill_payroll_run_numbers
uv run python manage.py help profile_page_loads
uv run python manage.py help mock_data
uv run python manage.py help seed_session_import_demo
```

Inspect `--help` and the current command implementation before an operational run; flags and side effects may evolve.

## Fee-period maintenance

```sh
uv run python manage.py resync_session_fee_periods --session-id 123 --dry-run
uv run python manage.py resync_session_fee_periods --session-id 123 --create-missing --dry-run
uv run python manage.py process_pending_fee_resyncs --session-id 123 --batch-size 200
uv run python manage.py process_pending_fee_resyncs --batch-size 200 --max-sessions 20
```

The explicit resync requires a session ID. Remove `--dry-run` only when intentionally applying the reviewed scope. `--create-missing` includes missing month fee rows. Pending processing performs writes and does not declare a dry-run flag; an unscoped run can process multiple sessions. Record selected scope, counts, and failures. A command's existence does not prove it is scheduled on the deployment.

## Finance numbering maintenance

```sh
uv run python manage.py backfill_finance_document_numbers --school-id 1 --session-id 123 --type receipt voucher refund transaction payroll --dry-run
uv run python manage.py backfill_payroll_run_numbers --school-id 1 --session-id 123 --dry-run
```

Both commands support optional school/session scope and `--dry-run`. The finance document command defaults to receipt/voucher/refund/transaction when `--type` is omitted; payroll must be selected explicitly or handled by the dedicated command. Dry-run uses transactional rollback, so it still executes database work and is not a purely read-only query. Preserve a backup and review output before removing `--dry-run`.

## Demo data — disposable databases only

```sh
uv run python manage.py mock_data --size 60 --seed 42
uv run python manage.py mock_data --size 60 --seed 42 --keep-existing
uv run python manage.py seed_session_import_demo --keep-existing
```

Mock generation writes data and can replace previous mock rows unless keeping existing records. The session-import demo command without `--keep-existing` deletes/recreates the matching demo school. Do not run either against real school data or import `full_data.json` as a generic quickstart. No restore/import command is prescribed without knowing the destination and data provenance.

## Performance investigation

```sh
uv run python manage.py profile_page_loads --session-id 123 --repeat 3 --show-sql-summary
uv run python manage.py profile_page_loads --session-id 123 --path /management/home/ --json
```

Optional `--username` chooses the user; `--path` is repeatable, `--sql-limit` limits query-pattern output. The command authenticates a Django test client and requests real app pages, so use test data and expect session/page side effects. It reports application-level timing, not external browser latency. Keep SQL output out of shared artifacts if it contains sensitive values.

## Permission verification scripts

`verify_staff_permissions.py` and `verify_all_permissions_middleware.py` assume a particular existing account and modify groups, roles, permissions, and access records in the configured database. The former also uses Playwright. They are not normal read-only checks and are not part of the CI test command. Prefer isolated Django permission tests; refactoring the scripts is T-006.

## Backup and recovery

For the current MySQL database name, a reviewed manual backup pattern is:

```sh
mysqldump -u YOUR_DB_USER -p schoolstack > /secure/backup/location/schoolstack_backup.sql
```

Replace the destination with an access-controlled directory outside the checkout. This is a template; it does not establish consistency under concurrent writes, encryption, retention, or a successful restore. Protect media alongside database backups. PostgreSQL/SQLite recovery procedures need backend-specific validation. Do not publish dumps or put passwords directly on the command line.

Before schema/data maintenance: identify the backend and school/session scope, verify a recoverable backup, inspect the plan/dry-run where supported, apply in the intended environment, and verify resulting counts/invariants. T-007 tracks an executable restore and rollback procedure.

## Deployment workflow reference

Source: `.github/workflows/deploy.yml`.

| Input | Source / default |
| --- | --- |
| `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` | Repository/environment secrets |
| `DEPLOY_PORT` | Variable; default 22 |
| `DEPLOY_PATH` | Variable; default `/root/schoolManagement/schoolStackPro` |
| `DEPLOY_BRANCH` | Variable; default `main` |
| `DEPLOY_RESTART_COMMAND` | Variable; default `sudo systemctl restart schoolstack.service` |

A push to `main` or manual dispatch runs validation, then the production job configures SSH, fetches/resets the checkout, syncs dependencies, generates/applies migrations, clears/rebuilds static assets, and restarts. The hard reset discards tracked remote changes; deploying is not a harmless verification step.

The actual systemd service file is external to this repository. Verify its account, working directory, environment, executable, and module target on the host. Read-only diagnostics on the intended host include:

```sh
systemctl status schoolstack.service
journalctl -u schoolstack.service -n 100 --no-pager
```

Logs may contain sensitive information. Do not copy them wholesale into docs. No firewall flush, unrestricted network change, or production restart is part of local setup.

## Troubleshooting map

| Symptom | Inspect first |
| --- | --- |
| Missing secret/settings error | `.env` discovery and exported `SECRET_KEY` |
| Wrong database selected | Lowercase `db` value, not `USE_MYSQL` |
| Login fails to persist on local HTTP | DEBUG and secure-cookie flags |
| CSRF origin rejection | Comma-separated `CSRF_TRUSTED_ORIGINS`, including scheme and any port |
| Missing tables | Local generated migrations and `showmigrations` |
| Cache connection error | `REDIS_URL` and cache service availability |
| Static missing in production | Collection output, serving entry point, proxy mapping |
| Management action denied | Staff mapping, action/method, school/session, license |
| SMTP send failure | Backend, host, sender, TLS/SSL and credentials |

Record reproducible symptoms and sanitized evidence in TASKS.md; do not fix operational configuration by weakening authentication or disabling validation globally.
