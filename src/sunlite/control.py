from __future__ import annotations

from datetime import datetime, timedelta

from .domain import (
    CommandedState,
    DeviceMode,
    DeviceRuntime,
    ManualOverride,
    Schedule,
    require_aware,
)
from .scheduling import state_at


class FleetArbiter:
    def __init__(self, device_ids: tuple[str, ...], manual_on_max_seconds: int = 3600) -> None:
        if not device_ids or len(set(device_ids)) != len(device_ids):
            raise ValueError("device ids must be non-empty and unique")
        if manual_on_max_seconds <= 0:
            raise ValueError("manual_on_max_seconds must be positive")
        self.devices = {device_id: DeviceRuntime(device_id) for device_id in device_ids}
        self.manual_on_max_seconds = manual_on_max_seconds
        self.global_stop_latched = False

    def stop_all(self, now: datetime) -> None:
        require_aware(now, "now")
        self.global_stop_latched = True
        for runtime in self.devices.values():
            runtime.manual_override = None
        self.evaluate(now, ())

    def resume_all(self) -> None:
        self.global_stop_latched = False

    def stop_device(self, device_id: str, now: datetime) -> None:
        runtime = self._device(device_id)
        runtime.stop_latched = True
        runtime.manual_override = None
        self._set(runtime, DeviceMode.STOPPED, CommandedState.OFF, now)

    def pause_device(self, device_id: str, now: datetime) -> None:
        runtime = self._device(device_id)
        runtime.paused = True
        runtime.manual_override = None
        self._set(runtime, DeviceMode.PAUSED, CommandedState.OFF, now)

    def resume_device(self, device_id: str) -> None:
        runtime = self._device(device_id)
        runtime.stop_latched = False
        runtime.paused = False
        runtime.manual_override = None

    def set_fault(self, device_id: str, fault: str, now: datetime) -> None:
        if not fault.strip():
            raise ValueError("fault cannot be empty")
        runtime = self._device(device_id)
        runtime.fault = fault
        runtime.manual_override = None
        self._set(runtime, DeviceMode.FAULT, CommandedState.OFF, now)

    def clear_fault(self, device_id: str) -> None:
        self._device(device_id).fault = None

    def set_manual_override(
        self,
        device_id: str,
        state: CommandedState,
        duration: timedelta,
        reason: str,
        now: datetime,
    ) -> None:
        require_aware(now, "now")
        runtime = self._device(device_id)
        if self.global_stop_latched or runtime.stop_latched or runtime.paused or runtime.fault:
            raise ValueError("manual override is unavailable while stopped, paused, or faulted")
        if duration <= timedelta(0):
            raise ValueError("manual override duration must be positive")
        if state is CommandedState.ON and duration.total_seconds() > self.manual_on_max_seconds:
            raise ValueError("manual ON exceeds configured maximum")
        if not reason.strip():
            raise ValueError("manual override reason is required")
        runtime.manual_override = ManualOverride(state, now + duration, reason.strip())

    def evaluate(
        self, now: datetime, schedules: tuple[Schedule, ...]
    ) -> dict[str, DeviceRuntime]:
        require_aware(now, "now")
        unknown = {schedule.device_id for schedule in schedules} - self.devices.keys()
        if unknown:
            raise ValueError(f"schedules reference unknown devices: {sorted(unknown)}")

        for device_id, runtime in self.devices.items():
            if runtime.manual_override and runtime.manual_override.expires_at <= now:
                runtime.manual_override = None

            if self.global_stop_latched:
                self._set(runtime, DeviceMode.STOPPED, CommandedState.OFF, now)
                continue
            if runtime.fault:
                self._set(runtime, DeviceMode.FAULT, CommandedState.OFF, now)
                continue
            if runtime.stop_latched:
                self._set(runtime, DeviceMode.STOPPED, CommandedState.OFF, now)
                continue
            if runtime.paused:
                self._set(runtime, DeviceMode.PAUSED, CommandedState.OFF, now)
                continue
            if runtime.manual_override:
                state = runtime.manual_override.state
                mode = DeviceMode.MANUAL_ON if state is CommandedState.ON else DeviceMode.MANUAL_OFF
                self._set(runtime, mode, state, now)
                continue

            active = [
                (schedule, current)
                for schedule in schedules
                if schedule.device_id == device_id
                if (current := state_at(schedule, now)) is not None
            ]
            if len(active) > 1:
                runtime.fault = "runtime_schedule_conflict"
                self._set(runtime, DeviceMode.FAULT, CommandedState.OFF, now)
                continue
            if active:
                schedule, state = active[0]
                mode = (
                    DeviceMode.AUTOMATIC_ON
                    if state is CommandedState.ON
                    else DeviceMode.AUTOMATIC_OFF
                )
                self._set(runtime, mode, state, now, schedule.id)
                continue
            self._set(runtime, DeviceMode.IDLE, CommandedState.OFF, now)
        return self.devices

    def _device(self, device_id: str) -> DeviceRuntime:
        try:
            return self.devices[device_id]
        except KeyError as error:
            raise KeyError(f"unknown device: {device_id}") from error

    @staticmethod
    def _set(
        runtime: DeviceRuntime,
        mode: DeviceMode,
        state: CommandedState,
        now: datetime,
        active_schedule_id: str | None = None,
    ) -> None:
        require_aware(now, "now")
        changed = (
            runtime.mode is not mode
            or runtime.commanded_state is not state
            or runtime.active_schedule_id != active_schedule_id
        )
        runtime.mode = mode
        runtime.commanded_state = state
        runtime.active_schedule_id = active_schedule_id
        if changed:
            runtime.updated_at = now
