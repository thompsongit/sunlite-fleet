#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_ROOT=/opt/sunlite-scheduler/current
CONFIG_ROOT=/etc/sunlite-scheduler
DATABASE=/var/lib/sunlite-scheduler/sunlite.db
BACKUP_ROOT=/var/backups/sunlite-scheduler

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -x "$APP_ROOT/.venv/bin/sunlite-maintenance" ]] || fail "Sunlite is not installed"
install -d -m 0700 -o root -g root "$BACKUP_ROOT"

output=${1:-$BACKUP_ROOT/sunlite-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}
release=$(basename "$(readlink -f "$APP_ROOT")")
"$APP_ROOT/.venv/bin/sunlite-maintenance" backup \
  --database "$DATABASE" \
  --config "$CONFIG_ROOT/config.toml" \
  --environment "$CONFIG_ROOT/web.env" \
  --output "$output" \
  --release "$release"
echo "$output"
