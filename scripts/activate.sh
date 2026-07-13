#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT=/opt/sunlite-scheduler/current
CONFIG_ROOT=/etc/sunlite-scheduler

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -x "$APP_ROOT/.venv/bin/sunlite-maintenance" ]] || fail "Sunlite is not installed"

"$APP_ROOT/.venv/bin/sunlite-maintenance" check \
  --config "$CONFIG_ROOT/config.toml" --environment "$CONFIG_ROOT/web.env"
systemd-analyze verify /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service
systemctl enable --now sunlite-controller.service
systemctl enable --now sunlite-web.service

healthy=false
for _attempt in {1..20}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null; then
    healthy=true
    break
  fi
  sleep 1
done
$healthy || fail "web health check failed; inspect journalctl -u sunlite-web"
echo "Sunlite services are active and healthy."
