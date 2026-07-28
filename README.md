# Sunlite Scheduler

Sunlite Scheduler controls a configurable fleet of ABET Sunlite Model 11002 solar simulators from one Raspberry Pi.

Each simulator has an independent schedule, commanded state, history, and relay-channel mapping. Use **Stop All** to command every configured simulator OFF, or select a device such as **Sunlite 11002 - A** for individual controls.

Schedules support:

- saved on-demand timelines launched when the experiment is ready;
- future regular ON/OFF cycles with a repeat count or end time;
- future custom transition timelines;
- independent concurrent operation on different simulators;
- interruption recovery configured per schedule.

Schedule timing is entered in seconds and accepts whole or fractional values. Every
schedule has its own **Handoff delay** and **OCP period**:

- Handoff delay occurs before the measured run and keeps the simulator OFF.
- Run time `0` is the beginning of OCP, with the simulator OFF.
- The first ON transition is placed at a run time equal to the OCP period.
- Custom run times include OCP; OCP is not added to them a second time.

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

- Use **Schedules** to create an **On-Demand Run**, **Scheduled Regular Cycle**, or **Scheduled Custom Timeline** for one simulator.
- On-demand is the default. Save its relative timeline, select **Launch run**, verify the independent Handoff and OCP values, then select **Begin launch countdown**.
- Generate the transition preview before saving a schedule.
- Follow the separately labelled Handoff and OCP countdowns on the device page. Run elapsed time begins when OCP starts.
- Future schedules are entered as South African wall time and displayed explicitly in SAST. On-demand previews use relative run time only.
- Use a device page for **Stop run**, **Pause run**, **Resume run**, and time-limited Manual ON/OFF.
- **Stop All** cancels active runs and latches every configured output OFF.
- Use **Resume automation** only after confirming the fleet can safely return to scheduled operation.
