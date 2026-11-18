"""Small, dependency-free pipeline timings; values are cumulative milliseconds."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager


class StageTimings:
    def __init__(self):
        self._seconds: dict[str, float] = defaultdict(float)
        self._calls: dict[str, int] = defaultdict(int)

    @contextmanager
    def measure(self, stage: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self._seconds[stage] += time.perf_counter() - started
            self._calls[stage] += 1

    def snapshot(self) -> dict[str, float]:
        return {name: round(seconds * 1000, 3) for name, seconds in sorted(self._seconds.items())}

    def calls(self) -> dict[str, int]:
        return dict(sorted(self._calls.items()))
