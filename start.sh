#!/usr/bin/env bash
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: ./start.sh [options]
  --docker-build | --docker-up | --docker-logs | --docker-down
      Build, start and wait for health, follow logs, or stop and retain storage.
  -e, --env, --env-file FILE   Configuration (Docker default: .env.docker)
  --db postgres|sqlite         PostgreSQL is the Docker default.
  Example: ./start.sh --db postgres --docker-up
  -p, --port PORT              Local listener or Docker published port
  -b, --bind, --host HOST      Local bind address
  --prod, --production         Run Gunicorn with an ASGI worker
  -w, --workers N              Production worker count (default 1)
  --timeout SECONDS            Gunicorn timeout (default 120)
  -s, --sync                   Install frozen dependencies with uv
  -c, --check                  Check Django (deployment checks in production mode)
  --check-deploy               Run Django deployment checks
  -t, --test                   Run Django tests
  -m, --migrate                Apply migrations
  --check-migrations           Check for pending migrations
  --collectstatic              Collect static files
  --createsuperuser            Run interactive superuser creation
  --reset-db                   Prompt before flushing the selected local database
  --shell                      Start the Django shell
  -n, --no-run                 Finish after preparation
  --noreload                   Disable the development auto-reloader
  -d, --dry-run                Print commands without running them
  -h, --help                   Show help
HELP
}
run() {
    if ((dry)); then printf '%q ' "$@"; printf '\n'; else "$@"; fi
}
positive() { [[ $2 =~ ^[1-9][0-9]{0,8}$ ]] || fail "$1 requires a positive integer"; }

caller=$PWD
cd "$(dirname "${BASH_SOURCE[0]}")"
repo=$PWD
port='' host='' selected=${ENV_FILE:-} backend='' action=''
workers=${WEB_WORKERS:-1} timeout=${WEB_TIMEOUT:-120}
prod=0 dry=0 sync=0 check=0 deploy_check=0 migrate=0 pending=0 tests=0
assets=0 superuser=0 shell_mode=0 reset=0 no_run=0 noreload=0 local_options=0
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --docker-build|--docker-up|--docker-logs|--docker-down)
            [[ -z $action ]] || fail 'Select one Docker action'
            action=$1 ;;
        --env|--env-file|-e|--db|--port|-p|--bind|--host|-b|--workers|-w|--timeout)
            [[ -n ${2:-} && $2 != -* ]] || fail "$1 requires a value"
            case "$1" in
                -e|--env|--env-file) selected=$2 ;;
                --db) case "$2" in postgres|postgresql) backend=postgres ;; sqlite|sqlite3) backend=sqlite ;; *) fail 'Use --db postgres or sqlite' ;; esac ;;
                -p|--port) positive "$1" "$2"; (( $2 <= 65535 )) || fail 'Port exceeds 65535'; port=$2 ;;
                -b|--host|--bind) host=$2; local_options=1 ;;
                -w|--workers) positive "$1" "$2"; workers=$2; local_options=1 ;;
                --timeout) positive "$1" "$2"; timeout=$2; local_options=1 ;;
            esac
            shift ;;
        --dry-run|-d) dry=1 ;;
        *)
            local_options=1
            case "$1" in
                -s|--sync) sync=1 ;; -c|--check) check=1 ;; --check-deploy) deploy_check=1 ;;
                -m|--migrate) migrate=1 ;; --check-migrations) pending=1 ;; -t|--test) tests=1 ;;
                --collectstatic) assets=1 ;; --createsuperuser) superuser=1 ;; --reset-db) reset=1 ;;
                --shell) shell_mode=1 ;; --prod|--production) prod=1 ;; -n|--no-run) no_run=1 ;;
                --noreload) noreload=1 ;; *) fail "Unknown option: $1" ;;
            esac ;;
    esac
    shift
done
if [[ -n $selected && $selected != /* ]]; then selected="$caller/$selected"; fi
if [[ -n $action ]]; then
    ((local_options == 0)) || fail 'Docker actions accept --env, --db, --port and --dry-run'
    if [[ -n $port ]]; then export HOST_PORT=$port APP_PORT=$port; fi
    exec "$repo/scripts/docker-deploy.sh" "$action" "${selected:-${APP_ENV_FILE:-$repo/.env.docker}}" "$backend" "$dry"
fi

if [[ -n $selected ]]; then
    [[ -f $selected ]] || fail "Missing $selected"
    export SCHOOLSTACK_ENV_FILE=$selected
fi
if [[ -n $backend ]]; then export DB_ENGINE=$backend db=$backend; fi
if ((sync)); then run uv sync --frozen; fi
if [[ ${SCHOOLSTACK_CONTAINER:-0} == 1 ]]; then runner=(env); else runner=(uv run --frozen); fi
manage() { run "${runner[@]}" python manage.py "$@"; }
if ((reset)); then
    if ((dry)); then manage flush --noinput
    elif read -r -p 'Delete all records in the selected database? [y/N] ' answer && [[ $answer == [yY] ]]; then manage flush --noinput; fi
fi
if ((deploy_check || (prod && check))); then manage check --deploy; elif ((check)); then manage check; fi
if ((tests)); then manage test; fi
if ((migrate)); then manage migrate --noinput; fi
if ((pending)); then manage migrate --check; fi
if ((assets)); then manage collectstatic --noinput; fi
if ((superuser)); then manage createsuperuser; fi
if ((shell_mode)); then manage shell; exit 0; fi
if ((no_run)); then exit 0; fi
if ((prod)); then
    cmd=(gunicorn -c "$repo/gunicorn.conf.py" schoolStackPro.asgi:application -k uvicorn.workers.UvicornWorker
         --bind "${host:-0.0.0.0}:${port:-${APP_PORT:-8000}}" --workers "$workers" --timeout "$timeout" --access-logfile -)
else
    cmd=(python manage.py runserver "${host:-127.0.0.1}:${port:-8000}")
    if ((noreload)); then cmd+=(--noreload); fi
fi
if ((dry)); then run "${runner[@]}" "${cmd[@]}"; else exec "${runner[@]}" "${cmd[@]}"; fi
