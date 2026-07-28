from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil, isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    CommandedState,
    CustomSchedule,
    OnDemandSchedule,
    RecoveryAction,
    RecoveryPolicy,
    RegularSchedule,
    RelativeTransition,
    Schedule,
    ScheduleConflict,
    ScheduleDefinition,
    ScheduleStep,
    Transition,
    require_aware,
)

_MAX_CYCLES = 100_000


def validate_schedule(schedule: Schedule) -> None:
    _validate_common(schedule)
    require_aware(schedule.starts_at, "starts_at")
    _validate_pattern(schedule)
    if isinstance(schedule, RegularSchedule) and schedule.ends_at is not None:
        require_aware(schedule.ends_at, "ends_at")
        if schedule.ends_at <= pattern_start(schedule):
            raise ValueError("ends_at must be after the OCP period")


def validate_on_demand(schedule: OnDemandSchedule) -> None:
    _validate_common(schedule)
    _validate_custom_steps(schedule.steps, schedule.ocp_duration)


def validate_definition(schedule: ScheduleDefinition) -> None:
    if isinstance(schedule, OnDemandSchedule):
        validate_on_demand(schedule)
    else:
        validate_schedule(schedule)


def reservation_start(schedule: Schedule) -> datetime:
    return schedule.starts_at.astimezone(UTC)


def run_start(schedule: Schedule) -> datetime:
    return reservation_start(schedule) + schedule.handoff_delay


def pattern_start(schedule: Schedule) -> datetime:
    return run_start(schedule) + schedule.ocp_duration


def schedule_end(schedule: Schedule) -> datetime:
    validate_schedule(schedule)
    if isinstance(schedule, CustomSchedule):
        return run_start(schedule) + schedule.steps[-1].offset
    if schedule.ends_at is not None:
        return schedule.ends_at.astimezone(UTC)
    assert schedule.repeat_count is not None
    cycle = schedule.on_duration + schedule.off_duration
    return pattern_start(schedule) + cycle * (schedule.repeat_count - 1) + schedule.on_duration


def phase_at(schedule: Schedule, at: datetime) -> str | None:
    validate_schedule(schedule)
    require_aware(at, "at")
    at = at.astimezone(UTC)
    if not schedule.enabled or at < reservation_start(schedule) or at >= schedule_end(schedule):
        return None
    if at < run_start(schedule):
        return "handoff"
    if at < pattern_start(schedule):
        return "ocp"
    return "running"


def phase_deadline(schedule: Schedule, at: datetime) -> datetime | None:
    phase = phase_at(schedule, at)
    if phase == "handoff":
        return run_start(schedule)
    if phase == "ocp":
        return pattern_start(schedule)
    if phase == "running":
        future = [
            item.at
            for item in transitions_between(schedule, at, schedule_end(schedule))
            if item.at > at
        ]
        return min(future) if future else schedule_end(schedule)
    return None


def state_at(schedule: Schedule, at: datetime) -> CommandedState | None:
    phase = phase_at(schedule, at)
    if phase is None:
        return None
    if phase in {"handoff", "ocp"}:
        return CommandedState.OFF

    at = at.astimezone(UTC)
    if isinstance(schedule, RegularSchedule):
        elapsed = at - pattern_start(schedule)
        cycle = schedule.on_duration + schedule.off_duration
        return CommandedState.ON if elapsed % cycle < schedule.on_duration else CommandedState.OFF

    elapsed = at - run_start(schedule)
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

    candidates: list[Transition] = []
    reserved_at = reservation_start(schedule)
    run_at = run_start(schedule)
    pattern_at = pattern_start(schedule)
    end = schedule_end(schedule)

    if schedule.handoff_delay > timedelta():
        candidates.append(
            Transition(schedule.id, schedule.device_id, reserved_at, CommandedState.OFF, "handoff")
        )
    if schedule.ocp_duration > timedelta():
        candidates.append(
            Transition(schedule.id, schedule.device_id, run_at, CommandedState.OFF, "ocp")
        )

    if isinstance(schedule, CustomSchedule):
        candidates.extend(
            Transition(
                schedule.id,
                schedule.device_id,
                run_at + step.offset,
                step.state,
                "pattern",
            )
            for step in schedule.steps
        )
    else:
        cycle = schedule.on_duration + schedule.off_duration
        cycle_count = schedule.repeat_count or ceil((end - pattern_at) / cycle)
        if cycle_count > _MAX_CYCLES:
            raise ValueError("schedule expands beyond the supported cycle limit")
        for index in range(cycle_count):
            on_at = pattern_at + cycle * index
            if on_at >= end:
                break
            off_at = min(on_at + schedule.on_duration, end)
            candidates.append(
                Transition(schedule.id, schedule.device_id, on_at, CommandedState.ON, "pattern")
            )
            candidates.append(
                Transition(schedule.id, schedule.device_id, off_at, CommandedState.OFF, "pattern")
            )

    unique = {
        (item.at, item.state, item.phase): item
        for item in candidates
        if reserved_at <= item.at <= end
    }
    return tuple(
        item
        for item in sorted(unique.values(), key=lambda transition: transition.at)
        if window_start <= item.at <= window_end
    )


def preview(schedule: Schedule, limit: int = 512) -> tuple[Transition, ...]:
    items = transitions_between(schedule, reservation_start(schedule), schedule_end(schedule))
    if len(items) > limit:
        raise ValueError(f"preview exceeds {limit} transitions")
    return items


def preview_on_demand(
    schedule: OnDemandSchedule, limit: int = 512
) -> tuple[RelativeTransition, ...]:
    validate_on_demand(schedule)
    items: list[RelativeTransition] = []
    if schedule.ocp_duration > timedelta():
        items.append(
            RelativeTransition(
                schedule.id,
                schedule.device_id,
                timedelta(),
                CommandedState.OFF,
                "ocp",
            )
        )
    items.extend(
        RelativeTransition(
            schedule.id,
            schedule.device_id,
            step.offset,
            step.state,
            "pattern",
        )
        for step in schedule.steps
    )
    if len(items) > limit:
        raise ValueError(f"preview exceeds {limit} transitions")
    return tuple(items)


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
            start = max(reservation_start(first), reservation_start(second))
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
    if now <= reservation_start(schedule):
        return RecoveryAction.FUTURE
    if now >= schedule_end(schedule):
        return RecoveryAction.COMPLETE
    if schedule.recovery_policy is RecoveryPolicy.RESUME_IF_ACTIVE:
        return RecoveryAction.RESUME
    return RecoveryAction.ABORT


def shifted_on_demand_steps(
    schedule: OnDemandSchedule, ocp_duration: timedelta
) -> tuple[ScheduleStep, ...]:
    _validate_duration(ocp_duration, "OCP period")
    delta = ocp_duration - schedule.ocp_duration
    shifted = tuple(ScheduleStep(step.offset + delta, step.state) for step in schedule.steps)
    _validate_custom_steps(shifted, ocp_duration)
    return shifted


def _validate_common(schedule: ScheduleDefinition) -> None:
    if not schedule.id or not schedule.device_id or not schedule.name.strip():
        raise ValueError("schedule id, device id, and name are required")
    try:
        ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {schedule.timezone}") from error
    _validate_duration(schedule.handoff_delay, "handoff delay")
    _validate_duration(schedule.ocp_duration, "OCP period")


def _validate_pattern(schedule: Schedule) -> None:
    if isinstance(schedule, RegularSchedule):
        if schedule.on_duration <= timedelta() or schedule.off_duration <= timedelta():
            raise ValueError("ON and OFF durations must be positive")
        if not isfinite(schedule.on_duration.total_seconds()) or not isfinite(
            schedule.off_duration.total_seconds()
        ):
            raise ValueError("ON and OFF durations must be finite")
        if (schedule.repeat_count is None) == (schedule.ends_at is None):
            raise ValueError("regular schedule requires exactly one of repeat_count or ends_at")
        if schedule.repeat_count is not None and schedule.repeat_count <= 0:
            raise ValueError("repeat_count must be positive")
        return
    _validate_custom_steps(schedule.steps, schedule.ocp_duration)


def _validate_custom_steps(steps: tuple[ScheduleStep, ...], ocp_duration: timedelta) -> None:
    if len(steps) < 2:
        raise ValueError("custom schedule requires at least two transitions")
    if steps[0].offset != ocp_duration:
        raise ValueError("first ON run time must equal the OCP period")
    if steps[0].state is not CommandedState.ON:
        raise ValueError("custom schedule must begin with first light ON")
    if steps[-1].state is not CommandedState.OFF:
        raise ValueError("custom schedule must end OFF")
    for previous, current in zip(steps, steps[1:], strict=False):
        _validate_duration(current.offset, "run time")
        if current.offset <= previous.offset:
            raise ValueError("custom run times must be strictly increasing")
        if current.state is previous.state:
            raise ValueError("custom states must alternate")


def _validate_duration(value: timedelta, label: str) -> None:
    seconds = value.total_seconds()
    if not isfinite(seconds) or seconds < 0:
        raise ValueError(f"{label} must be a finite non-negative duration")
