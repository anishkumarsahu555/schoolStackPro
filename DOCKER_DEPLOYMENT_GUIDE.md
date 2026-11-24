# SchoolStack Docker deployment

## Upgrading an existing PostgreSQL 16 stack

Before starting this configuration against existing data, use the running PostgreSQL 16 server to take a logical dump. Restore it into a fresh PostgreSQL 17 data directory and verify the application before switching over. Keep the original PostgreSQL 16 storage and the matching uploads until verification is complete. Fresh installations can start directly with `postgres:17-alpine`.

## Start or update an environment

For a fresh install, copy `.env.example` to the repository-root `.env` and generate independent values for `SECRET_KEY` and `DB_PASSWORD`. On an existing stack, reuse its credentials, port, project name, storage paths and VAPID settings when consolidating configuration. Run the launcher from any directory. A relative `--env` argument resolves against your current directory.

```sh
./start.sh --db postgres --docker-up
./start.sh --db postgres --docker-logs
./start.sh --db postgres --docker-down
```

The launcher reads `.env` by default. Use `DB_ENGINE=postgres` for PostgreSQL. An explicit `--db` overrides `DATABASE_ENGINE`, which overrides `DB_ENGINE` for Docker. The example lists image, resource, port and storage overrides.

Each `--docker-up` builds a fresh image under `IMAGE_NAME`, which defaults to `PROJECT_NAME` (here `schoolstackpro`). Its tag includes the UTC timestamp, Git revision, optional `-dirty` marker and a random build ID. `APP_IMAGE` and `APP_IMAGE_TAG` can pin manual builds and direct Compose commands. To recreate web with an existing release through direct Compose, set `APP_IMAGE` to the exact printed release image. It prepares storage, waits for Redis and the database, checks Django and migration drift, then stops web. It runs migrations once and recreates web from the same image. A project lock serializes releases. A failed deployment after downtime leaves web stopped for investigation. Record the printed release image. `--docker-build` builds an image without starting services.

Gunicorn serves local uploads through Django at `MEDIA_URL` (default `/media/`). Forward this prefix to web when configuring the proxy. An alias to the host directory would bypass session authentication. To reuse existing files, point `MEDIA_DATA_DIR` at the directory containing the upload tree and grant UID 10001 access. The directory is mounted at `/srv/media` inside the container. The HTTP probe calls `/healthz` with an allowed host and requires a successful database query. `/livez` checks the HTTP process independently. Web binds to loopback for a host reverse proxy. Configure HTTPS, Django trusted origins, allowed hosts and secure cookies for the public hostname.

## Diagnose a slow build

Run `BUILDKIT_PROGRESS=plain ./start.sh --docker-build` to see the active command and layer timings without starting services. A new release tag still reuses layers on the same builder. A cold builder downloads system packages, locked Python dependencies and Chromium. The build and runtime stages share a locked, architecture-specific APT cache on the builder. Downloaded Debian packages and package lists stay available across layer rebuilds. The uv cache also retains Python downloads and built wheels when the lockfile changes. A first build on a fresh VPS still depends on its connection to the Debian mirror.

A PyPI timeout fetching `setuptools` while building `http-ece` occurs during build dependency resolution. The Docker build gives uv a 180-second HTTP timeout, five retries for retryable failures and four concurrent downloads. Override `UV_HTTP_TIMEOUT`, `UV_HTTP_RETRIES` or `UV_CONCURRENT_DOWNLOADS` in `.env` if needed. After a transient failure, rerun the launcher on the same builder to reuse the caches.

## Separate production and staging

For a new named VPS environment, use a separate private file for each stack. Include the following entries alongside its own credentials and Django settings. Edit the example’s identity, root and port entries in place. Keep the derived mount variables below these entries so Compose interpolates the selected values.

```dotenv
PROJECT_NAME=schoolstackpro
DEPLOY_ENV=staging
COMPOSE_PROJECT_NAME=schoolstackpro-staging
VPS_STORAGE_ROOT=/srv/apps
APP_PORT=8102
```

This selects the Compose project `schoolstackpro-staging` and host directories under `/srv/apps/schoolstackpro/staging` for PostgreSQL, media and application data. Use a distinct port and directories for each stack. `--port` sets both the published and internal ports. `HOST_PORT` sets only the published port. `WEB_WORKERS`, `WEB_TIMEOUT`, `WEB_MEMORY_LIMIT` and `POSTGRES_MEMORY_LIMIT` can be set per environment.

Keep existing project names and storage paths when upgrading. The original non-VPS setup retains its named PostgreSQL volume and, when `MEDIA_DATA_DIR` is unset and no path overrides apply, its named media volume. Existing local `APP_DATA_ROOT` selects `data` and `media_data` below it. A storage path or project name change selects different files and requires a planned copy or restore. Keep the same environment and database selection for later launcher commands. The launcher derives host directories from `APP_DATA_ROOT`. Direct Compose commands require explicit `MEDIA_DATA_DIR`, `DATA_DIR` and `POSTGRES_DATA_DIR` to use the same bind mounts. Keep those entries from the Docker environment template when switching between the two commands.

SQLite is available for an isolated alternative installation using `--db sqlite` and a separate project name and storage. It stores the database under `/data/db.sqlite3`. The controlled release flow below targets PostgreSQL.

## Release a reviewed PostgreSQL image

Restore the applicable numbered migration history to the build checkout before changing models. SchoolStack currently ignores numbered migrations in Git. Review generated incremental migrations and retain them with the release. See the [migration procedure](COMMANDS.md#docker-compose).

```sh
./release.sh generate
./release.sh check
APP_IMAGE=schoolstackpro:reviewed-001 ./start.sh --db postgres --docker-build
./release.sh deploy schoolstackpro:reviewed-001 /absolute/private/backups --maintenance
```

Choose a unique tag per reviewed build. Create the private backup directory outside the checkout before deploying. Pause external writers and take a coordinated copy of uploaded media. PostgreSQL and Redis must already be running. The script resolves the candidate to its local image ID and validates Django configuration and the migration plan before downtime.

During maintenance it stops web, saves a custom-format PostgreSQL dump, and creates a disposable PostgreSQL instance on a private network. It migrates a fresh database, restores the dump into a second database, migrates the restored copy and checks that Django reports no pending migrations on either database. After successful rehearsals it applies outstanding migrations to the selected database, waits for web readiness and verifies the running image ID. The release saves the candidate ID beside the dump.

The dump contains school records. Keep it private and back it up together with the matching media, configuration and previous image identity. On failure inspect the reported backup and migration state before reopening writes. Rehearse recovery in a separate stack with the matching PostgreSQL version, a fresh database, `pg_restore --exit-on-error --no-owner --no-acl`, the matching media tree and compatible application image. An application image rollback alone cannot reverse database changes.

## Generate a portable Compose scaffold

```sh
./dockerGenerator.sh --project example --output /tmp/example-stack --host-port 8103 --image example:release --database postgres --redis
```

The generator writes a separate Compose project, private environment files, ignore rules and a project-specific operations guide. It refuses to overwrite an existing Compose stack. It defaults to PostgreSQL and supports optional Redis. Use SchoolStack's checked-in Compose file for its migration, upload storage and release workflows.
