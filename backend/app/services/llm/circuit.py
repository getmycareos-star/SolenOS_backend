"""Circuit breaker around the local model.

A caregiver mid-crisis should never wait on a 45 second model timeout, and a
stopped Ollama should not add that wait to every single request. After a few
consecutive failures the breaker opens and the deterministic engines answer
directly until the cooldown has passed.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        reset_seconds: float = 120.0,
        clock=None,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_seconds = float(reset_seconds)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if (self._clock() - self._opened_at) >= self.reset_seconds:
                self._failures = 0
                self._opened_at = None
                return False
            return True

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._failures

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = self._clock()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def state(self) -> str:
        return "open" if self.is_open else "closed"
