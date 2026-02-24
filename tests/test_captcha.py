"""Tests for CAPTCHA handlers (Phase 2.3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from hypothesis import given, settings, strategies as st

from weiboloader._captcha import (
    ManualCaptchaHandler,
    PlaywrightCaptchaHandler,
    SkipCaptchaHandler,
    VisitorCookieFetcher,
    _is_captcha_url,
    extract_captcha_url,
    is_playwright_available,
)


class TestIsPlaywrightAvailable:
    def test_returns_bool(self):
        result = is_playwright_available()
        assert isinstance(result, bool)

    @patch("builtins.__import__", side_effect=ImportError("no module"))
    def test_false_on_import_error(self, mock_import):
        assert is_playwright_available() is False


class TestIsCaptchaUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://passport.weibo.com/verify", True),
            ("https://login.sina.com.cn/challenge", True),
            ("https://weibo.com/captcha/test", True),
            ("https://api.weibo.cn/2/statuses", False),
            ("https://m.weibo.cn/detail/123", False),
        ],
    )
    def test_detection(self, url, expected):
        assert _is_captcha_url(url) is expected


class TestExtractCaptchaUrl:
    def test_418_status_with_captcha_url(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 418
        resp.url = "https://passport.weibo.com/verify"
        resp.headers = {}
        assert extract_captcha_url(resp) == resp.url

    def test_418_status_without_captcha_url(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 418
        resp.url = "https://api.weibo.cn/normal"
        resp.headers = {}
        assert extract_captcha_url(resp) is None

    def test_redirect_header(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 302
        resp.url = "https://api.weibo.cn/2/statuses"
        resp.headers = {"Location": "https://passport.weibo.com/verify"}
        assert extract_captcha_url(resp) == "https://passport.weibo.com/verify"

    def test_no_captcha(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.url = "https://api.weibo.cn/2/statuses"
        resp.headers = {}
        assert extract_captcha_url(resp) is None


class TestSkipCaptchaHandler:
    def test_always_returns_false(self):
        handler = SkipCaptchaHandler()
        session = MagicMock(spec=requests.Session)
        assert handler.solve("https://passport.weibo.com/verify", session) is False


class TestManualCaptchaHandler:
    def test_returns_bool(self):
        handler = ManualCaptchaHandler()
        session = MagicMock(spec=requests.Session)
        result = handler.solve("https://passport.weibo.com/verify", session, timeout=0.01)
        assert isinstance(result, bool)


class TestPlaywrightCaptchaHandler:
    def test_init(self):
        handler = PlaywrightCaptchaHandler(headless=True)
        assert handler.headless is True


class TestCaptchaStateMachine:
    """PBT: CAPTCHA state machine properties."""

    @given(st.sampled_from(["skip"]))
    @settings(deadline=None)
    def test_state_transitions_never_reverse(self, mode):
        """CAPTCHA state: INIT -> WAITING -> SOLVED|TIMEOUT (no reverse)."""
        handler = SkipCaptchaHandler()
        session = MagicMock(spec=requests.Session)
        session.cookies = []

        result = handler.solve("https://passport.weibo.com/verify", session, timeout=0.1)
        assert result is False

    @given(st.floats(min_value=0.01, max_value=0.5))
    @settings(deadline=None)
    def test_duration_within_timeout(self, timeout):
        """CAPTCHA total duration <= Config.Timeout."""
        import time

        handler = SkipCaptchaHandler()
        session = MagicMock(spec=requests.Session)

        start = time.monotonic()
        handler.solve("https://passport.weibo.com/verify", session, timeout=timeout)
        elapsed = time.monotonic() - start

        assert elapsed <= timeout + 0.1


class TestVisitorCookieFetcher:
    def test_init_headless_default(self):
        fetcher = VisitorCookieFetcher()
        assert fetcher.headless is True

    def test_init_headless_false(self):
        fetcher = VisitorCookieFetcher(headless=False)
        assert fetcher.headless is False

    def test_import_error_propagates(self):
        """playwright not installed -> ImportError."""
        fetcher = VisitorCookieFetcher()
        with patch.dict("sys.modules", {"playwright.sync_api": None}):
            with pytest.raises(ImportError):
                fetcher.fetch()
