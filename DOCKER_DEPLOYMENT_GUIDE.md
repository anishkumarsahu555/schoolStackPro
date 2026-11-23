# SchoolStack Docker deployment

## Upgrading an existing PostgreSQL 16 stack

Before starting this configuration against existing data, use the running PostgreSQL 16 server to take a logical dump. Restore it into a fresh PostgreSQL 17 data directory and verify the application before switching over. Keep the original PostgreSQL 16 storage and the matching uploads until verification is complete. Fresh installations can start directly with `postgres:17-alpine`.

## Start or update an environment

Copy `.env.docker.example` to a private file and generate independent values for `SECRET_KEY` and `DB_PASSWORD`. Run the launcher from any directory. A relative `--env` argument resolves against your current directory.

```sh
./start.sh --db postgres --docker-up
./start.sh --db postgres --docker-logs
./start.sh --db postgres --docker-down
```

The launcher reads `.env.docker` by default. Use `DB_ENGINE=postgres` for PostgreSQL. An explicit `--db` overrides `DATABASE_ENGINE`, which overrides `DB_ENGINE` for Docker. The example lists image, resource, port and storage overrides.

The launcher builds and runs the configured `APP_IMAGE`. When that variable is unset it generates a timestamped tag with the Git revision and a dirty-checkout marker when applicable. It prepares storage, waits for Redis and the database, checks Django and migration drift, then stops web. It runs migrations once and recreates web from the same image. A project lock serializes releases. A failed deployment after downtime leaves web stopped for investigation. Record the printed release image. `--docker-build` builds an image without starting services.

Gunicorn serves the ASGI application, including uploads. The HTTP probe calls `/healthz` with an allowed host and requires a successful database query. `/livez` checks the HTTP process independently. Web binds to loopback for a host reverse proxy. Configure HTTPS, Django trusted origins, allowed hosts and secure cookies for the public hostname.

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

Keep existing project names and storage paths when upgrading. The original non-VPS setup retains its named PostgreSQL volume and, when `MEDIA_DATA_DIR` is unset and no path overrides apply, its named media volume. Existing local `APP_DATA_ROOT` selects `data` and `media_data` below it. A storage path or project name change selects different files and requires a planned copy or restore. Keep the same environment and database selection for later launcher commands. For direct Compose commands against VPS defaults, include the resolved project identity and mounts in the environment file, or use the launcher.

SQLite is available for an isolated alternative installation using `--db sqlite` and a separate project name and storage. It stores the database under `/data/db.sqlite3`. The controlled release flow below targets PostgreSQL.

## Release a reviewed PostgreSQL image

Restore the applicable numbered migration history to the build checkout before changing models. SchoolStack currently ignores numbered migrations in Git. Review generated incremental migrations and retain them with the release. See the [migration procedure](COMMANDS.md#docker-compose).

```sh
./release.sh generate
./release.sh check
APP_IMAGE=schoolstackpro:reviewed-001 ./start.sh --db postgres --docker-build
APP_ENV_FILE=.env.docker ./release.sh deploy schoolstackpro:reviewed-001 /absolute/private/backups --maintenance
```

Choose a unique tag per reviewed build. Create the private backup directory outside the checkout before deploying. Pause external writers and take a coordinated copy of uploaded media. PostgreSQL and Redis must already be running. The script resolves the candidate to its local image ID and validates Django configuration and the migration plan before downtime.

During maintenance it stops web, saves a custom-format PostgreSQL dump, and creates a disposable PostgreSQL instance on a private network. It migrates a fresh database, restores the dump into a second database, migrates the restored copy and checks that Django reports no pending migrations on either database. After successful rehearsals it applies outstanding migrations to the selected database, waits for web readiness and verifies the running image ID. The release saves the candidate ID beside the dump.

The dump contains school records. Keep it private and back it up together with the matching media, configuration and previous image identity. On failure inspect the reported backup and migration state before reopening writes. Rehearse recovery in a separate stack with the matching PostgreSQL version, a fresh database, `pg_restore --exit-on-error --no-owner --no-acl`, the matching media tree and compatible application image. An application image rollback alone cannot reverse database changes.

## Generate a portable Compose scaffold

```sh
./dockerGenerator.sh --project example --output /tmp/example-stack --host-port 8103 --image example:release --database postgres --redis
```

The generator writes a separate Compose project, private environment files, ignore rules and a project-specific operations guide. It refuses to overwrite an existing Compose stack. It defaults to PostgreSQL and supports optional Redis. Use SchoolStack's checked-in Compose file for its migration, upload storage and release workflows.
