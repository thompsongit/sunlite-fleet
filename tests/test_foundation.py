from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sunlite.clock import FakeClock
from sunlite.config import config_from_dict, load_config
from sunlite.domain import (
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
    ScheduleStep,
)
from sunlite.relay import RecordingRelay
from sunlite.storage import Repository

NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)


def test_config_clock_and_recording_relay() -> None:
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    assert [device.name for device in config.devices] == [
        "Sunlite 11002 - A",
        "Sunlite 11002 - B",
        "Sunlite 11002 - C",
    ]
    with pytest.raises(ValueError, match="duplicate assigned relay channel"):
        config_from_dict(
            {
                "channels": [{"id": "r1", "bcm_pin": 17}],
                "devices": [
                    {"id": "a", "name": "A", "profile": "maintained_output", "channels": ["r1"]},
                    {"id": "b", "name": "B", "profile": "maintained_output", "channels": ["r1"]},
                ],
            }
        )

    clock = FakeClock(NOW)
    relay = RecordingRelay(("sunlite-a", "sunlite-b"))
    clock.advance(timedelta(seconds=5))
    relay.set_state("sunlite-a", CommandedState.ON, clock.now())
    clock.set_wall_time(NOW - timedelta(hours=1))
    assert clock.monotonic() == 5
    assert relay.state("sunlite-a") is CommandedState.ON
    assert relay.state("sunlite-b") is CommandedState.OFF
    assert len(relay.events) == 1


def test_repository_round_trip(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    repository = Repository(tmp_path / "sunlite.db")
    repository.migrate()
    repository.save_registry(config)

    schedule = RegularSchedule(
        "regular-a",
        "sunlite-a",
        "Cycle A",
        NOW,
        "Africa/Johannesburg",
        timedelta(minutes=10),
        timedelta(minutes=5),
        repeat_count=3,
        recovery_policy=RecoveryPolicy.RESUME_IF_ACTIVE,
    )
    repository.save_schedule(schedule)
    custom = CustomSchedule(
        "custom-b",
        "sunlite-b",
        "Custom B",
        NOW,
        "Africa/Johannesburg",
        (
            ScheduleStep(timedelta(), CommandedState.ON),
            ScheduleStep(timedelta(minutes=2), CommandedState.OFF),
        ),
    )
    repository.save_schedule(custom)
    assert repository.get_schedule(schedule.id) == schedule
    assert repository.get_schedule(custom.id) == custom
    assert len(repository.list_schedules()) == 2
    assert repository.device_names()["sunlite-c"] == "Sunlite 11002 - C"

    runtime = DeviceRuntime(
        "sunlite-a",
        DeviceMode.MANUAL_ON,
        CommandedState.ON,
        manual_override=ManualOverride(CommandedState.ON, NOW + timedelta(minutes=5), "test"),
        updated_at=NOW,
    )
    repository.save_runtime(runtime)
    repository.save_global_stop(True, NOW)
    repository.record_command(CommandRecord("cmd-1", "stop_all", NOW, "operator@example.com"))
    repository.record_audit(AuditEvent(NOW, "operator@example.com", "stop_all"))
    repository.save_run(RunRecord("run-1", schedule.id, "sunlite-a", NOW))

    assert repository.load_runtime("sunlite-a") == runtime
    assert repository.load_global_stop() is True
    assert repository.record_counts() == {"commands": 1, "audit_events": 1, "runs": 1}
