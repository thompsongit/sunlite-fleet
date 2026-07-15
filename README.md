# Sunlite Scheduler

Sunlite Scheduler controls a configurable fleet of ABET Sunlite Model 11002 solar simulators from one Raspberry Pi.

Each simulator has an independent schedule, commanded state, history, and relay-channel mapping. Use **Stop All** to command every configured simulator OFF, or select a device such as **Sunlite 11002 - A** for individual controls.

Schedules support:

- regular ON/OFF cycles with a repeat count or end time;
- one-off custom transition timelines;
- independent concurrent operation on different simulators;
- interruption recovery configured per schedule.

The interface reports commanded relay state. It does not confirm the simulator's electrical or optical output unless hardware feedback is added.

## Local simulation

```bash
cp config.example.toml config.toml
uv sync --group dev
uv run sunlite-controller --config config.toml --mock
```

In a second terminal:

```bash
uv run sunlite-web --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Network access

The Raspberry Pi service listens on all configured network interfaces by default. Open it with the Pi's Wi-Fi, LAN, or Tailscale address:

```bash
http://PI_ADDRESS:8000
```

Every client that can reach the interface can operate the controller. Restrict network access to trusted lab users. External tunnels, reverse proxies, and their authentication policies are configured separately from Sunlite Scheduler.

Set `SUNLITE_WEB_HOST` or `SUNLITE_WEB_PORT` in `/etc/sunlite-scheduler/web.env` to use a different interface or port.

## Raspberry Pi deployment

Use the [deployment guide](docs/deployment.md) for systemd installation, network access, upgrades, backups, restoration, and troubleshooting.

## Operation

- Use **Schedules** to create a recurring cycle or custom transition timeline for one simulator.
- Generate the transition preview before saving a schedule.
- Use a device page for Stop, Pause, Resume, and time-limited Manual ON/OFF.
- **Stop All** cancels active runs and latches every configured output OFF.
- Use **Resume automation** only after confirming the fleet can safely return to scheduled operation.
