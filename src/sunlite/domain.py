from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class CommandedState(StrEnum):
    OFF = "off"
    ON = "on"


class RelayProfile(StrEnum):
    MAINTAINED_OUTPUT = "maintained_output"
    DUAL_SET_RESET_PULSE = "dual_set_reset_pulse"
    SINGLE_TOGGLE_PULSE = "single_toggle_pulse"


class RecoveryPolicy(StrEnum):
    ABORT_IF_INTERRUPTED = "abort_if_interrupted"
    RESUME_IF_ACTIVE = "resume_if_active"


class ScheduleKind(StrEnum):
    REGULAR = "regular"
    CUSTOM = "custom"
    ON_DEMAND = "on_demand"


class DeviceMode(StrEnum):
    IDLE = "idle"
    AUTOMATIC_ON = "automatic_on"
    AUTOMATIC_OFF = "automatic_off"
    MANUAL_ON = "manual_on"
    MANUAL_OFF = "manual_off"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAULT = "fault"


class RecoveryAction(StrEnum):
    FUTURE = "future"
    ABORT = "abort"
    RESUME = "resume"
    COMPLETE = "complete"


def require_aware(value: datetime, name: str = "datetime") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ScheduleStep:
    offset: timedelta
    state: CommandedState


@dataclass(frozen=True, slots=True)
class RegularSchedule:
    id: str
    device_id: str
    name: str
    starts_at: datetime
    timezone: str
    on_duration: timedelta
    off_duration: timedelta
    repeat_count: int | None = None
    ends_at: datetime | None = None
    recovery_policy: RecoveryPolicy = RecoveryPolicy.ABORT_IF_INTERRUPTED
    enabled: bool = True
    handoff_delay: timedelta = timedelta()
    ocp_duration: timedelta = timedelta()
    kind: ScheduleKind = field(default=ScheduleKind.REGULAR, init=False)


@dataclass(frozen=True, slots=True)
class CustomSchedule:
    id: str
    device_id: str
    name: str
    starts_at: datetime
    timezone: str
    steps: tuple[ScheduleStep, ...]
    recovery_policy: RecoveryPolicy = RecoveryPolicy.ABORT_IF_INTERRUPTED
    enabled: bool = True
    handoff_delay: timedelta = timedelta()
    ocp_duration: timedelta = timedelta()
    kind: ScheduleKind = field(default=ScheduleKind.CUSTOM, init=False)


Schedule = RegularSchedule | CustomSchedule


@dataclass(frozen=True, slots=True)
class OnDemandSchedule:
    id: str
    device_id: str
    name: str
    timezone: str
    steps: tuple[ScheduleStep, ...]
    recovery_policy: RecoveryPolicy = RecoveryPolicy.ABORT_IF_INTERRUPTED
    enabled: bool = True
    handoff_delay: timedelta = timedelta()
    ocp_duration: timedelta = timedelta()
    kind: ScheduleKind = field(default=ScheduleKind.ON_DEMAND, init=False)


ScheduleDefinition = Schedule | OnDemandSchedule


@dataclass(frozen=True, slots=True)
class Transition:
    schedule_id: str
    device_id: str
    at: datetime
    state: CommandedState
    phase: str = "pattern"


@dataclass(frozen=True, slots=True)
class RelativeTransition:
    schedule_id: str
    device_id: str
    run_time: timedelta
    state: CommandedState
    phase: str = "pattern"


@dataclass(frozen=True, slots=True)
class ScheduleConflict:
    device_id: str
    first_schedule_id: str
    second_schedule_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class ManualOverride:
    state: CommandedState
    expires_at: datetime
    reason: str


@dataclass(slots=True)
class DeviceRuntime:
    device_id: str
    mode: DeviceMode = DeviceMode.IDLE
    commanded_state: CommandedState = CommandedState.OFF
    stop_latched: bool = False
    paused: bool = False
    fault: str | None = None
    manual_override: ManualOverride | None = None
    active_schedule_id: str | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class CommandRecord:
    id: str
    action: str
    requested_at: datetime
    requested_by: str
    device_id: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    at: datetime
    actor: str
    action: str
    device_id: str | None = None
    details: str = ""


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    schedule_id: str
    device_id: str
    planned_start: datetime
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    outcome: str = "planned"
    source_schedule_id: str | None = None
    handoff_delay: timedelta = timedelta()
    ocp_duration: timedelta = timedelta()
    first_light_at: datetime | None = None
