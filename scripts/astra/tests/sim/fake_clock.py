"""Deterministic clock for simulation tests."""


class FakeClock:
    """Replaces ``time.time`` and ``time.sleep`` with deterministic versions."""

    def __init__(self, start: float = 1_000_000.0):
        self.now: float = start
        self.total_slept: float = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.total_slept += seconds

    def advance(self, seconds: float) -> None:
        """Manually advance the clock (without recording as sleep)."""
        self.now += seconds
