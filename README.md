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

## Access and roles

Local simulation grants administrator access. To require Cloudflare Access, set these environment variables before starting the web application:

```bash
export SUNLITE_ACCESS_REQUIRED=true
export SUNLITE_CF_TEAM_DOMAIN=https://your-team.cloudflareaccess.com
export SUNLITE_CF_AUDIENCE=your-access-application-aud-tag
export SUNLITE_SESSION_SECRET=replace-with-a-random-secret-of-at-least-32-characters
export SUNLITE_ADMIN_EMAILS=lab-admin@example.com
export SUNLITE_OPERATOR_EMAILS=scientist-a@example.com,scientist-b@example.com
export SUNLITE_ALLOWED_HOSTS=sunlite.example.com,127.0.0.1,localhost
```

Administrators and operators can create schedules and issue commands. Other identities accepted by the Cloudflare Access policy receive view-only access. Email matching is case-insensitive.

`SUNLITE_COOKIE_SECURE` defaults to `true` when Access is required. Set it to `false` only for trusted local HTTP testing.

## Raspberry Pi deployment

Use the [deployment guide](docs/deployment.md) for systemd installation, Cloudflare Tunnel, upgrades, backups, restoration, and troubleshooting.

## Operation

- Use **Schedules** to create a recurring cycle or custom transition timeline for one simulator.
- Generate the transition preview before saving a schedule.
- Use a device page for Stop, Pause, Resume, and time-limited Manual ON/OFF.
- **Stop All** cancels active runs and latches every configured output OFF.
- Use **Resume automation** only after confirming the fleet can safely return to scheduled operation.
