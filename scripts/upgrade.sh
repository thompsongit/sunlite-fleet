#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

SOURCE_ROOT=${1:-$PWD}
SOURCE_ROOT=$(cd "$SOURCE_ROOT" && pwd)
APP_ROOT=/opt/sunlite-scheduler
CONFIG_ROOT=/etc/sunlite-scheduler

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -L "$APP_ROOT/current" ]] || fail "Sunlite is not installed"
[[ -f "$SOURCE_ROOT/uv.lock" && -d "$SOURCE_ROOT/src" ]] || fail "invalid source directory"
for command in uv python3 curl systemctl systemd-analyze; do
  command -v "$command" >/dev/null || fail "$command is required"
done
python3 -c 'import lgpio' || fail "install the Raspberry Pi OS python3-lgpio package"
systemctl is-active --quiet sunlite-controller.service || fail "controller is not active"
systemctl is-active --quiet sunlite-web.service || fail "web service is not active"

previous=$(readlink -f "$APP_ROOT/current")
"$previous/.venv/bin/sunlite-maintenance" check \
  --config "$CONFIG_ROOT/config.toml" --environment "$CONFIG_ROOT/web.env"
safety_backup=$(/usr/local/sbin/sunlite-backup)

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
chown -R root:root "$release"
chmod -R go-w "$release"

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
  systemctl restart sunlite-controller.service sunlite-web.service || true
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
systemctl restart sunlite-controller.service sunlite-web.service

healthy=false
for _attempt in {1..20}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null; then
    healthy=true
    break
  fi
  sleep 1
done
$healthy || false

trap - ERR
rm -rf "$rollback_units"
echo "Upgraded to $release"
echo "Pre-upgrade backup: $safety_backup"
