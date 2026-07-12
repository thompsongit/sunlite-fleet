from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import AppConfig
from .domain import (
    AuditEvent,
    CommandedState,
    CommandRecord,
    CustomSchedule,
    DeviceMode,
    DeviceRuntime,
    ManualOverride,
    RecoveryPolicy,
    RegularSchedule,
    RunRecord,
    Schedule,
    ScheduleKind,
    ScheduleStep,
    require_aware,
)

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE relay_channels (
            id TEXT PRIMARY KEY,
            bcm_pin INTEGER NOT NULL UNIQUE,
            active_high INTEGER NOT NULL,
            initial_state TEXT NOT NULL CHECK (initial_state = 'off'),
            enabled INTEGER NOT NULL
        );
        CREATE TABLE devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            profile TEXT NOT NULL,
            enabled INTEGER NOT NULL
        );
        CREATE TABLE device_channels (
            device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            channel_id TEXT NOT NULL UNIQUE REFERENCES relay_channels(id),
            position INTEGER NOT NULL,
            PRIMARY KEY (device_id, position)
        );
        CREATE TABLE schedules (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(id),
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            starts_at_utc TEXT NOT NULL,
            timezone TEXT NOT NULL,
            recovery_policy TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            on_seconds REAL,
            off_seconds REAL,
            repeat_count INTEGER,
            ends_at_utc TEXT
        );
        CREATE INDEX schedules_device_start ON schedules(device_id, starts_at_utc);
        CREATE TABLE schedule_steps (
            schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            offset_seconds REAL NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (schedule_id, position)
        );
        CREATE TABLE device_runtime (
            device_id TEXT PRIMARY KEY REFERENCES devices(id),
            mode TEXT NOT NULL,
            commanded_state TEXT NOT NULL,
            stop_latched INTEGER NOT NULL,
            paused INTEGER NOT NULL,
            fault TEXT,
            manual_state TEXT,
            manual_expires_at_utc TEXT,
            manual_reason TEXT,
            active_schedule_id TEXT,
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE fleet_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            global_stop_latched INTEGER NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE commands (
            id TEXT PRIMARY KEY,
            device_id TEXT REFERENCES devices(id),
            action TEXT NOT NULL,
            requested_at_utc TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            reason TEXT NOT NULL
        );
        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at_utc TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            device_id TEXT REFERENCES devices(id),
            details TEXT NOT NULL
        );
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL REFERENCES schedules(id),
            device_id TEXT NOT NULL REFERENCES devices(id),
            planned_start_utc TEXT NOT NULL,
            actual_start_utc TEXT,
            actual_end_utc TEXT,
            outcome TEXT NOT NULL
        );
        """,
    ),
)


class Repository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in _MIGRATIONS:
                if version in applied:
                    continue
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                    (version, _dump_datetime(datetime.now(UTC))),
                )

    def save_registry(self, config: AppConfig) -> None:
        with self.connect() as connection:
            for channel in config.channels:
                connection.execute(
                    """INSERT INTO relay_channels
                       (id, bcm_pin, active_high, initial_state, enabled)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET bcm_pin=excluded.bcm_pin,
                       active_high=excluded.active_high, initial_state=excluded.initial_state,
                       enabled=excluded.enabled""",
                    (
                        channel.id,
                        channel.bcm_pin,
                        channel.active_high,
                        channel.initial_state.value,
                        channel.enabled,
                    ),
                )
            for device in config.devices:
                connection.execute(
                    """INSERT INTO devices(id, name, profile, enabled) VALUES (?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                       profile=excluded.profile, enabled=excluded.enabled""",
                    (device.id, device.name, device.profile.value, device.enabled),
                )
                connection.execute("DELETE FROM device_channels WHERE device_id = ?", (device.id,))
                connection.executemany(
                    "INSERT INTO device_channels(device_id, channel_id, position) VALUES (?, ?, ?)",
                    (
                        (device.id, channel_id, position)
                        for position, channel_id in enumerate(device.channel_ids)
                    ),
                )

    def device_names(self) -> dict[str, str]:
        with self.connect() as connection:
            return {
                str(row["id"]): str(row["name"])
                for row in connection.execute("SELECT id, name FROM devices ORDER BY id")
            }

    def save_schedule(self, schedule: Schedule) -> None:
        from .scheduling import validate_schedule

        validate_schedule(schedule)
        regular = schedule if isinstance(schedule, RegularSchedule) else None
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO schedules
                   (id, device_id, name, kind, starts_at_utc, timezone, recovery_policy,
                    enabled, on_seconds, off_seconds, repeat_count, ends_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET device_id=excluded.device_id, name=excluded.name,
                   kind=excluded.kind, starts_at_utc=excluded.starts_at_utc,
                   timezone=excluded.timezone, recovery_policy=excluded.recovery_policy,
                   enabled=excluded.enabled, on_seconds=excluded.on_seconds,
                   off_seconds=excluded.off_seconds, repeat_count=excluded.repeat_count,
                   ends_at_utc=excluded.ends_at_utc""",
                (
                    schedule.id,
                    schedule.device_id,
                    schedule.name,
                    schedule.kind.value,
                    _dump_datetime(schedule.starts_at),
                    schedule.timezone,
                    schedule.recovery_policy.value,
                    schedule.enabled,
                    regular.on_duration.total_seconds() if regular else None,
                    regular.off_duration.total_seconds() if regular else None,
                    regular.repeat_count if regular else None,
                    _dump_datetime(regular.ends_at) if regular and regular.ends_at else None,
                ),
            )
            connection.execute("DELETE FROM schedule_steps WHERE schedule_id = ?", (schedule.id,))
            if isinstance(schedule, CustomSchedule):
                connection.executemany(
                    """INSERT INTO schedule_steps
                       (schedule_id, position, offset_seconds, state) VALUES (?, ?, ?, ?)""",
                    (
                        (schedule.id, position, step.offset.total_seconds(), step.state.value)
                        for position, step in enumerate(schedule.steps)
                    ),
                )

    def get_schedule(self, schedule_id: str) -> Schedule | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            return self._schedule_from_row(connection, row) if row else None

    def list_schedules(self) -> tuple[Schedule, ...]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM schedules ORDER BY starts_at_utc, id")
            return tuple(self._schedule_from_row(connection, row) for row in rows)

    def save_runtime(self, runtime: DeviceRuntime) -> None:
        manual = runtime.manual_override
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO device_runtime
                   (device_id, mode, commanded_state, stop_latched, paused, fault,
                    manual_state, manual_expires_at_utc, manual_reason,
                    active_schedule_id, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET mode=excluded.mode,
                   commanded_state=excluded.commanded_state,
                   stop_latched=excluded.stop_latched, paused=excluded.paused,
                   fault=excluded.fault, manual_state=excluded.manual_state,
                   manual_expires_at_utc=excluded.manual_expires_at_utc,
                   manual_reason=excluded.manual_reason,
                   active_schedule_id=excluded.active_schedule_id,
                   updated_at_utc=excluded.updated_at_utc""",
                (
                    runtime.device_id,
                    runtime.mode.value,
                    runtime.commanded_state.value,
                    runtime.stop_latched,
                    runtime.paused,
                    runtime.fault,
                    manual.state.value if manual else None,
                    _dump_datetime(manual.expires_at) if manual else None,
                    manual.reason if manual else None,
                    runtime.active_schedule_id,
                    _dump_datetime(runtime.updated_at),
                ),
            )

    def load_runtime(self, device_id: str) -> DeviceRuntime | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM device_runtime WHERE device_id = ?", (device_id,)
            ).fetchone()
        if row is None:
            return None
        manual = None
        if row["manual_state"]:
            manual = ManualOverride(
                CommandedState(row["manual_state"]),
                _load_datetime(row["manual_expires_at_utc"]),
                str(row["manual_reason"]),
            )
        return DeviceRuntime(
            device_id=str(row["device_id"]),
            mode=DeviceMode(row["mode"]),
            commanded_state=CommandedState(row["commanded_state"]),
            stop_latched=bool(row["stop_latched"]),
            paused=bool(row["paused"]),
            fault=str(row["fault"]) if row["fault"] else None,
            manual_override=manual,
            active_schedule_id=(
                str(row["active_schedule_id"]) if row["active_schedule_id"] else None
            ),
            updated_at=_load_datetime(row["updated_at_utc"]),
        )

    def save_global_stop(self, latched: bool, at: datetime) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO fleet_state(singleton, global_stop_latched, updated_at_utc)
                   VALUES (1, ?, ?) ON CONFLICT(singleton) DO UPDATE SET
                   global_stop_latched=excluded.global_stop_latched,
                   updated_at_utc=excluded.updated_at_utc""",
                (latched, _dump_datetime(at)),
            )

    def load_global_stop(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT global_stop_latched FROM fleet_state WHERE singleton = 1"
            ).fetchone()
            return bool(row[0]) if row else False

    def record_command(self, command: CommandRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO commands
                   (id, device_id, action, requested_at_utc, requested_by, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    command.id,
                    command.device_id,
                    command.action,
                    _dump_datetime(command.requested_at),
                    command.requested_by,
                    command.reason,
                ),
            )

    def record_audit(self, event: AuditEvent) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO audit_events(at_utc, actor, action, device_id, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    _dump_datetime(event.at),
                    event.actor,
                    event.action,
                    event.device_id,
                    event.details,
                ),
            )

    def save_run(self, run: RunRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO runs
                   (id, schedule_id, device_id, planned_start_utc,
                    actual_start_utc, actual_end_utc, outcome)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET actual_start_utc=excluded.actual_start_utc,
                   actual_end_utc=excluded.actual_end_utc, outcome=excluded.outcome""",
                (
                    run.id,
                    run.schedule_id,
                    run.device_id,
                    _dump_datetime(run.planned_start),
                    _dump_datetime(run.actual_start) if run.actual_start else None,
                    _dump_datetime(run.actual_end) if run.actual_end else None,
                    run.outcome,
                ),
            )

    def record_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("commands", "audit_events", "runs")
            }

    @staticmethod
    def _schedule_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> Schedule:
        schedule_id = str(row["id"])
        device_id = str(row["device_id"])
        name = str(row["name"])
        starts_at = _load_datetime(row["starts_at_utc"])
        timezone = str(row["timezone"])
        recovery_policy = RecoveryPolicy(row["recovery_policy"])
        enabled = bool(row["enabled"])
        if ScheduleKind(row["kind"]) is ScheduleKind.REGULAR:
            return RegularSchedule(
                id=schedule_id,
                device_id=device_id,
                name=name,
                starts_at=starts_at,
                timezone=timezone,
                on_duration=timedelta(seconds=float(row["on_seconds"])),
                off_duration=timedelta(seconds=float(row["off_seconds"])),
                repeat_count=(int(row["repeat_count"]) if row["repeat_count"] else None),
                ends_at=(_load_datetime(row["ends_at_utc"]) if row["ends_at_utc"] else None),
                recovery_policy=recovery_policy,
                enabled=enabled,
            )
        steps = tuple(
            ScheduleStep(
                timedelta(seconds=float(step["offset_seconds"])),
                CommandedState(step["state"]),
            )
            for step in connection.execute(
                "SELECT * FROM schedule_steps WHERE schedule_id = ? ORDER BY position",
                (row["id"],),
            )
        )
        return CustomSchedule(
            id=schedule_id,
            device_id=device_id,
            name=name,
            starts_at=starts_at,
            timezone=timezone,
            steps=steps,
            recovery_policy=recovery_policy,
            enabled=enabled,
        )


def _dump_datetime(value: datetime) -> str:
    require_aware(value)
    return value.astimezone(UTC).isoformat()


def _load_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return require_aware(parsed)
