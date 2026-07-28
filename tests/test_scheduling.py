from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sunlite.domain import (
    CommandedState,
    CustomSchedule,
    OnDemandSchedule,
    RecoveryAction,
    RecoveryPolicy,
    RegularSchedule,
    ScheduleStep,
)
from sunlite.scheduling import (
    find_conflicts,
    phase_at,
    preview,
    preview_on_demand,
    recovery_action,
    state_at,
    validate_schedule,
)

START = datetime(2026, 7, 12, 8, tzinfo=UTC)


def regular(schedule_id: str = "r1", device_id: str = "sunlite-a") -> RegularSchedule:
    return RegularSchedule(
        schedule_id,
        device_id,
        "Stability cycle",
        START,
        "Africa/Johannesburg",
        timedelta(minutes=10),
        timedelta(minutes=5),
        repeat_count=3,
    )


def test_regular_schedule_is_anchor_based() -> None:
    schedule = regular()
    items = preview(schedule)
    assert [(item.at - START, item.state) for item in items] == [
        (timedelta(), CommandedState.ON),
        (timedelta(minutes=10), CommandedState.OFF),
        (timedelta(minutes=15), CommandedState.ON),
        (timedelta(minutes=25), CommandedState.OFF),
        (timedelta(minutes=30), CommandedState.ON),
        (timedelta(minutes=40), CommandedState.OFF),
    ]
    assert state_at(schedule, START + timedelta(minutes=12)) is CommandedState.OFF
    assert state_at(schedule, START + timedelta(minutes=31)) is CommandedState.ON
    assert state_at(schedule, START + timedelta(minutes=40)) is None

    bounded = replace(
        schedule, id="bounded", repeat_count=None, ends_at=START + timedelta(minutes=17)
    )
    assert [(item.at - START, item.state) for item in preview(bounded)][-2:] == [
        (timedelta(minutes=15), CommandedState.ON),
        (timedelta(minutes=17), CommandedState.OFF),
    ]


def test_custom_schedule_conflicts_and_recovery() -> None:
    custom = CustomSchedule(
        "custom-a",
        "sunlite-a",
        "Irregular sequence",
        START,
        "Africa/Johannesburg",
        (
            ScheduleStep(timedelta(), CommandedState.ON),
            ScheduleStep(timedelta(minutes=7), CommandedState.OFF),
            ScheduleStep(timedelta(minutes=20), CommandedState.ON),
            ScheduleStep(timedelta(minutes=30), CommandedState.OFF),
        ),
        RecoveryPolicy.RESUME_IF_ACTIVE,
    )
    assert state_at(custom, START + timedelta(minutes=10)) is CommandedState.OFF
    assert recovery_action(custom, START - timedelta(seconds=1)) is RecoveryAction.FUTURE
    assert recovery_action(custom, START + timedelta(minutes=10)) is RecoveryAction.RESUME
    assert recovery_action(custom, START + timedelta(minutes=30)) is RecoveryAction.COMPLETE

    overlapping = replace(regular("r2"), starts_at=START + timedelta(minutes=5))
    other_device = replace(overlapping, id="r3", device_id="sunlite-b")
    assert len(find_conflicts((regular(), overlapping, other_device))) == 1

    invalid = replace(custom, steps=custom.steps[:-1])
    with pytest.raises(ValueError, match="end OFF"):
        validate_schedule(invalid)


def test_handoff_and_ocp_are_distinct_and_custom_times_include_ocp() -> None:
    prepared = replace(
        regular(),
        handoff_delay=timedelta(seconds=4),
        ocp_duration=timedelta(seconds=60),
    )
    assert phase_at(prepared, START + timedelta(seconds=2)) == "handoff"
    assert phase_at(prepared, START + timedelta(seconds=10)) == "ocp"
    assert state_at(prepared, START + timedelta(seconds=64)) is CommandedState.ON

    on_demand = OnDemandSchedule(
        "ready-a",
        "sunlite-a",
        "OCP run",
        "Africa/Johannesburg",
        (
            ScheduleStep(timedelta(seconds=60), CommandedState.ON),
            ScheduleStep(timedelta(seconds=75), CommandedState.OFF),
        ),
        handoff_delay=timedelta(seconds=4),
        ocp_duration=timedelta(seconds=60),
    )
    assert [
        (item.run_time.total_seconds(), item.state) for item in preview_on_demand(on_demand)
    ] == [
        (0, CommandedState.OFF),
        (60, CommandedState.ON),
        (75, CommandedState.OFF),
    ]
