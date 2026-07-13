from __future__ import annotations

import re
import tomllib
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import CommandedState, RelayProfile

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class RelayChannelConfig:
    id: str
    bcm_pin: int
    active_high: bool = True
    initial_state: CommandedState = CommandedState.OFF
    enabled: bool = True

    def __post_init__(self) -> None:
        _valid_id(self.id, "channel id")
        if not 0 <= self.bcm_pin <= 27:
            raise ValueError(f"invalid BCM pin for {self.id}: {self.bcm_pin}")
        if self.initial_state is not CommandedState.OFF:
            raise ValueError(f"{self.id} must initialize OFF")


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    id: str
    name: str
    profile: RelayProfile
    channel_ids: tuple[str, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        _valid_id(self.id, "device id")
        if not self.name.strip():
            raise ValueError("device name cannot be empty")
        required = {
            RelayProfile.MAINTAINED_OUTPUT: 1,
            RelayProfile.DUAL_SET_RESET_PULSE: 2,
            RelayProfile.SINGLE_TOGGLE_PULSE: 1,
        }[self.profile]
        if self.enabled and len(self.channel_ids) != required:
            raise ValueError(f"{self.id} profile {self.profile} requires {required} channel(s)")


@dataclass(frozen=True, slots=True)
class AppConfig:
    timezone: str
    manual_on_max_seconds: int
    database_path: str
    controller_socket: str
    pulse_seconds: float
    channels: tuple[RelayChannelConfig, ...]
    devices: tuple[DeviceConfig, ...]

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown timezone: {self.timezone}") from error
        if self.manual_on_max_seconds <= 0:
            raise ValueError("manual_on_max_seconds must be positive")
        if not self.database_path or not self.controller_socket:
            raise ValueError("database_path and controller_socket are required")
        if not 0.02 <= self.pulse_seconds <= 5:
            raise ValueError("pulse_seconds must be between 0.02 and 5 seconds")
        _unique((channel.id for channel in self.channels), "channel id")
        _unique((channel.bcm_pin for channel in self.channels), "BCM pin")
        _unique((device.id for device in self.devices), "device id")
        _unique((device.name for device in self.devices), "device name")

        channel_ids = {channel.id for channel in self.channels if channel.enabled}
        assigned: list[str] = []
        for device in self.devices:
            missing = set(device.channel_ids) - channel_ids
            if missing:
                raise ValueError(f"{device.id} references unavailable channels: {sorted(missing)}")
            if device.enabled:
                assigned.extend(device.channel_ids)
        _unique(assigned, "assigned relay channel")


def load_config(path: str | Path) -> AppConfig:
    with Path(path).open("rb") as stream:
        raw = tomllib.load(stream)
    return config_from_dict(raw)


def config_from_dict(raw: dict[str, Any]) -> AppConfig:
    channels = tuple(
        RelayChannelConfig(
            id=str(item["id"]),
            bcm_pin=int(item["bcm_pin"]),
            active_high=bool(item.get("active_high", True)),
            initial_state=CommandedState(str(item.get("initial_state", "off"))),
            enabled=bool(item.get("enabled", True)),
        )
        for item in _tables(raw, "channels")
    )
    devices = tuple(
        DeviceConfig(
            id=str(item["id"]),
            name=str(item["name"]),
            profile=RelayProfile(str(item["profile"])),
            channel_ids=tuple(str(value) for value in item.get("channels", [])),
            enabled=bool(item.get("enabled", True)),
        )
        for item in _tables(raw, "devices")
    )
    return AppConfig(
        timezone=str(raw.get("timezone", "UTC")),
        manual_on_max_seconds=int(raw.get("manual_on_max_seconds", 3600)),
        database_path=str(raw.get("database_path", "sunlite.db")),
        controller_socket=str(raw.get("controller_socket", "/tmp/sunlite-controller.sock")),
        pulse_seconds=float(raw.get("pulse_seconds", 0.25)),
        channels=channels,
        devices=devices,
    )


def _tables(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of tables")
    return value


def _valid_id(value: str, name: str) -> None:
    if not _ID.fullmatch(value):
        raise ValueError(f"invalid {name}: {value}")


def _unique(values: Iterable[Hashable], label: str) -> None:
    seen: set[Hashable] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label}: {value}")
        seen.add(value)
