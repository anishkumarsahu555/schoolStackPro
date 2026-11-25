#!/usr/bin/env bash
set -euo pipefail
umask 077
caller=$PWD
cd "$(dirname "${BASH_SOURCE[0]}")"
repo=$(pwd -P)
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
./release.sh generate [app_label ...]           Generate local migrations for review
./release.sh check                              Check local model/migration drift
./release.sh deploy IMAGE BACKUP_DIR --maintenance

Use APP_ENV_FILE to select the running PostgreSQL stack (default .env).
Supply a local candidate image and an existing private backup directory outside
the checkout. Pause all external writers first. The release stops web, saves a
PostgreSQL custom-format dump, rehearses fresh and restored migrations, then
migrates the selected stack once and starts the verified image. On a failure
after downtime, inspect the backup and schema before restarting web.
HELP
}
case "${1:---help}" in
    -h|--help) usage; exit 0 ;;
    generate|check|deploy) mode=$1; shift ;;
    *) usage >&2; exit 2 ;;
esac
deleted=$(git diff --diff-filter=D --name-only HEAD -- ':(glob)**/migrations/*.py')
[[ -z $deleted ]] || fail 'Restore or reconcile deleted migration history first'
case "$mode" in
    generate) uv run --frozen python manage.py check; exec uv run --frozen python manage.py makemigrations "$@" ;;
    check) [[ $# == 0 ]] || fail 'check takes no arguments'; uv run --frozen python manage.py check; exec uv run --frozen python manage.py makemigrations --check --dry-run ;;
    deploy) [[ $# == 3 && $3 == --maintenance ]] || { usage >&2; exit 2; } ;;
esac
[[ $2 == /* && -d $2 ]] || fail 'Use an existing absolute backup directory'
destination=$(cd "$2" && pwd -P)
case "$destination/" in "$repo/"*) fail 'Choose a backup directory outside the checkout' ;; esac
selected=${APP_ENV_FILE:-$repo/.env}
[[ $selected == /* ]] || selected="$caller/$selected"
source scripts/docker-common.sh
configure_stack "$selected"
[[ $engine == postgres ]] || fail 'Controlled releases require PostgreSQL'
image_id=$(docker image inspect --format '{{.Id}}' "$1")
export APP_IMAGE=$image_id
pg_id=$("${compose[@]}" ps -q postgres)
[[ -n $pg_id && $(docker inspect --format '{{.State.Health.Status}}' "$pg_id") == healthy ]] || fail 'Start the selected PostgreSQL stack first'
pg_image=$(docker inspect --format '{{.Image}}' "$pg_id")
lock_stack
work='' network='' trial_id='' backup='' partial='' paused=false
cleanup() {
    local status=$?
    trap - EXIT
    if [[ $paused == true && $status != 0 ]]; then
        "${compose[@]}" stop web >&2 || true
        printf 'Release stopped during maintenance. Inspect the database before restarting web. Backup: %s\n' "${backup:-incomplete}" >&2
    fi
    if [[ -n $trial_id ]]; then docker rm -fv "$trial_id" >/dev/null || true; fi
    if [[ -n $network ]]; then docker network rm "$network" >/dev/null || true; fi
    if [[ -n $partial ]]; then rm -f "$partial"; fi
    if [[ -n $work ]]; then rm -rf -- "$work"; fi
    rmdir "$lock"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
work=$(mktemp -d)
manage() { "${compose[@]}" run --rm --no-deps --pull never -T migrate python manage.py "$@"; }
"${compose[@]}" run --rm --no-deps --pull never -T storage-init
"${compose[@]}" run --rm --no-deps --pull never -T web ./start.sh --check-migrations --dry-run >/dev/null
manage check
manage makemigrations --check --dry-run
manage migrate --plan
paused=true
"${compose[@]}" stop web
partial=$(mktemp "$destination/before-release-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
"${compose[@]}" exec -T postgres sh -ec 'exec pg_dump --format=custom --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' > "$partial"
[[ -s $partial ]] || fail 'Empty database backup'
mv "$partial" "$partial.dump"
backup="$partial.dump"
partial=''
printf 'Database backup: %s\n' "$backup"

trial_name="schoolstack-rehearsal-$(openssl rand -hex 8)"
network=$(docker network create --internal "$trial_name")
password=$(openssl rand -hex 32)
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_DB=fresh\n' "$password" > "$work/postgres.env"
printf 'SECRET_KEY=%s\ndb=postgres\nDB_HOST=trial-db\nDB_USER=postgres\nDB_PASSWORD=%s\n' "$(openssl rand -hex 32)" "$password" > "$work/app.env"
trial_id=$(docker run -d --network "$network" --network-alias trial-db --memory 768m --cpus 1 --env-file "$work/postgres.env" "$pg_image")
ready=false
for ((i=0; i<90; i++)); do
    if docker exec "$trial_id" pg_isready -h 127.0.0.1 -U postgres -d fresh >/dev/null 2>&1; then ready=true; break; fi
    sleep 2
done
[[ $ready == true ]] || fail 'Disposable PostgreSQL did not become ready'
trial_manage() {
    local database=$1; shift
    docker run --rm --network "$network" --memory 768m --cpus 1 --env-file "$work/app.env" -e "DB_NAME=$database" "$image_id" python manage.py "$@"
}
trial_manage fresh migrate --noinput
trial_manage fresh migrate --check
docker exec "$trial_id" createdb -U postgres restored
docker exec -i "$trial_id" pg_restore -U postgres -d restored --exit-on-error --no-owner --no-acl < "$backup"
trial_manage restored check
trial_manage restored migrate --plan
trial_manage restored migrate --noinput
trial_manage restored migrate --check
manage migrate --noinput
manage migrate --check
"${compose[@]}" up -d --no-deps --no-build --force-recreate --pull never --wait --wait-timeout 240 web
web_id=$("${compose[@]}" ps -q web)
[[ $(docker inspect --format '{{.Image}}' "$web_id") == "$image_id" ]] || fail 'Running image differs from reviewed candidate'
paused=false
printf '%s\n' "$image_id" > "$backup.image-id"
printf 'Release healthy. Backup: %s\nImage: %s\n' "$backup" "$image_id"
