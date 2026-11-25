#!/usr/bin/env bash
set -euo pipefail
umask 077
fail() { printf '%s\n' "$*" >&2; exit 1; }
action=${1:?Choose a Docker action}
selected=${2:-${APP_ENV_FILE:-}}
if [[ -n $selected && $selected != /* ]]; then selected="$PWD/$selected"; fi
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
dry=${4:-0}
[[ $dry == 0 || $dry == 1 ]] || fail 'Invalid dry-run mode'
run() {
    if ((dry)); then printf '%q ' "$@"; printf '\n'; else "$@"; fi
}
source scripts/docker-common.sh
configure_stack "${selected:-$repo/.env}" "${3:-}"

docker_up() (
    set -e
    if ((!dry)); then lock_stack; fi
    paused=false
    cleanup_up() {
        local status=$?
        if [[ $paused == true && $status != 0 ]]; then "${compose[@]}" stop web >&2 || true; fi
        if ((!dry)); then rmdir "$lock"; fi
    }
    trap cleanup_up EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    local revision dirty='' build_id
    revision=$(git rev-parse --verify --short=12 HEAD)
    [[ -z $(git status --porcelain --untracked-files=normal) ]] || dirty=-dirty
    build_id=$(openssl rand -hex 6)
    export APP_IMAGE_TAG="release-$(date -u +%Y%m%dT%H%M%SZ)-$revision$dirty-$build_id"
    export APP_IMAGE="${IMAGE_NAME:-${PROJECT_NAME:-schoolstackpro}}:$APP_IMAGE_TAG"
    printf 'Building release image: %s\n' "$APP_IMAGE"
    run "${compose[@]}" build web
    run "${compose[@]}" run --no-deps --rm -T storage-init
    local services=(redis)
    if [[ $engine == postgres ]]; then services+=(postgres); fi
    run "${compose[@]}" up -d --no-deps --wait "${services[@]}"
    run "${compose[@]}" run --rm --no-deps -T migrate python manage.py check
    run "${compose[@]}" run --rm --no-deps -T migrate python manage.py makemigrations --check --dry-run
    paused=true
    run "${compose[@]}" stop web
    run "${compose[@]}" run --rm --no-deps -T migrate python manage.py migrate --noinput
    run "${compose[@]}" up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 240 web
    paused=false
    printf 'Release image: %s\n' "$APP_IMAGE"
)

case "$action" in
    --docker-build) run "${compose[@]}" build web ;;
    --docker-up) docker_up ;;
    --docker-logs) run "${compose[@]}" logs --follow --tail=100 ;;
    --docker-down) run "${compose[@]}" down ;;
    *) fail "Unknown Docker action: $action" ;;
esac
