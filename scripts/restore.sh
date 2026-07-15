#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT=/opt/sunlite-scheduler/current
CONFIG_ROOT=/etc/sunlite-scheduler
DATABASE=/var/lib/sunlite-scheduler/sunlite.db

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ $# -eq 1 ]] || fail "usage: sunlite-restore BACKUP.tar.gz"
archive=$(readlink -f "$1")
[[ -f "$archive" ]] || fail "backup not found: $archive"
maintenance="$APP_ROOT/.venv/bin/sunlite-maintenance"
[[ -x "$maintenance" ]] || fail "Sunlite is not installed"

"$maintenance" verify "$archive" >/dev/null
safety_backup=$(/usr/local/sbin/sunlite-backup)
controller_active=false
web_active=false
systemctl is-active --quiet sunlite-controller.service && controller_active=true
systemctl is-active --quiet sunlite-web.service && web_active=true

restart_previous_state() {
  trap - ERR
  if $controller_active || $web_active; then
    systemctl start sunlite-controller.service || true
  fi
  if $web_active; then
    systemctl start sunlite-web.service || true
  fi
}
trap restart_previous_state ERR

systemctl stop sunlite-web.service sunlite-controller.service
"$maintenance" restore "$archive" \
  --database "$DATABASE" \
  --config "$CONFIG_ROOT/config.toml" \
  --environment "$CONFIG_ROOT/web.env"
# shellcheck disable=SC1091
source "$CONFIG_ROOT/web.env"
health_host=${SUNLITE_WEB_HOST:-0.0.0.0}
[[ $health_host == "0.0.0.0" ]] && health_host=127.0.0.1
[[ $health_host == "::" ]] && health_host="::1"
[[ $health_host == *:* ]] && health_host="[$health_host]"
health_url="http://$health_host:${SUNLITE_WEB_PORT:-8000}/health"
chown sunlite-controller:sunlite "$DATABASE"
chown root:sunlite "$CONFIG_ROOT/config.toml"
chown root:sunlite-web "$CONFIG_ROOT/web.env"
chmod 0640 "$DATABASE" "$CONFIG_ROOT/config.toml" "$CONFIG_ROOT/web.env"
trap - ERR
if $controller_active || $web_active; then
  systemctl start sunlite-controller.service
fi
if $web_active; then
  systemctl start sunlite-web.service
  healthy=false
  for _attempt in {1..20}; do
    if curl --fail --silent --max-time 2 "$health_url" >/dev/null; then
      healthy=true
      break
    fi
    sleep 1
  done
  $healthy || fail "restored web service did not become healthy"
fi

echo "Restored $archive"
echo "Pre-restore safety backup: $safety_backup"
