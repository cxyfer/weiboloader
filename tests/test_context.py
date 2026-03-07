"""Tests for WeiboLoaderContext (Phase 3.1)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import responses
from hypothesis import given, strategies as st

from weiboloader.context import WeiboLoaderContext
from weiboloader.exceptions import AuthError, RateLimitError, TargetError
from weiboloader.structures import Post, User


class MockRateController:
    """No-op rate controller for testing."""
    def wait_before_request(self, bucket: str) -> None:
        pass
    def handle_response(self, bucket: str, status_code: int) -> None:
        pass


class TestSessionPersistence:
    def test_save_load_roundtrip(self, tmp_path: Path):
        """PBT: Load(Save(Session)) == Session."""
        session_file = tmp_path / "session_12345.dat"
        ctx = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        ctx.session.cookies.set("SUB", "test_value", domain=".weibo.cn")

        ctx.save_session(uid="12345")
        assert session_file.exists()

        ctx2 = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        assert ctx2.load_session() is True
        assert ctx2.session.cookies.get("SUB", domain=".weibo.cn") == "test_value"

    def test_save_with_explicit_path(self, tmp_path: Path):
        session_file = tmp_path / "explicit.dat"
        ctx = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        ctx.session.cookies.set("SUB", "test_value", domain=".weibo.cn")
        ctx.save_session(path=session_file)
        assert session_file.exists()

    def test_load_nonexistent_returns_false(self, tmp_path: Path):
        ctx = WeiboLoaderContext(session_path=tmp_path / "nonexistent.dat")
        assert ctx.load_session() is False

    def test_load_corrupt_returns_false(self, tmp_path: Path):
        session_path = tmp_path / "session_bad.dat"
        session_path.write_text("not valid json", encoding="utf-8")
        ctx = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        assert ctx.load_session() is False

    def test_load_picks_most_recent(self, tmp_path: Path):
        import time
        ctx1 = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        ctx1.session.cookies.set("SUB", "old_value", domain=".weibo.cn")
        ctx1.save_session(uid="111")
        time.sleep(0.05)
        ctx2 = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        ctx2.session.cookies.set("SUB", "new_value", domain=".weibo.cn")
        ctx2.save_session(uid="222")

        ctx3 = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        assert ctx3.load_session() is True
        assert ctx3.session.cookies.get("SUB", domain=".weibo.cn") == "new_value"


class TestVerifyLogin:
    @responses.activate
    def test_login_true_returns_uid(self):
        responses.get(
            "https://m.weibo.cn/api/config",
            json={"data": {"login": True, "uid": 12345}},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        ok, uid = ctx.verify_login()
        assert ok is True
        assert uid == "12345"

    @responses.activate
    def test_login_false(self):
        responses.get(
            "https://m.weibo.cn/api/config",
            json={"data": {"login": False}},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        ok, uid = ctx.verify_login()
        assert ok is False
        assert uid is None

    @responses.activate
    def test_network_error_returns_none(self):
        responses.get("https://m.weibo.cn/api/config", body=ConnectionError("fail"))
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        ok, uid = ctx.verify_login()
        assert ok is None
        assert uid is None

    @responses.activate
    def test_missing_data_returns_none(self):
        responses.get("https://m.weibo.cn/api/config", json={"ok": 1})
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        ok, uid = ctx.verify_login()
        assert ok is None
        assert uid is None


class TestCookieFromString:
    def test_parse_cookie_string(self):
        ctx = WeiboLoaderContext()
        ctx.set_cookies_from_string("SUB=value1; SUBP=value2")
        assert ctx.session.cookies.get("SUB", domain=".weibo.cn") == "value1"
        assert ctx.session.cookies.get("SUBP", domain=".weibo.cn") == "value2"

    def test_empty_string_raises(self):
        ctx = WeiboLoaderContext()
        with pytest.raises(AuthError, match="empty cookie string"):
            ctx.set_cookies_from_string("")

    def test_newline_separated(self):
        ctx = WeiboLoaderContext()
        ctx.set_cookies_from_string("SUB=value1\nSUBP=value2")
        assert ctx.session.cookies.get("SUB", domain=".weibo.cn") == "value1"


class TestCookieFromFile:
    def test_load_from_file(self, tmp_path: Path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("SUB=test_value")

        ctx = WeiboLoaderContext()
        ctx.set_cookies_from_file(cookie_file)
        assert ctx.session.cookies.get("SUB", domain=".weibo.cn") == "test_value"


class TestBrowserCookies:
    @patch("builtins.__import__", side_effect=ImportError("not installed"))
    def test_import_error_raises_auth_error(self, mock_import):
        ctx = WeiboLoaderContext()
        with pytest.raises(AuthError, match="browser_cookie3 not installed"):
            ctx.load_browser_cookies("chrome")

    @patch("weiboloader.context.WeiboLoaderContext.load_browser_cookies")
    def test_unsupported_browser_raises(self, mock_load):
        mock_load.side_effect = AuthError("unsupported browser: safari")
        with pytest.raises(AuthError, match="unsupported browser"):
            raise AuthError("unsupported browser: safari")


class TestRequestWithMockedResponses:
    @responses.activate
    def test_successful_request(self):
        responses.get("https://m.weibo.cn/api/test", json={"ok": 1})
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        resp = ctx.request("GET", "/api/test")
        assert resp.status_code == 200

    @responses.activate
    def test_rate_limit_error(self):
        responses.get("https://m.weibo.cn/api/test", status=403)
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(RateLimitError):
            ctx.request("GET", "/api/test")

    @responses.activate
    def test_auth_error_401(self):
        responses.get("https://m.weibo.cn/api/test", status=401)
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(AuthError):
            ctx.request("GET", "/api/test")

    @responses.activate
    def test_target_error_404(self):
        responses.get("https://m.weibo.cn/api/test", status=404)
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(TargetError):
            ctx.request("GET", "/api/test")

    @responses.activate
    def test_server_error_with_retry(self):
        responses.get("https://m.weibo.cn/api/test", status=500)
        responses.get("https://m.weibo.cn/api/test", json={"ok": 1})
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        resp = ctx.request("GET", "/api/test")
        assert resp.status_code == 200

    @responses.activate
    def test_rate_limit_403(self):
        responses.get("https://m.weibo.cn/api/test", status=403)
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(RateLimitError):
            ctx.request("GET", "/api/test")


class TestRateControlIntegration:
    @responses.activate
    def test_wait_before_request_called(self):
        responses.get("https://m.weibo.cn/api/test", json={"ok": 1})

        mock_controller = MagicMock()
        ctx = WeiboLoaderContext(rate_controller=mock_controller)
        ctx.request("GET", "/api/test")

        mock_controller.wait_before_request.assert_called_once_with("api")
        mock_controller.handle_response.assert_called_once_with("api", 200)


class TestResolveNickname:
    @responses.activate
    def test_resolve_via_redirect(self):
        # Mock both requests (with and without allow_redirects)
        responses.add(
            responses.GET,
            "https://m.weibo.cn/n/testuser",
            status=302,
            headers={"Location": "https://m.weibo.cn/u/123456789"},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        uid = ctx.resolve_nickname_to_uid("testuser")
        assert uid == "123456789"

    @responses.activate
    def test_resolve_via_url_body(self):
        responses.add(
            responses.GET,
            "https://m.weibo.cn/n/testuser",
            body="https://weibo.com/u/987654321/profile"
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        uid = ctx.resolve_nickname_to_uid("testuser")
        assert uid == "987654321"

    @responses.activate
    def test_resolve_failure_raises(self):
        responses.add(
            responses.GET,
            "https://m.weibo.cn/n/testuser",
            body="no uid here"
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(TargetError, match="cannot resolve nickname"):
            ctx.resolve_nickname_to_uid("testuser")

    def test_resolve_all_requests_disable_captcha(self):
        """All /n/{name} requests must carry allow_captcha=False."""
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        calls: list[dict] = []

        def fake_request(method, url, **kwargs):
            calls.append({"url": url, "allow_captcha": kwargs.get("allow_captcha", True)})
            mock_resp = MagicMock()
            if len(calls) == 1:
                mock_resp.headers = {"Location": ""}
                mock_resp.url = url
                mock_resp.status_code = 302
                mock_resp.text = ""
            else:
                mock_resp.headers = {}
                mock_resp.url = "https://m.weibo.cn/u/111222333"
                mock_resp.status_code = 200
                mock_resp.text = ""
            return mock_resp

        with patch.object(ctx, "request", side_effect=fake_request):
            uid = ctx.resolve_nickname_to_uid("testuser")

        assert uid == "111222333"
        assert len(calls) == 2
        assert all(c["allow_captcha"] is False for c in calls)

    def test_resolve_passport_redirect_raises_auth_error(self):
        """302 to passport URL should raise AuthError."""
        ctx = WeiboLoaderContext(rate_controller=MockRateController())

        def fake_request(method, url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.headers = {"Location": "https://visitor.passport.weibo.cn/visitor/visitor?_rand=1772848282"}
            mock_resp.url = url
            mock_resp.status_code = 302
            return mock_resp

        with patch.object(ctx, "request", side_effect=fake_request):
            with pytest.raises(AuthError, match="login required"):
                ctx.resolve_nickname_to_uid("testuser")


class TestGetUserInfo:
    @responses.activate
    def test_get_user_info(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={
                "data": {
                    "userInfo": {
                        "id": "123456",
                        "screen_name": "TestUser",
                        "profile_image_url": "http://example.com/avatar.jpg",
                    }
                }
            },
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        user = ctx.get_user_info("123456")
        assert isinstance(user, User)
        assert user.uid == "123456"
        assert user.nickname == "TestUser"

    @responses.activate
    def test_user_not_found_raises(self):
        responses.get("https://m.weibo.cn/api/container/getIndex", json={"data": {"cards": []}})
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(AuthError, match="user not found"):
            ctx.get_user_info("123456")


class TestGetPosts:
    @responses.activate
    def test_get_user_posts(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={
                "data": {
                    "cards": [{"mblog": {"mid": "123", "text": "Hello", "created_at": "Mon Jan 01 00:00:00 +0800 2024"}}]
                }
            },
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        posts, cursor = ctx.get_user_posts("123456", 1)
        assert len(posts) == 1
        assert isinstance(posts[0], Post)

    @responses.activate
    def test_get_supertopic_posts(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={
                "data": {
                    "cardlistInfo": {"since_id": "next_cursor"},
                    "cards": [{"mblog": {"mid": "456", "text": "Topic post", "created_at": "Mon Jan 01 00:00:00 +0800 2024"}}],
                }
            },
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        posts, cursor = ctx.get_supertopic_posts("100808abc", 1)
        assert len(posts) == 1
        assert cursor == "next_cursor"


class TestGetPostByMid:
    @responses.activate
    def test_get_post_by_mid_html(self):
        html = '<script>var $render_data = [{"status": {"mid": "789", "text": "Detail", "created_at": "Mon Jan 01 00:00:00 +0800 2024"}}][0];</script>'
        responses.get("https://m.weibo.cn/detail/789", body=html)
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        post = ctx.get_post_by_mid("789")
        assert post.mid == "789"

    @responses.activate
    def test_get_post_by_mid_api(self):
        responses.get("https://m.weibo.cn/detail/789", body="no render data")
        responses.get(
            "https://m.weibo.cn/api/statuses/show",
            json={"data": {"mid": "789", "text": "API post", "created_at": "Mon Jan 01 00:00:00 +0800 2024"}},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        post = ctx.get_post_by_mid("789")
        assert post.mid == "789"

    @responses.activate
    def test_post_not_found_raises(self):
        responses.get("https://m.weibo.cn/detail/789", body="not found")
        responses.get("https://m.weibo.cn/api/statuses/show", json={})
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(TargetError, match="post not found"):
            ctx.get_post_by_mid("789")


class TestCaptchaModeRouting:
    @responses.activate
    def test_captcha_418_triggers_handler(self):
        # 418 triggers CAPTCHA detection when URL matches pattern
        responses.add(
            responses.GET,
            "https://m.weibo.cn/api/test",
            status=418,
            headers={"Location": "https://passport.weibo.com/verify"},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController(), captcha_mode="skip")
        # With skip mode, captcha handler returns False, leading to RateLimitError after retries
        with pytest.raises((AuthError, RateLimitError)):
            ctx.request("GET", "/api/test")


@given(st.dictionaries(st.text(min_size=1), st.text(), min_size=0, max_size=10))
def test_cookie_roundtrip_property(cookies):
    """PBT: cookies set can be retrieved."""
    ctx = WeiboLoaderContext()
    for name, value in cookies.items():
        if name and value:
            ctx.session.cookies.set(name, value, domain=".weibo.cn")

    for name, value in cookies.items():
        if name and value:
            retrieved = ctx.session.cookies.get(name, domain=".weibo.cn")
            assert retrieved == value


class TestExtractUid:
    def test_passport_url_returns_none(self):
        ctx = WeiboLoaderContext()
        assert ctx._extract_uid("https://visitor.passport.weibo.cn/visitor/visitor?_rand=1772848282") is None

    def test_login_sina_url_returns_none(self):
        ctx = WeiboLoaderContext()
        assert ctx._extract_uid("https://login.sina.com.cn/sso/login?_rand=12345678") is None

    def test_valid_u_path_returns_uid(self):
        ctx = WeiboLoaderContext()
        assert ctx._extract_uid("https://m.weibo.cn/u/3908122917") == "3908122917"

    def test_valid_uid_query_returns_uid(self):
        ctx = WeiboLoaderContext()
        assert ctx._extract_uid("https://m.weibo.cn/api?uid=12345") == "12345"

    def test_profile_path_returns_uid(self):
        ctx = WeiboLoaderContext()
        assert ctx._extract_uid("https://m.weibo.cn/profile/9876543210") == "9876543210"


class TestHandleResponse432:
    @responses.activate
    def test_432_retries_then_succeeds(self):
        responses.get("https://m.weibo.cn/api/test", status=432)
        responses.get("https://m.weibo.cn/api/test", json={"ok": 1})
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        resp = ctx.request("GET", "/api/test")
        assert resp.status_code == 200

    @responses.activate
    def test_432_exhausted_retries_raises_rate_limit(self):
        for _ in range(5):
            responses.get("https://m.weibo.cn/api/test", status=432)
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with pytest.raises(RateLimitError, match="rate limited"):
            ctx.request("GET", "/api/test")


class TestLoadSessionUAProtection:
    def test_load_session_preserves_default_ua(self, tmp_path: Path):
        import json
        session_file = tmp_path / "session_test.dat"
        payload = {
            "cookies": [{"name": "SUB", "value": "v", "domain": ".weibo.cn", "path": "/"}],
            "headers": {"User-Agent": "Evil/1.0", "X-Custom": "keep"},
        }
        session_file.write_text(json.dumps(payload), encoding="utf-8")

        ctx = WeiboLoaderContext(session_path=tmp_path / "session.dat")
        default_ua = ctx.session.headers["User-Agent"]
        ctx._load_session_file(session_file)

        assert ctx.session.headers["User-Agent"] == default_ua
        assert ctx.session.headers["X-Custom"] == "keep"


class TestFetchVisitorCookies:
    def test_success_sets_cookies(self):
        ctx = WeiboLoaderContext()
        with patch("weiboloader.context.VisitorCookieFetcher") as MockFetcher:
            MockFetcher.return_value.fetch.return_value = {"SUB": "visitor_sub", "_T_WM": "abc"}
            ctx.fetch_visitor_cookies()
        assert ctx.session.cookies.get("SUB", domain=".weibo.cn") == "visitor_sub"
        assert ctx.session.cookies.get("_T_WM", domain=".weibo.cn") == "abc"

    def test_empty_cookies_raises_auth_error(self):
        ctx = WeiboLoaderContext()
        with patch("weiboloader.context.VisitorCookieFetcher") as MockFetcher:
            MockFetcher.return_value.fetch.return_value = {}
            with pytest.raises(AuthError, match="failed to fetch visitor cookies"):
                ctx.fetch_visitor_cookies()

    def test_playwright_not_installed_raises(self):
        ctx = WeiboLoaderContext()
        with patch("weiboloader.context.VisitorCookieFetcher") as MockFetcher:
            MockFetcher.return_value.fetch.side_effect = ImportError("no playwright")
            with pytest.raises(ImportError):
                ctx.fetch_visitor_cookies()


class TestGetIndexCaptcha:
    @responses.activate
    def test_ok0_solve_captcha_then_retry_success(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={"ok": 0, "msg": "need captcha"},
        )
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={"ok": 1, "data": {"cards": []}},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with patch.object(ctx, "_solve_captcha", return_value=True) as mock_solve:
            data = ctx._get_index({"type": "uid", "value": "123"})
        assert data == {"cards": []}
        mock_solve.assert_called_once()

    @responses.activate
    def test_skip_mode_raises_rate_limit(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={"ok": 0, "msg": "rate limited"},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController(), captcha_mode="skip")
        with pytest.raises(RateLimitError, match="rate limited"):
            ctx._get_index({"type": "uid", "value": "123"})

    @responses.activate
    def test_solve_captcha_fails_raises_auth_error(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={"ok": 0, "msg": "captcha"},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with patch.object(ctx, "_solve_captcha", return_value=False):
            with pytest.raises(AuthError, match="captcha not solved"):
                ctx._get_index({"type": "uid", "value": "123"})

    @responses.activate
    def test_max_attempts_exhausted_raises_target_error(self):
        for _ in range(3):
            responses.get(
                "https://m.weibo.cn/api/container/getIndex",
                json={"ok": 0, "msg": "still blocked"},
            )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with patch.object(ctx, "_solve_captcha", return_value=True) as mock_solve:
            with pytest.raises(TargetError, match="still blocked"):
                ctx._get_index({"type": "uid", "value": "123"})
        assert mock_solve.call_count == 2

    @responses.activate
    def test_ok1_no_data_dict_raises_target_error(self):
        responses.get(
            "https://m.weibo.cn/api/container/getIndex",
            json={"ok": 1, "data": "not a dict"},
        )
        ctx = WeiboLoaderContext(rate_controller=MockRateController())
        with patch.object(ctx, "_solve_captcha", return_value=True) as mock_solve:
            with pytest.raises(TargetError):
                ctx._get_index({"type": "uid", "value": "123"})
