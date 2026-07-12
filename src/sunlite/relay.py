from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .domain import CommandedState, require_aware


class RelayDriver(Protocol):
    def set_state(self, device_id: str, state: CommandedState, at: datetime) -> None: ...

    def state(self, device_id: str) -> CommandedState: ...


class MockRelay:
    def __init__(self, device_ids: tuple[str, ...]) -> None:
        self._states = dict.fromkeys(device_ids, CommandedState.OFF)

    def set_state(self, device_id: str, state: CommandedState, at: datetime) -> None:
        require_aware(at, "at")
        if device_id not in self._states:
            raise KeyError(f"unknown device: {device_id}")
        self._states[device_id] = state

    def state(self, device_id: str) -> CommandedState:
        return self._states[device_id]


@dataclass(frozen=True, slots=True)
class RelayEvent:
    device_id: str
    state: CommandedState
    at: datetime


class RecordingRelay(MockRelay):
    def __init__(self, device_ids: tuple[str, ...]) -> None:
        super().__init__(device_ids)
        self.events: list[RelayEvent] = []

    def set_state(self, device_id: str, state: CommandedState, at: datetime) -> None:
        super().set_state(device_id, state, at)
        self.events.append(RelayEvent(device_id, state, at))
