from __future__ import annotations

import random
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class _BucketConfig:
    limit: int
    window: float


@dataclass
class _BucketState:
    timestamps: deque[float] = field(default_factory=deque)
    last_request_at: float | None = None
    failures: int = 0
    backoff_until: float = 0.0


class BaseRateController(ABC):
    @abstractmethod
    def wait_before_request(self, bucket: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def handle_response(self, bucket: str, status_code: int) -> None:
        raise NotImplementedError


class SlidingWindowRateController(BaseRateController):
    def __init__(
        self,
        api_limit: int = 30,
        api_window: float = 600,
        base_delay: float = 30,
        max_delay: float = 600,
        jitter_ratio: float = 0.5,
        request_interval: float = 0.0,
    ) -> None:
        if api_limit <= 0 or api_window <= 0:
            raise ValueError("api_limit and api_window must be > 0")

        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.jitter_ratio = float(jitter_ratio)
        self.request_interval = float(request_interval)

        cfg = _BucketConfig(limit=api_limit, window=api_window)
        self._config: dict[str, _BucketConfig] = {"api": cfg, "media": cfg}
        self._state: dict[str, _BucketState] = {"api": _BucketState(), "media": _BucketState()}
        self._lock = threading.RLock()
        self._random = random.Random()

        # Injectable for testing
        self._now = time.monotonic
        self._sleep = time.sleep

    def wait_before_request(self, bucket: str) -> None:
        while True:
            with self._lock:
                cfg = self._config[bucket]
                state = self._state[bucket]
                now = self._now()

                # Clean old timestamps
                while state.timestamps and now - state.timestamps[0] >= cfg.window:
                    state.timestamps.popleft()

                # Calculate required wait
                wait = 0.0
                if len(state.timestamps) >= cfg.limit:
                    wait = max(wait, state.timestamps[0] + cfg.window - now)
                if bucket == "api" and self.request_interval > 0 and state.last_request_at is not None:
                    wait = max(wait, state.last_request_at + self.request_interval - now)
                wait = max(wait, state.backoff_until - now)

                if wait <= 0:
                    # Atomic: record timestamp while holding lock
                    state.timestamps.append(now)
                    state.last_request_at = now
                    return

            self._sleep(wait)

    def handle_response(self, bucket: str, status_code: int) -> None:
        with self._lock:
            state = self._state[bucket]
            if status_code in (403, 418):
                state.failures += 1
                base = min(self.base_delay * (2 ** (state.failures - 1)), self.max_delay)
                jitter = base * self.jitter_ratio * self._random.random()
                state.backoff_until = self._now() + base + jitter
            elif 200 <= status_code < 400:
                state.failures = 0
                state.backoff_until = 0.0
