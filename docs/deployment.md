# Raspberry Pi Deployment

## Requirements

- Raspberry Pi OS with systemd, Python 3.11 or newer, and `python3-lgpio`.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), `curl`, and Git available from the root user's command path.
- The Raspberry Pi OS `gpio` group and standard GPIO device permissions.
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

Set the actual BCM pins, relay polarity, devices, web bind address, and port. The generated session secret should remain private. The default `0.0.0.0:8000` bind is reachable through the Pi's Wi-Fi, LAN, and Tailscale addresses.

Validate without starting GPIO control:

```bash
sudo /opt/sunlite-scheduler/current/.venv/bin/sunlite-maintenance check \
  --config /etc/sunlite-scheduler/config.toml \
  --environment /etc/sunlite-scheduler/web.env
sudo systemd-analyze verify \
  /etc/systemd/system/sunlite-controller.service \
  /etc/systemd/system/sunlite-web.service
```

## First activation

Confirm that all simulators may safely receive an OFF command, then start the application:

```bash
sudo sunlite-activate
curl --fail http://127.0.0.1:8000/health
```

Open `http://PI_ADDRESS:8000` from another device on the trusted network.

## Service operation

```bash
sudo systemctl status sunlite-controller sunlite-web
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

The upgrade creates a verified backup when a database exists, installs a versioned release, and restarts services that were already running. Stopped services remain stopped. A failed health check restores the previous release and unit files. Controller restart interruption behavior follows each schedule's recovery policy.

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

Backups contain the SQLite database, device configuration, web configuration, and session secret. Store them as credentials with mode `0600` or equivalent access control.

## Restore

Restoration stops the application, creates a pre-restore safety backup, validates checksums and SQLite integrity, replaces the database and configuration, and returns services to their previous active state.

```bash
sudo sunlite-restore /var/backups/sunlite-scheduler/BACKUP.tar.gz
```

Confirm controller health, device states, schedules, and network access immediately after restoration.

## Troubleshooting

Controller startup failure:

```bash
sudo journalctl -u sunlite-controller -n 100 --no-pager
id sunlite-controller
ls -l /dev/gpiomem /dev/gpiochip* 2>/dev/null
```

Web startup or access failure:

```bash
sudo journalctl -u sunlite-web -n 100 --no-pager
curl --fail http://127.0.0.1:8000/health
```

Every client that can reach the web interface can control the equipment. Use firewall rules, network segmentation, or an external authenticated proxy to limit access to trusted lab users.
