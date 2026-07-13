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

## Operation

- Use **Schedules** to create a recurring cycle or custom transition timeline for one simulator.
- Generate the transition preview before saving a schedule.
- Use a device page for Stop, Pause, Resume, and time-limited Manual ON/OFF.
- **Stop All** cancels active runs and latches every configured output OFF.
- Use **Resume automation** only after confirming the fleet can safely return to scheduled operation.
