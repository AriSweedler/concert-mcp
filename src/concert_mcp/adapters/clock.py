"""The wall clock (unit adapter-openclaw)."""

from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    """Clock backed by datetime and time."""

    def now(self) -> datetime:
        """Aware UTC now."""
        return datetime.now(UTC)

    def time(self) -> float:
        """time.time()."""
        return time.time()

    def sleep(self, seconds: float) -> None:
        """time.sleep()."""
        time.sleep(seconds)
