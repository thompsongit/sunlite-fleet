from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    CommandedState,
    CustomSchedule,
    RecoveryAction,
    RecoveryPolicy,
    RegularSchedule,
    Schedule,
    ScheduleConflict,
    Transition,
    require_aware,
)

_MAX_CYCLES = 100_000


def validate_schedule(schedule: Schedule) -> None:
    if not schedule.id or not schedule.device_id or not schedule.name.strip():
        raise ValueError("schedule id, device id, and name are required")
    require_aware(schedule.starts_at, "starts_at")
    try:
        ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {schedule.timezone}") from error

    if isinstance(schedule, RegularSchedule):
        if schedule.on_duration <= timedelta(0) or schedule.off_duration <= timedelta(0):
            raise ValueError("ON and OFF durations must be positive")
        if (schedule.repeat_count is None) == (schedule.ends_at is None):
            raise ValueError("regular schedule requires exactly one of repeat_count or ends_at")
        if schedule.repeat_count is not None and schedule.repeat_count <= 0:
            raise ValueError("repeat_count must be positive")
        if schedule.ends_at is not None:
            require_aware(schedule.ends_at, "ends_at")
            if schedule.ends_at <= schedule.starts_at:
                raise ValueError("ends_at must be after starts_at")
        return

    if len(schedule.steps) < 2:
        raise ValueError("custom schedule requires at least two transitions")
    if schedule.steps[0].offset != timedelta(0):
        raise ValueError("custom schedule must start at offset zero")
    if schedule.steps[0].state is not CommandedState.ON:
        raise ValueError("custom schedule must start ON")
    if schedule.steps[-1].state is not CommandedState.OFF:
        raise ValueError("custom schedule must end OFF")
    for previous, current in zip(schedule.steps, schedule.steps[1:], strict=False):
        if current.offset <= previous.offset:
            raise ValueError("custom offsets must be strictly increasing")
        if current.state is previous.state:
            raise ValueError("custom states must alternate")


def schedule_end(schedule: Schedule) -> datetime:
    validate_schedule(schedule)
    anchor = schedule.starts_at.astimezone(UTC)
    if isinstance(schedule, CustomSchedule):
        return anchor + schedule.steps[-1].offset
    if schedule.ends_at is not None:
        return schedule.ends_at.astimezone(UTC)
    assert schedule.repeat_count is not None
    cycle = schedule.on_duration + schedule.off_duration
    return anchor + cycle * (schedule.repeat_count - 1) + schedule.on_duration


def state_at(schedule: Schedule, at: datetime) -> CommandedState | None:
    validate_schedule(schedule)
    require_aware(at, "at")
    anchor = schedule.starts_at.astimezone(UTC)
    at = at.astimezone(UTC)
    end = schedule_end(schedule)
    if not schedule.enabled or at < anchor or at >= end:
        return None

    elapsed = at - anchor
    if isinstance(schedule, RegularSchedule):
        cycle = schedule.on_duration + schedule.off_duration
        phase = elapsed % cycle
        return CommandedState.ON if phase < schedule.on_duration else CommandedState.OFF

    current = schedule.steps[0].state
    for step in schedule.steps[1:]:
        if step.offset > elapsed:
            break
        current = step.state
    return current


def transitions_between(
    schedule: Schedule, window_start: datetime, window_end: datetime
) -> tuple[Transition, ...]:
    validate_schedule(schedule)
    require_aware(window_start, "window_start")
    require_aware(window_end, "window_end")
    window_start = window_start.astimezone(UTC)
    window_end = window_end.astimezone(UTC)
    if window_end < window_start or not schedule.enabled:
        return ()

    anchor = schedule.starts_at.astimezone(UTC)
    if isinstance(schedule, CustomSchedule):
        candidates = (
            Transition(schedule.id, schedule.device_id, anchor + step.offset, step.state)
            for step in schedule.steps
        )
        return tuple(item for item in candidates if window_start <= item.at <= window_end)

    end = schedule_end(schedule)
    cycle = schedule.on_duration + schedule.off_duration
    cycle_count = schedule.repeat_count or ceil((end - anchor) / cycle)
    if cycle_count > _MAX_CYCLES:
        raise ValueError("schedule expands beyond the supported cycle limit")

    transitions: list[Transition] = []
    for index in range(cycle_count):
        on_at = anchor + cycle * index
        if on_at >= end:
            break
        off_at = min(on_at + schedule.on_duration, end)
        if window_start <= on_at <= window_end:
            transitions.append(
                Transition(schedule.id, schedule.device_id, on_at, CommandedState.ON)
            )
        if window_start <= off_at <= window_end:
            transitions.append(
                Transition(schedule.id, schedule.device_id, off_at, CommandedState.OFF)
            )
    return tuple(transitions)


def preview(schedule: Schedule, limit: int = 512) -> tuple[Transition, ...]:
    items = transitions_between(
        schedule, schedule.starts_at.astimezone(UTC), schedule_end(schedule)
    )
    if len(items) > limit:
        raise ValueError(f"preview exceeds {limit} transitions")
    return items


def find_conflicts(schedules: tuple[Schedule, ...]) -> tuple[ScheduleConflict, ...]:
    enabled = [schedule for schedule in schedules if schedule.enabled]
    for schedule in enabled:
        validate_schedule(schedule)
    conflicts: list[ScheduleConflict] = []
    for index, first in enumerate(enabled):
        first_end = schedule_end(first)
        for second in enabled[index + 1 :]:
            if first.device_id != second.device_id:
                continue
            second_end = schedule_end(second)
            start = max(first.starts_at.astimezone(UTC), second.starts_at.astimezone(UTC))
            end = min(first_end, second_end)
            if start < end:
                conflicts.append(
                    ScheduleConflict(first.device_id, first.id, second.id, start, end)
                )
    return tuple(conflicts)


def recovery_action(schedule: Schedule, now: datetime) -> RecoveryAction:
    validate_schedule(schedule)
    require_aware(now, "now")
    now = now.astimezone(UTC)
    if now < schedule.starts_at.astimezone(UTC):
        return RecoveryAction.FUTURE
    if now >= schedule_end(schedule):
        return RecoveryAction.COMPLETE
    if schedule.recovery_policy is RecoveryPolicy.RESUME_IF_ACTIVE:
        return RecoveryAction.RESUME
    return RecoveryAction.ABORT
