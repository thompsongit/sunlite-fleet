from datetime import UTC, datetime, timedelta

import pytest

from sunlite.control import FleetArbiter
from sunlite.domain import CommandedState, DeviceMode, RegularSchedule

NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)


def cycle(schedule_id: str, device_id: str, starts_at: datetime = NOW) -> RegularSchedule:
    return RegularSchedule(
        schedule_id,
        device_id,
        schedule_id,
        starts_at,
        "Africa/Johannesburg",
        timedelta(minutes=10),
        timedelta(minutes=5),
        repeat_count=2,
    )


def test_device_isolation_overrides_and_stop_all() -> None:
    arbiter = FleetArbiter(("sunlite-a", "sunlite-b"), manual_on_max_seconds=600)
    schedules = (cycle("a-cycle", "sunlite-a"), cycle("b-cycle", "sunlite-b"))

    states = arbiter.evaluate(NOW, schedules)
    assert states["sunlite-a"].commanded_state is CommandedState.ON
    assert states["sunlite-b"].commanded_state is CommandedState.ON

    arbiter.set_manual_override(
        "sunlite-a", CommandedState.OFF, timedelta(minutes=3), "sample change", NOW
    )
    states = arbiter.evaluate(NOW + timedelta(minutes=1), schedules)
    assert states["sunlite-a"].mode is DeviceMode.MANUAL_OFF
    assert states["sunlite-b"].mode is DeviceMode.AUTOMATIC_ON

    arbiter.stop_all(NOW + timedelta(minutes=2))
    assert all(state.commanded_state is CommandedState.OFF for state in arbiter.devices.values())
    assert arbiter.devices["sunlite-a"].manual_override is None
    arbiter.resume_all()
    states = arbiter.evaluate(NOW + timedelta(minutes=4), schedules)
    assert all(state.commanded_state is CommandedState.ON for state in states.values())


def test_expiry_limits_and_runtime_conflict_fail_off() -> None:
    arbiter = FleetArbiter(("sunlite-a",), manual_on_max_seconds=300)
    with pytest.raises(ValueError, match="maximum"):
        arbiter.set_manual_override(
            "sunlite-a", CommandedState.ON, timedelta(minutes=6), "too long", NOW
        )
    arbiter.set_manual_override(
        "sunlite-a", CommandedState.ON, timedelta(minutes=5), "calibration", NOW
    )
    assert arbiter.evaluate(NOW + timedelta(minutes=6), ())["sunlite-a"].mode is DeviceMode.IDLE

    states = arbiter.evaluate(
        NOW + timedelta(minutes=7),
        (cycle("one", "sunlite-a"), cycle("two", "sunlite-a")),
    )
    assert states["sunlite-a"].mode is DeviceMode.FAULT
    assert states["sunlite-a"].commanded_state is CommandedState.OFF
