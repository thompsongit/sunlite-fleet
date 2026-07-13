from __future__ import annotations

from datetime import datetime
from time import sleep
from typing import Any

from .config import AppConfig, DeviceConfig
from .domain import CommandedState, RelayProfile, require_aware


class GpioZeroRelay:
    """GPIO Zero adapter; construction is intentionally deferred to the Pi process."""

    def __init__(self, config: AppConfig) -> None:
        try:
            from gpiozero import OutputDevice  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError("GPIO Zero is required on the controller host") from error

        unsupported = [
            device.id
            for device in config.devices
            if device.enabled and device.profile is RelayProfile.SINGLE_TOGGLE_PULSE
        ]
        if unsupported:
            raise ValueError(f"unattended single-toggle profiles are disabled: {unsupported}")

        self._devices = {device.id: device for device in config.devices if device.enabled}
        self._outputs: dict[str, Any] = {
            channel.id: OutputDevice(
                channel.bcm_pin,
                active_high=channel.active_high,
                initial_value=False,
            )
            for channel in config.channels
            if channel.enabled
        }
        self._states = dict.fromkeys(self._devices, CommandedState.OFF)
        self._pulse_seconds = config.pulse_seconds

    def set_state(self, device_id: str, state: CommandedState, at: datetime) -> None:
        require_aware(at, "at")
        device = self._device(device_id)
        if self._states[device_id] is state:
            return
        outputs = [self._outputs[channel_id] for channel_id in device.channel_ids]
        if device.profile is RelayProfile.MAINTAINED_OUTPUT:
            outputs[0].on() if state is CommandedState.ON else outputs[0].off()
        elif device.profile is RelayProfile.DUAL_SET_RESET_PULSE:
            for output in outputs:
                output.off()
            selected = outputs[0] if state is CommandedState.ON else outputs[1]
            selected.on()
            sleep(self._pulse_seconds)
            selected.off()
        else:
            raise ValueError(f"unsupported unattended relay profile: {device.profile}")
        self._states[device_id] = state

    def state(self, device_id: str) -> CommandedState:
        self._device(device_id)
        return self._states[device_id]

    def close(self) -> None:
        for output in self._outputs.values():
            output.off()
            output.close()

    def _device(self, device_id: str) -> DeviceConfig:
        try:
            return self._devices[device_id]
        except KeyError as error:
            raise KeyError(f"unknown device: {device_id}") from error
