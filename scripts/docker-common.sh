# Both launchers use the same Compose identity, storage, and release lock.
configure_stack() {
    local selected=$1 requested=${2:-} resolved key value
    [[ -f $selected ]] || fail "Missing Docker environment file: $selected"
    export APP_ENV_FILE="$(cd "$(dirname "$selected")" && pwd)/$(basename "$selected")"
    # Ask Compose to interpret dotenv quoting and shell overrides. Never source secrets.
    resolved=$(docker compose --env-file "$APP_ENV_FILE" -f - config --environment <<'YAML'
name: schoolstack-config-probe
services:
  probe:
    image: scratch
YAML
    ) || fail 'Invalid Docker environment'
    while IFS='=' read -r key value; do
        case "$key" in
            COMPOSE_PROJECT_NAME) [[ $value == schoolstack-config-probe ]] || export "$key=$value" ;;
            VAPID_KEY_FILE|PROJECT_NAME|DEPLOY_ENV|VPS_STORAGE_ROOT|APP_DATA_ROOT|DATA_DIR|MEDIA_DATA_DIR|POSTGRES_DATA_DIR|DATABASE_ENGINE|DB_ENGINE|APP_IMAGE|IMAGE_NAME|PORT|HOST_PORT|APP_PORT) export "$key=$value" ;;
        esac
    done <<< "$resolved"
    if [[ -n ${VPS_STORAGE_ROOT:-} || -n ${DEPLOY_ENV:-} ]]; then
        export PROJECT_NAME=${PROJECT_NAME:-schoolstackpro} DEPLOY_ENV=${DEPLOY_ENV:-production}
        [[ $PROJECT_NAME =~ ^[a-z0-9][a-z0-9_-]*$ && $DEPLOY_ENV =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail 'Invalid project or environment name'
        export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-$PROJECT_NAME-$DEPLOY_ENV}
        local root=${APP_DATA_ROOT:-${VPS_STORAGE_ROOT:-/srv/apps}/$PROJECT_NAME/$DEPLOY_ENV}
        export DATA_DIR=${DATA_DIR:-$root/data} MEDIA_DATA_DIR=${MEDIA_DATA_DIR:-$root/media} POSTGRES_DATA_DIR=${POSTGRES_DATA_DIR:-$root/postgres}
    elif [[ -n ${APP_DATA_ROOT:-} ]]; then
        export DATA_DIR=${DATA_DIR:-$APP_DATA_ROOT/data} MEDIA_DATA_DIR=${MEDIA_DATA_DIR:-$APP_DATA_ROOT/media_data}
    fi
    export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-${PROJECT_NAME:-schoolstackpro}}
    [[ $COMPOSE_PROJECT_NAME =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail 'Invalid Compose project name'
    export APP_PORT=${APP_PORT:-${PORT:-8000}}
    export HOST_PORT=${HOST_PORT:-${PORT:-$APP_PORT}}
    compose=(docker compose --env-file "$APP_ENV_FILE" -f "$repo/docker-compose.yml")
    engine=${requested:-${DATABASE_ENGINE:-${DB_ENGINE:-postgres}}}
    case "$engine" in
        postgres|postgresql) engine=postgres ;;
        sqlite|sqlite3)
            engine=sqlite
            # The unused postgres profile still undergoes Compose interpolation.
            export DB_PASSWORD=unused-sqlite-profile
            compose+=(-f "$repo/docker-compose.sqlite.yml") ;;
        *) fail 'Choose postgres or sqlite' ;;
    esac
    if [[ -n ${VAPID_KEY_FILE:-} ]]; then
        compose+=(-f "$repo/docker-compose.vapid.yml")
    fi
    "${compose[@]}" config --quiet
}

lock_stack() {
    lock="/tmp/schoolstack-deploy-$COMPOSE_PROJECT_NAME.lock"
    mkdir "$lock" || fail "Another release holds $lock"
}
