#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

SOURCE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
APP_ROOT=/opt/sunlite-scheduler
CONFIG_ROOT=/etc/sunlite-scheduler
STATE_ROOT=/var/lib/sunlite-scheduler
BACKUP_ROOT=/var/backups/sunlite-scheduler

fail() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ ! -e "$APP_ROOT/current" ]] || fail "Sunlite is already installed; use sunlite-upgrade"
for command in uv python3 curl systemctl systemd-analyze useradd groupadd usermod getent; do
  command -v "$command" >/dev/null || fail "$command is required"
done
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || \
  fail "Python 3.11 or newer is required"
python3 -c 'import lgpio' || fail "install the Raspberry Pi OS python3-lgpio package"
[[ -f "$SOURCE_ROOT/uv.lock" && -d "$SOURCE_ROOT/src" ]] || fail "invalid source directory"
getent group gpio >/dev/null || fail "the Raspberry Pi OS gpio group is required"

getent group sunlite >/dev/null || groupadd --system sunlite
if ! id sunlite-controller >/dev/null 2>&1; then
  useradd --system --gid sunlite --groups gpio --home-dir "$STATE_ROOT" \
    --no-create-home --shell /usr/sbin/nologin sunlite-controller
else
  usermod --append --groups gpio sunlite-controller
fi
if ! id sunlite-web >/dev/null 2>&1; then
  useradd --system --user-group --groups sunlite --home-dir /nonexistent \
    --no-create-home --shell /usr/sbin/nologin sunlite-web
else
  usermod --append --groups sunlite sunlite-web
fi

install -d -m 0755 -o root -g root "$APP_ROOT/releases"
install -d -m 0750 -o root -g sunlite "$CONFIG_ROOT"
install -d -m 0750 -o sunlite-controller -g sunlite "$STATE_ROOT"
install -d -m 0700 -o root -g root "$BACKUP_ROOT"

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
chown -R root:root "$release"
chmod -R go-w "$release"

temporary_link="$APP_ROOT/.current.new"
rm -f "$temporary_link"
ln -s "$release" "$temporary_link"
mv -Tf "$temporary_link" "$APP_ROOT/current"

if [[ ! -e "$CONFIG_ROOT/config.toml" ]]; then
  install -m 0640 -o root -g sunlite "$SOURCE_ROOT/deploy/config.toml.example" \
    "$CONFIG_ROOT/config.toml"
fi
if [[ ! -e "$CONFIG_ROOT/web.env" ]]; then
  secret=$(
    "$release/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))'
  )
  temporary_environment=$(mktemp)
  sed "s/__GENERATE__/$secret/" "$SOURCE_ROOT/deploy/web.env.example" \
    >"$temporary_environment"
  install -m 0640 -o root -g sunlite-web "$temporary_environment" \
    "$CONFIG_ROOT/web.env"
  rm -f "$temporary_environment"
fi

install -m 0644 "$SOURCE_ROOT/deploy/systemd/sunlite-controller.service" \
  "$SOURCE_ROOT/deploy/systemd/sunlite-web.service" /etc/systemd/system/
install -m 0755 "$SOURCE_ROOT/scripts/activate.sh" /usr/local/sbin/sunlite-activate
install -m 0755 "$SOURCE_ROOT/scripts/backup.sh" /usr/local/sbin/sunlite-backup
install -m 0755 "$SOURCE_ROOT/scripts/restore.sh" /usr/local/sbin/sunlite-restore
install -m 0755 "$SOURCE_ROOT/scripts/upgrade.sh" /usr/local/sbin/sunlite-upgrade

systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service

echo "Installed $release"
echo "Edit $CONFIG_ROOT/config.toml and $CONFIG_ROOT/web.env, then run:"
echo "  sudo sunlite-activate"
