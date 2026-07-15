#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT=/opt/sunlite-scheduler/current
CONFIG_ROOT=/etc/sunlite-scheduler

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -x "$APP_ROOT/.venv/bin/sunlite-maintenance" ]] || fail "Sunlite is not installed"

"$APP_ROOT/.venv/bin/sunlite-maintenance" check \
  --config "$CONFIG_ROOT/config.toml" --environment "$CONFIG_ROOT/web.env"
# shellcheck disable=SC1091
source "$CONFIG_ROOT/web.env"
health_host=${SUNLITE_WEB_HOST:-0.0.0.0}
[[ $health_host == "0.0.0.0" ]] && health_host=127.0.0.1
[[ $health_host == "::" ]] && health_host="::1"
[[ $health_host == *:* ]] && health_host="[$health_host]"
health_url="http://$health_host:${SUNLITE_WEB_PORT:-8000}/health"
systemd-analyze verify /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service
systemctl enable --now sunlite-controller.service
systemctl enable --now sunlite-web.service

healthy=false
for _attempt in {1..20}; do
  if curl --fail --silent --max-time 2 "$health_url" >/dev/null; then
    healthy=true
    break
  fi
  sleep 1
done
$healthy || fail "web health check failed; inspect journalctl -u sunlite-web"
echo "Sunlite services are active and healthy."
