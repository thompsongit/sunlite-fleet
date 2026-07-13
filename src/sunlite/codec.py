from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .domain import (
    CommandedState,
    CustomSchedule,
    RecoveryPolicy,
    RegularSchedule,
    Schedule,
    ScheduleKind,
    ScheduleStep,
    require_aware,
)

JsonObject = dict[str, Any]


def schedule_to_dict(schedule: Schedule) -> JsonObject:
    result: JsonObject = {
        "id": schedule.id,
        "device_id": schedule.device_id,
        "name": schedule.name,
        "kind": schedule.kind.value,
        "starts_at": schedule.starts_at.isoformat(),
        "timezone": schedule.timezone,
        "recovery_policy": schedule.recovery_policy.value,
        "enabled": schedule.enabled,
    }
    if isinstance(schedule, RegularSchedule):
        result.update(
            {
                "on_seconds": schedule.on_duration.total_seconds(),
                "off_seconds": schedule.off_duration.total_seconds(),
                "repeat_count": schedule.repeat_count,
                "ends_at": schedule.ends_at.isoformat() if schedule.ends_at else None,
            }
        )
    else:
        result["steps"] = [
            {"offset_seconds": step.offset.total_seconds(), "state": step.state.value}
            for step in schedule.steps
        ]
    return result


def schedule_from_dict(data: JsonObject) -> Schedule:
    starts_at = _datetime(data, "starts_at")
    schedule_id = _text(data, "id")
    device_id = _text(data, "device_id")
    name = _text(data, "name")
    timezone = _text(data, "timezone")
    recovery_policy = RecoveryPolicy(
        str(data.get("recovery_policy", RecoveryPolicy.ABORT_IF_INTERRUPTED.value))
    )
    enabled = bool(data.get("enabled", True))
    if ScheduleKind(_text(data, "kind")) is ScheduleKind.REGULAR:
        ends_at = data.get("ends_at")
        return RegularSchedule(
            id=schedule_id,
            device_id=device_id,
            name=name,
            starts_at=starts_at,
            timezone=timezone,
            on_duration=timedelta(seconds=_number(data, "on_seconds")),
            off_duration=timedelta(seconds=_number(data, "off_seconds")),
            repeat_count=(int(data["repeat_count"]) if data.get("repeat_count") else None),
            ends_at=(
                require_aware(datetime.fromisoformat(str(ends_at)), "ends_at")
                if ends_at
                else None
            ),
            recovery_policy=recovery_policy,
            enabled=enabled,
        )
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("steps must be a list")
    return CustomSchedule(
        id=schedule_id,
        device_id=device_id,
        name=name,
        starts_at=starts_at,
        timezone=timezone,
        steps=tuple(
            ScheduleStep(
                timedelta(seconds=float(step["offset_seconds"])),
                CommandedState(str(step["state"])),
            )
            for step in raw_steps
            if isinstance(step, dict)
        ),
        recovery_policy=recovery_policy,
        enabled=enabled,
    )


def _text(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def _number(data: JsonObject, key: str) -> float:
    value = data.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def _datetime(data: JsonObject, key: str) -> datetime:
    return require_aware(datetime.fromisoformat(_text(data, key)), key)
