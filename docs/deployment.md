# Raspberry Pi Deployment

## Requirements

- Raspberry Pi OS with systemd, Python 3.11 or newer, and `python3-lgpio`.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), `curl`, and Git available from the root user's command path.
- The Raspberry Pi OS `gpio` group and standard GPIO device permissions.
- A Cloudflare-managed domain, Cloudflare Access application, named Tunnel, and the current [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/).
- Completed relay wiring and a verified GPIO pin, polarity, and relay-profile configuration.

Keep the simulator disconnected from relay control until the configuration has been reviewed. Activating the controller initializes every configured output OFF and begins schedule recovery.

## Install

From a checked-out release on the Raspberry Pi:

```bash
sudo apt update
sudo apt install python3 python3-lgpio curl git
```

Install `uv` using its official installation instructions and ensure `sudo uv --version` succeeds. Then run:

```bash
sudo ./scripts/install.sh
```

The installer does not start the services. Use the upgrade command when Sunlite is already installed.

Review the production configuration:

```bash
sudoedit /etc/sunlite-scheduler/config.toml
sudoedit /etc/sunlite-scheduler/web.env
```

Set the actual BCM pins, relay polarity, devices, Cloudflare team domain, Access application audience tag, public hostname, and operator email addresses. The generated session secret should remain private.

Validate without starting GPIO control:

```bash
sudo /opt/sunlite-scheduler/current/.venv/bin/sunlite-maintenance check \
  --config /etc/sunlite-scheduler/config.toml \
  --environment /etc/sunlite-scheduler/web.env
sudo systemd-analyze verify \
  /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service
```

## Cloudflare Access and Tunnel

Create a Cloudflare Access self-hosted application for the public hostname before starting the Tunnel. Its application audience tag is the value of `SUNLITE_CF_AUDIENCE`. Access policies decide who may reach the application; the email lists in `web.env` decide who can operate it. Authenticated users not listed as administrators or operators are view-only.

Create a locally managed named Tunnel and DNS route:

```bash
cloudflared tunnel login
cloudflared tunnel create sunlite-scheduler
cloudflared tunnel route dns sunlite-scheduler sunlite.example.com
```

Copy the generated Tunnel credentials to `/etc/cloudflared`, then install the example configuration:

```bash
sudo install -d -m 0755 /etc/cloudflared
sudo install -m 0600 ~/.cloudflared/TUNNEL-UUID.json \
  /etc/cloudflared/TUNNEL-UUID.json
sudo install -m 0644 deploy/cloudflared/config.yml.example \
  /etc/cloudflared/config.yml
sudoedit /etc/cloudflared/config.yml
sudo cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
```

Replace every placeholder and keep the final catch-all `http_status:404` rule.

## First activation

Confirm that all simulators may safely receive an OFF command, then start the application:

```bash
sudo sunlite-activate
curl --fail http://127.0.0.1:8000/health
```

Install and start the Tunnel service only after the local health check passes and Cloudflare Access is active:

```bash
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl start cloudflared
```

Open the public hostname and verify viewer and operator accounts separately.

## Service operation

```bash
sudo systemctl status sunlite-controller sunlite-web cloudflared
sudo journalctl -u sunlite-controller -u sunlite-web --since today
sudo systemd-analyze security sunlite-controller.service sunlite-web.service
```

Restarting or stopping the controller commands initialized GPIO outputs OFF. Check active experiments before service maintenance.

```bash
sudo systemctl restart sunlite-controller sunlite-web
sudo systemctl stop sunlite-web sunlite-controller
```

## Upgrade and rollback

Check out the desired release, enter its repository directory, then run:

```bash
sudo ./scripts/upgrade.sh
```

The upgrade creates a verified backup, installs a versioned release, restarts both services, and checks local health. A failed health check restores the previous release and unit files. Controller restart interruption behavior follows each schedule's recovery policy.

## Backup

Create a timestamped root-only archive:

```bash
sudo sunlite-backup
```

Specify a destination when copying directly to protected external storage:

```bash
sudo sunlite-backup /media/lab-backup/sunlite-$(date -u +%Y%m%d).tar.gz
```

Verify an archive without restoring it:

```bash
sudo /opt/sunlite-scheduler/current/.venv/bin/sunlite-maintenance verify \
  /var/backups/sunlite-scheduler/BACKUP.tar.gz
```

Backups contain the SQLite database, device configuration, Cloudflare role configuration, and session secret. Store them as credentials with mode `0600` or equivalent access control.

## Restore

Restoration stops the application, creates a pre-restore safety backup, validates checksums and SQLite integrity, replaces the database and configuration, and returns services to their previous active state.

```bash
sudo sunlite-restore /var/backups/sunlite-scheduler/BACKUP.tar.gz
```

Confirm controller health, device states, schedules, and Cloudflare access immediately after restoration.

## Troubleshooting

Controller startup failure:

```bash
sudo journalctl -u sunlite-controller -n 100 --no-pager
id sunlite-controller
ls -l /dev/gpiomem /dev/gpiochip* 2>/dev/null
```

Web startup or authentication failure:

```bash
sudo journalctl -u sunlite-web -n 100 --no-pager
curl --fail http://127.0.0.1:8000/health
```

Tunnel routing failure:

```bash
sudo systemctl status cloudflared
sudo cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
sudo cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule \
  https://sunlite.example.com
```

Never bypass Cloudflare Access by binding the web service to a public interface.
