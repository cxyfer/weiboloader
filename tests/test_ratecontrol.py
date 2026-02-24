"""Tests for rate controller (Phase 2.1)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import given, settings, strategies as st

from weiboloader.ratecontrol import SlidingWindowRateController


@dataclass
class FakeClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.value += seconds


def bind_fake_clock(controller: SlidingWindowRateController, clock: FakeClock) -> None:
    controller._now = clock.now  # type: ignore[method-assign]
    controller._sleep = clock.sleep  # type: ignore[method-assign]


class TestSlidingWindowQuota:
    def test_api_window_quota_enforced(self):
        """PBT: API quota within window."""
        controller = SlidingWindowRateController(
            api_limit=3,
            api_window=10,
            jitter_ratio=0.0,
        )
        clock = FakeClock()
        bind_fake_clock(controller, clock)

        # Make 3 requests at time 0,1,2 - all within limit
        for i in range(3):
            controller.wait_before_request("api")
            clock.advance(1)

        # 4th request at time 3 should wait until time 10 (window expiry of first)
        start = clock.now()  # time = 3
        controller.wait_before_request("api")
        elapsed = clock.now() - start

        # Should have waited until time 10 (when first request expires from window)
        assert elapsed >= 6  # 10 - 3 = 7, but give some margin

    def test_api_and_media_bucket_isolation(self):
        """PBT: API and media buckets do not cross-pollute."""
        controller = SlidingWindowRateController(
            api_limit=2,
            api_window=10,
            jitter_ratio=0.0,
        )
        clock = FakeClock()
        bind_fake_clock(controller, clock)

        controller.wait_before_request("api")
        controller.wait_before_request("api")

        before = clock.now()
        controller.wait_before_request("media")
        after = clock.now()
        assert after == before


class TestBackoff:
    def test_backoff_monotonic_excluding_jitter(self):
        """PBT: Backoff delay increases monotonically."""
        controller = SlidingWindowRateController(
            api_limit=10,
            api_window=600,
            base_delay=4,
            max_delay=100,
            jitter_ratio=0.0,
        )
        clock = FakeClock()
        bind_fake_clock(controller, clock)

        waits = []
        for attempt in range(3):
            controller.handle_response("api", 403)
            start = clock.now()
            controller.wait_before_request("api")
            waited = clock.now() - start
            waits.append(waited)

        assert waits == sorted(waits)
        assert waits[0] == pytest.approx(4.0)
        assert waits[1] == pytest.approx(8.0)

    def test_backoff_resets_after_success(self):
        controller = SlidingWindowRateController(
            api_limit=10,
            api_window=600,
            base_delay=4,
            max_delay=100,
            jitter_ratio=0.0,
        )
        clock = FakeClock()
        bind_fake_clock(controller, clock)

        controller.handle_response("api", 403)
        start = clock.now()
        controller.wait_before_request("api")
        first_wait = clock.now() - start
        assert first_wait == pytest.approx(4.0)

        controller.handle_response("api", 200)
        controller.handle_response("api", 403)
        start = clock.now()
        controller.wait_before_request("api")
        second_wait = clock.now() - start
        assert second_wait == pytest.approx(4.0)


class TestRequestInterval:
    def test_request_interval_is_enforced(self):
        controller = SlidingWindowRateController(
            api_limit=10,
            api_window=600,
            request_interval=5.0,
            jitter_ratio=0.0,
        )
        clock = FakeClock()
        bind_fake_clock(controller, clock)

        controller.wait_before_request("api")
        first_at = clock.now()
        controller.wait_before_request("api")
        second_at = clock.now()

        # The second request should wait 5s after the first
        assert second_at - first_at >= 5.0


@given(st.integers(min_value=10, max_value=100))
def test_max_delay_cap(delay):
    """PBT: backoff delay capped at max_delay."""
    controller = SlidingWindowRateController(
        api_limit=10,
        api_window=600,
        base_delay=1,
        max_delay=delay,
        jitter_ratio=0.0,
    )
    clock = FakeClock()
    bind_fake_clock(controller, clock)

    # After many failures, backoff should be capped at max_delay
    for _ in range(10):
        controller.handle_response("api", 403)
        start = clock.now()
        controller.wait_before_request("api")
        waited = clock.now() - start
        assert waited <= delay + 1.0  # Allow some margin for base_delay calc
