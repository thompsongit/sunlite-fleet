#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

SOURCE_ROOT=${1:-$PWD}
SOURCE_ROOT=$(cd "$SOURCE_ROOT" && pwd)
APP_ROOT=/opt/sunlite-scheduler
CONFIG_ROOT=/etc/sunlite-scheduler
STATE_ROOT=/var/lib/sunlite-scheduler
BACKUP_ROOT=/var/backups/sunlite-scheduler

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -L "$APP_ROOT/current" ]] || fail "Sunlite is not installed"
[[ -f "$SOURCE_ROOT/uv.lock" && -d "$SOURCE_ROOT/src" ]] || fail "invalid source directory"
for command in uv python3 curl systemctl systemd-analyze; do
  command -v "$command" >/dev/null || fail "$command is required"
done
python3 -c 'import lgpio' || fail "install the Raspberry Pi OS python3-lgpio package"

controller_was_active=false
web_was_active=false
systemctl is-active --quiet sunlite-controller.service && controller_was_active=true
systemctl is-active --quiet sunlite-web.service && web_was_active=true

previous=$(readlink -f "$APP_ROOT/current")

revision=$(git -C "$SOURCE_ROOT" rev-parse --short HEAD 2>/dev/null || echo source)
release="$APP_ROOT/releases/$(date -u +%Y%m%dT%H%M%SZ)-$revision"
[[ ! -e "$release" ]] || fail "release already exists; retry in one second"
install -d -m 0755 -o root -g root "$release"
cp -a "$SOURCE_ROOT/src" "$SOURCE_ROOT/docs" "$release/"
install -m 0644 "$SOURCE_ROOT/pyproject.toml" "$SOURCE_ROOT/uv.lock" \
  "$SOURCE_ROOT/README.md" "$release/"
uv venv --python "$(command -v python3)" --system-site-packages "$release/.venv"
uv sync --project "$release" --frozen --no-dev
"$release/.venv/bin/python" -c 'import lgpio; import gpiozero'
"$release/.venv/bin/sunlite-maintenance" check \
  --config "$CONFIG_ROOT/config.toml" --environment "$CONFIG_ROOT/web.env"
# shellcheck disable=SC1091
source "$CONFIG_ROOT/web.env"
health_host=${SUNLITE_WEB_HOST:-0.0.0.0}
[[ $health_host == "0.0.0.0" ]] && health_host=127.0.0.1
[[ $health_host == "::" ]] && health_host="::1"
[[ $health_host == *:* ]] && health_host="[$health_host]"
health_url="http://$health_host:${SUNLITE_WEB_PORT:-8000}/health"

safety_backup=
if [[ -f "$STATE_ROOT/sunlite.db" ]]; then
  safety_backup="$BACKUP_ROOT/pre-upgrade-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  "$release/.venv/bin/sunlite-maintenance" backup \
    --database "$STATE_ROOT/sunlite.db" \
    --config "$CONFIG_ROOT/config.toml" \
    --environment "$CONFIG_ROOT/web.env" \
    --output "$safety_backup" \
    --release "$(basename "$previous")"
fi
chown -R root:root "$release"
chmod -R a+rX,go-w "$release"

rollback_units=$(mktemp -d)
cp /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service "$rollback_units/"
cp /usr/local/sbin/sunlite-activate /usr/local/sbin/sunlite-backup \
  /usr/local/sbin/sunlite-restore /usr/local/sbin/sunlite-upgrade "$rollback_units/"
rollback() {
  trap - ERR
  temporary_link="$APP_ROOT/.current.rollback"
  rm -f "$temporary_link"
  ln -s "$previous" "$temporary_link"
  mv -Tf "$temporary_link" "$APP_ROOT/current"
  install -m 0644 "$rollback_units/sunlite-controller.service" \
    "$rollback_units/sunlite-web.service" /etc/systemd/system/
  install -m 0755 "$rollback_units/sunlite-activate" \
    "$rollback_units/sunlite-backup" "$rollback_units/sunlite-restore" \
    "$rollback_units/sunlite-upgrade" /usr/local/sbin/
  systemctl daemon-reload
  if $controller_was_active; then
    systemctl restart sunlite-controller.service || true
  else
    systemctl stop sunlite-controller.service || true
  fi
  if $web_was_active; then
    systemctl restart sunlite-web.service || true
  else
    systemctl stop sunlite-web.service || true
  fi
  rm -rf "$rollback_units"
  echo "Upgrade failed; restored $previous" >&2
}
trap rollback ERR

install -m 0644 "$SOURCE_ROOT/deploy/systemd/sunlite-controller.service" \
  "$SOURCE_ROOT/deploy/systemd/sunlite-web.service" /etc/systemd/system/
install -m 0755 "$SOURCE_ROOT/scripts/activate.sh" /usr/local/sbin/sunlite-activate
install -m 0755 "$SOURCE_ROOT/scripts/backup.sh" /usr/local/sbin/sunlite-backup
install -m 0755 "$SOURCE_ROOT/scripts/restore.sh" /usr/local/sbin/sunlite-restore
install -m 0755 "$SOURCE_ROOT/scripts/upgrade.sh" /usr/local/sbin/sunlite-upgrade
temporary_link="$APP_ROOT/.current.new"
rm -f "$temporary_link"
ln -s "$release" "$temporary_link"
mv -Tf "$temporary_link" "$APP_ROOT/current"
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service
if $controller_was_active; then
  systemctl restart sunlite-controller.service
fi
if $web_was_active; then
  systemctl restart sunlite-web.service
fi

if $controller_was_active && $web_was_active; then
  healthy=false
  for _attempt in {1..20}; do
    if curl --fail --silent --max-time 2 "$health_url" >/dev/null; then
      healthy=true
      break
    fi
    sleep 1
  done
  $healthy || false
fi

trap - ERR
rm -rf "$rollback_units"
echo "Upgraded to $release"
if [[ -n "$safety_backup" ]]; then
  echo "Pre-upgrade backup: $safety_backup"
else
  echo "No database existed, so no pre-upgrade backup was needed."
fi
if ! $controller_was_active && ! $web_was_active; then
  echo "Services remain stopped. Start them with: sudo sunlite-activate"
fi
