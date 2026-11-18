#!/usr/bin/env bash
#
# Offline backup/restore for the Compose deployment.
#
#   ./scripts/compose-backup.sh backup
#   ./scripts/compose-backup.sh verify ./backups/<stamp>
#   ./scripts/compose-backup.sh restore ./backups/<stamp> [--force]
#
# The backend is stopped while ChromaDB is copied; application and checkpoint
# SQLite databases are copied with the SQLite backup API. Backups are staged in
# the `opspilot-backups` volume and then copied to the host `./backups/` folder.
#
# Set COMPOSE_PROJECT_NAME to operate on an isolated test stack. The script only
# touches the project selected by Compose and never removes pre-existing volumes.

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=${COMPOSE:-"docker compose"}
BACKUP_DIR_IN_CONTAINER=/var/lib/opspilot/backups
BACKUP_DIR_ON_HOST=./backups

backend_running() {
    [ -n "$($COMPOSE ps -q backend 2>/dev/null)" ]
}

require_backend_container() {
    if [ -z "$($COMPOSE ps -aq backend 2>/dev/null)" ]; then
        echo "backend container does not exist; run 'docker compose up -d' first" >&2
        return 1
    fi
}

wait_for_backend() {
    local deadline=$((SECONDS + 60))
    until [ "$($COMPOSE ps --format '{{.Health}}' backend 2>/dev/null || true)" = "healthy" ]; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "backend did not become healthy after restart" >&2
            return 1
        fi
        sleep 2
    done
}

run_in_volume() {
    # One-off container using the backend image, environment and volumes.
    $COMPOSE run --rm --no-deps -T backend "$@"
}

command_backup() {
    local stamp
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    require_backend_container

    if backend_running; then
        echo "stopping backend for a consistent backup..."
        $COMPOSE stop backend
    else
        echo "backend is not running; continuing"
    fi

    if ! run_in_volume python scripts/backup_restore.py backup \
        --output-dir "$BACKUP_DIR_IN_CONTAINER/$stamp"; then
        echo "backup failed; restarting backend" >&2
        $COMPOSE start backend || true
        return 1
    fi

    # `docker cp` (used by compose cp) also works against a stopped container,
    # and the backend is the only long-lived container with the volumes.
    mkdir -p "$BACKUP_DIR_ON_HOST"
    $COMPOSE cp "backend:$BACKUP_DIR_IN_CONTAINER/$stamp" "$BACKUP_DIR_ON_HOST/$stamp"

    echo "restarting backend..."
    $COMPOSE start backend
    wait_for_backend
    echo "backup available at $BACKUP_DIR_ON_HOST/$stamp"
}

command_verify() {
    local source=$1
    local name
    name=$(basename "$source")
    if [ ! -d "$source" ]; then
        echo "backup directory not found: $source" >&2
        return 1
    fi
    require_backend_container
    $COMPOSE cp "$source" "backend:$BACKUP_DIR_IN_CONTAINER/$name"
    run_in_volume python scripts/backup_restore.py verify \
        --backup-dir "$BACKUP_DIR_IN_CONTAINER/$name"
}

command_restore() {
    local source=$1
    shift
    local name
    name=$(basename "$source")
    if [ ! -d "$source" ]; then
        echo "backup directory not found: $source" >&2
        return 1
    fi
    require_backend_container

    if backend_running; then
        echo "stopping backend for restore..."
        $COMPOSE stop backend
    fi

    $COMPOSE cp "$source" "backend:$BACKUP_DIR_IN_CONTAINER/$name"
    if ! run_in_volume python scripts/backup_restore.py restore \
        --backup-dir "$BACKUP_DIR_IN_CONTAINER/$name" "$@"; then
        echo "restore failed; backend left stopped" >&2
        return 1
    fi

    echo "starting backend..."
    $COMPOSE start backend
    wait_for_backend
    echo "restore complete"
}

case "${1:-}" in
    backup)
        command_backup
        ;;
    verify)
        [ $# -eq 2 ] || { echo "usage: $0 verify <backup-dir>" >&2; exit 2; }
        command_verify "$2"
        ;;
    restore)
        [ $# -ge 2 ] || { echo "usage: $0 restore <backup-dir> [--force]" >&2; exit 2; }
        shift
        command_restore "$@"
        ;;
    *)
        echo "usage: $0 {backup|verify <backup-dir>|restore <backup-dir> [--force]}" >&2
        exit 2
        ;;
esac
