from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol

from .domain import require_aware


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return monotonic()


@dataclass(slots=True)
class FakeClock:
    current: datetime
    elapsed: float = 0.0

    def __post_init__(self) -> None:
        require_aware(self.current, "current")

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.elapsed

    def advance(self, amount: timedelta) -> None:
        if amount < timedelta(0):
            raise ValueError("advance amount cannot be negative")
        self.current += amount
        self.elapsed += amount.total_seconds()

    def set_wall_time(self, value: datetime) -> None:
        self.current = require_aware(value, "value")
