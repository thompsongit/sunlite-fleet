from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .clock import Clock, SystemClock
from .codec import JsonObject, schedule_from_dict, schedule_to_dict
from .config import AppConfig, load_config
from .control import FleetArbiter
from .domain import (
    AuditEvent,
    CommandedState,
    CommandRecord,
    DeviceMode,
    DeviceRuntime,
    RecoveryAction,
    RunRecord,
    Schedule,
)
from .gpio import GpioZeroRelay
from .relay import RecordingRelay, RelayDriver
from .scheduling import (
    find_conflicts,
    preview,
    recovery_action,
    schedule_end,
    state_at,
    transitions_between,
)
from .storage import Repository


class ControllerService:
    def __init__(
        self,
        config: AppConfig,
        repository: Repository,
        relay: RelayDriver,
        clock: Clock | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.relay = relay
        self.clock = clock or SystemClock()
        device_ids = tuple(device.id for device in config.devices if device.enabled)
        self.arbiter = FleetArbiter(device_ids, config.manual_on_max_seconds)
        self._active_runs: dict[str, str] = {}
        self._persisted_runtime: dict[str, tuple[object, ...]] = {}
        self._persisted_global_stop: bool | None = None
        self._wake = asyncio.Event()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self.repository.migrate()
        self.repository.save_registry(self.config)
        now = self.clock.now()
        for device_id, runtime in self.arbiter.devices.items():
            persisted = self.repository.load_runtime(device_id)
            if persisted:
                runtime.stop_latched = persisted.stop_latched
                runtime.paused = persisted.paused
                runtime.fault = persisted.fault
            runtime.commanded_state = CommandedState.OFF
            runtime.active_schedule_id = None
            runtime.manual_override = None
            runtime.updated_at = now
            self.relay.set_state(device_id, CommandedState.OFF, now)
        self.arbiter.global_stop_latched = self.repository.load_global_stop()
        self._apply_startup_recovery(now)
        self.reconcile(now)
        self.repository.record_audit(AuditEvent(now, "system", "controller_started"))
        self._initialized = True

    def reconcile(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.clock.now()
        schedules = self.repository.list_schedules()
        runtimes = self.arbiter.evaluate(now, schedules)
        schedule_map = {schedule.id: schedule for schedule in schedules}

        for device_id, runtime in runtimes.items():
            previous_state = self.relay.state(device_id)
            if previous_state is not runtime.commanded_state:
                self.relay.set_state(device_id, runtime.commanded_state, now)
                self.repository.record_audit(
                    AuditEvent(
                        now,
                        "controller",
                        "output_commanded",
                        device_id,
                        runtime.commanded_state.value,
                    )
                )
            self._update_run(device_id, runtime.mode, runtime.active_schedule_id, schedule_map, now)
            signature = self._runtime_signature(runtime)
            if self._persisted_runtime.get(device_id) != signature:
                self.repository.save_runtime(runtime)
                self._persisted_runtime[device_id] = signature
        if self._persisted_global_stop != self.arbiter.global_stop_latched:
            self.repository.save_global_stop(self.arbiter.global_stop_latched, now)
            self._persisted_global_stop = self.arbiter.global_stop_latched
        return self.status(now, schedules)

    def next_boundary(self, now: datetime | None = None) -> datetime | None:
        now = now or self.clock.now()
        candidates: list[datetime] = []
        for schedule in self.repository.list_schedules():
            if not schedule.enabled or schedule_end(schedule) <= now:
                continue
            candidates.extend(
                transition.at
                for transition in transitions_between(schedule, now, schedule_end(schedule))
                if transition.at > now
            )
        candidates.extend(
            runtime.manual_override.expires_at
            for runtime in self.arbiter.devices.values()
            if runtime.manual_override and runtime.manual_override.expires_at > now
        )
        return min(candidates) if candidates else None

    def status(
        self,
        now: datetime | None = None,
        schedules: tuple[Schedule, ...] | None = None,
    ) -> JsonObject:
        now = now or self.clock.now()
        schedules = schedules if schedules is not None else self.repository.list_schedules()
        names = {device.id: device.name for device in self.config.devices}
        schedule_names = {schedule.id: schedule.name for schedule in schedules}
        devices: list[JsonObject] = []
        for device_id, runtime in self.arbiter.devices.items():
            next_transition = self._next_device_transition(device_id, schedules, now)
            devices.append(
                {
                    "id": device_id,
                    "name": names[device_id],
                    "commanded_state": runtime.commanded_state.value,
                    "mode": runtime.mode.value,
                    "fault": runtime.fault,
                    "active_schedule_id": runtime.active_schedule_id,
                    "active_schedule_name": (
                        schedule_names.get(runtime.active_schedule_id)
                        if runtime.active_schedule_id
                        else None
                    ),
                    "updated_at": runtime.updated_at.isoformat(),
                    "next_transition": next_transition,
                }
            )
        return {
            "healthy": True,
            "now": now.isoformat(),
            "timezone": self.config.timezone,
            "global_stop_latched": self.arbiter.global_stop_latched,
            "devices": devices,
        }

    def handle(self, request: JsonObject) -> Any:
        action = str(request.get("action", ""))
        actor = str(request.get("actor", "local"))
        now = self.clock.now()

        if action == "status":
            return self.reconcile(now)
        if action == "schedules.list":
            return [schedule_to_dict(item) for item in self.repository.list_schedules()]
        if action == "schedules.get":
            schedule = self.repository.get_schedule(str(request.get("id", "")))
            if schedule is None:
                raise KeyError("schedule not found")
            return schedule_to_dict(schedule)
        if action == "schedules.preview":
            schedule = schedule_from_dict(_object(request, "schedule"))
            return [
                {"at": item.at.isoformat(), "state": item.state.value} for item in preview(schedule)
            ]
        if action == "history":
            return self._history()
        if action == "system":
            return self._system_status()

        command_id = str(request.get("idempotency_key") or uuid4())
        previous_action = self.repository.command_action(command_id)
        if previous_action:
            if previous_action != action:
                raise ValueError("idempotency key was already used for another action")
            return self.reconcile(now)
        if action == "schedules.save":
            schedule = schedule_from_dict(_object(request, "schedule"))
            others = tuple(
                item for item in self.repository.list_schedules() if item.id != schedule.id
            )
            conflicts = find_conflicts((*others, schedule))
            if conflicts:
                raise ValueError("schedule conflicts with another enabled schedule for this device")
            self.repository.save_schedule(schedule)
        elif action == "schedules.delete":
            self.repository.archive_schedule(str(request.get("id", "")))
        elif action == "stop_all":
            self._abort_active_schedules(None, "stopped", now)
            self.arbiter.stop_all(now)
        elif action == "resume_all":
            self.arbiter.resume_all()
        elif action == "device.stop":
            device_id = _text(request, "device_id")
            self._abort_active_schedules(device_id, "stopped", now)
            self.arbiter.stop_device(device_id, now)
        elif action == "device.pause":
            self.arbiter.pause_device(_text(request, "device_id"), now)
        elif action == "device.resume":
            self.arbiter.resume_device(_text(request, "device_id"))
        elif action == "device.manual":
            self.arbiter.set_manual_override(
                _text(request, "device_id"),
                CommandedState(_text(request, "state")),
                timedelta(seconds=float(request.get("duration_seconds", 0))),
                _text(request, "reason"),
                now,
            )
        elif action == "device.clear_fault":
            self.arbiter.clear_fault(_text(request, "device_id"))
        else:
            raise ValueError(f"unknown action: {action}")

        self._record_command(command_id, action, request, actor, now)
        self.repository.record_audit(
            AuditEvent(now, actor, action, str(request.get("device_id") or "") or None)
        )
        result = self.reconcile(now)
        self._wake.set()
        return result

    async def scheduler_loop(self) -> None:
        self.initialize()
        while True:
            now = self.clock.now()
            self.reconcile(now)
            boundary = self.next_boundary(now)
            timeout = (
                1.0
                if boundary is None
                else min(1.0, max(0.01, (boundary - now).total_seconds()))
            )
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)

    def shutdown(self) -> None:
        now = self.clock.now()
        for device_id in self.arbiter.devices:
            if self.relay.state(device_id) is not CommandedState.OFF:
                self.relay.set_state(device_id, CommandedState.OFF, now)
        self.repository.record_audit(AuditEvent(now, "system", "controller_stopped"))
        self.relay.close()

    def _apply_startup_recovery(self, now: datetime) -> None:
        for schedule in self.repository.list_schedules():
            action = recovery_action(schedule, now)
            if action is RecoveryAction.ABORT:
                self.repository.set_schedule_enabled(schedule.id, False)
                self.repository.save_run(
                    RunRecord(
                        self._run_id(schedule),
                        schedule.id,
                        schedule.device_id,
                        schedule.starts_at,
                        actual_end=now,
                        outcome="interrupted",
                    )
                )
                self.repository.record_audit(
                    AuditEvent(now, "controller", "run_interrupted_on_restart", schedule.device_id)
                )
            elif action is RecoveryAction.RESUME:
                self.repository.record_audit(
                    AuditEvent(now, "controller", "run_resumed_after_restart", schedule.device_id)
                )

    def _abort_active_schedules(
        self, device_id: str | None, outcome: str, now: datetime
    ) -> None:
        for schedule in self.repository.list_schedules():
            if device_id is not None and schedule.device_id != device_id:
                continue
            if state_at(schedule, now) is None:
                continue
            self.repository.set_schedule_enabled(schedule.id, False)
            self.repository.save_run(
                RunRecord(
                    self._run_id(schedule),
                    schedule.id,
                    schedule.device_id,
                    schedule.starts_at,
                    actual_end=now,
                    outcome=outcome,
                )
            )
            self._active_runs.pop(schedule.device_id, None)

    def _update_run(
        self,
        device_id: str,
        mode: DeviceMode,
        active_schedule_id: str | None,
        schedules: dict[str, Schedule],
        now: datetime,
    ) -> None:
        previous_id = self._active_runs.get(device_id)
        if previous_id and active_schedule_id is None:
            previous = schedules.get(previous_id)
            if (
                mode in {DeviceMode.PAUSED, DeviceMode.MANUAL_ON, DeviceMode.MANUAL_OFF}
                and previous
                and now < schedule_end(previous)
            ):
                return
            if previous:
                outcome = "completed" if now >= schedule_end(previous) else "interrupted"
                self.repository.save_run(
                    RunRecord(
                        self._run_id(previous),
                        previous.id,
                        device_id,
                        previous.starts_at,
                        actual_end=now,
                        outcome=outcome,
                    )
                )
            self._active_runs.pop(device_id, None)
        if active_schedule_id and active_schedule_id != previous_id:
            schedule = schedules[active_schedule_id]
            self.repository.save_run(
                RunRecord(
                    self._run_id(schedule),
                    schedule.id,
                    device_id,
                    schedule.starts_at,
                    actual_start=now,
                    outcome="running",
                )
            )
            self._active_runs[device_id] = active_schedule_id

    def _next_device_transition(
        self, device_id: str, schedules: tuple[Schedule, ...], now: datetime
    ) -> JsonObject | None:
        candidates = [
            item
            for schedule in schedules
            if schedule.device_id == device_id and schedule.enabled and schedule_end(schedule) > now
            for item in transitions_between(schedule, now, schedule_end(schedule))
            if item.at > now
        ]
        if not candidates:
            return None
        item = min(candidates, key=lambda transition: transition.at)
        return {"at": item.at.isoformat(), "state": item.state.value}

    def _history(self) -> JsonObject:
        return {
            "runs": [
                {
                    "id": run.id,
                    "schedule_id": run.schedule_id,
                    "device_id": run.device_id,
                    "planned_start": run.planned_start.isoformat(),
                    "actual_start": run.actual_start.isoformat() if run.actual_start else None,
                    "actual_end": run.actual_end.isoformat() if run.actual_end else None,
                    "outcome": run.outcome,
                }
                for run in self.repository.list_runs()
            ],
            "audit": [
                {
                    "at": event.at.isoformat(),
                    "actor": event.actor,
                    "action": event.action,
                    "device_id": event.device_id,
                    "details": event.details,
                }
                for event in self.repository.list_audit()
            ],
        }

    def _system_status(self) -> JsonObject:
        return {
            "controller": "healthy",
            "database": str(self.repository.path),
            "socket": self.config.controller_socket,
            "timezone": self.config.timezone,
            "devices": [
                {
                    "id": device.id,
                    "name": device.name,
                    "profile": device.profile.value,
                    "channels": list(device.channel_ids),
                    "enabled": device.enabled,
                }
                for device in self.config.devices
            ],
            "channels": [
                {
                    "id": channel.id,
                    "bcm_pin": channel.bcm_pin,
                    "active_high": channel.active_high,
                    "enabled": channel.enabled,
                }
                for channel in self.config.channels
            ],
        }

    def _record_command(
        self,
        command_id: str,
        action: str,
        request: JsonObject,
        actor: str,
        now: datetime,
    ) -> None:
        self.repository.record_command(
            CommandRecord(
                command_id,
                action,
                now,
                actor,
                str(request.get("device_id") or "") or None,
                str(request.get("reason") or ""),
            )
        )

    @staticmethod
    def _run_id(schedule: Schedule) -> str:
        stamp = int(schedule.starts_at.astimezone(UTC).timestamp())
        return f"{schedule.id}:{stamp}"

    @staticmethod
    def _runtime_signature(runtime: DeviceRuntime) -> tuple[object, ...]:
        manual = runtime.manual_override
        return (
            runtime.mode,
            runtime.commanded_state,
            runtime.stop_latched,
            runtime.paused,
            runtime.fault,
            manual.state if manual else None,
            manual.expires_at if manual else None,
            manual.reason if manual else None,
            runtime.active_schedule_id,
        )


def _object(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _text(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def main() -> None:
    import argparse

    from .ipc import run_controller

    parser = argparse.ArgumentParser(description="Run the Sunlite controller")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--mock", action="store_true", help="Use in-memory relay outputs")
    arguments = parser.parse_args()
    config = load_config(Path(arguments.config))
    repository = Repository(config.database_path)
    relay: RelayDriver = (
        RecordingRelay(tuple(device.id for device in config.devices if device.enabled))
        if arguments.mock
        else GpioZeroRelay(config)
    )
    service = ControllerService(config, repository, relay)
    with suppress(KeyboardInterrupt):
        asyncio.run(run_controller(service, config.controller_socket))
