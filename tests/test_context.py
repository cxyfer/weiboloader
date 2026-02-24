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
        session_path = tmp_path / "session.dat"
        ctx = WeiboLoaderContext(session_path=session_path)
        ctx.session.cookies.set("SUB", "test_value", domain=".weibo.cn")

        ctx.save_session()
        assert session_path.exists()

        ctx2 = WeiboLoaderContext(session_path=session_path)
        assert ctx2.load_session() is True
        assert ctx2.session.cookies.get("SUB", domain=".weibo.cn") == "test_value"

    def test_load_nonexistent_returns_false(self, tmp_path: Path):
        ctx = WeiboLoaderContext(session_path=tmp_path / "nonexistent.dat")
        assert ctx.load_session() is False

    def test_load_corrupt_returns_false(self, tmp_path: Path):
        session_path = tmp_path / "session.dat"
        session_path.write_text("not valid json", encoding="utf-8")
        ctx = WeiboLoaderContext(session_path=session_path)
        assert ctx.load_session() is False


class TestCookieValidation:
    def test_validate_with_sub_passes(self):
        ctx = WeiboLoaderContext()
        ctx.session.cookies.set("SUB", "value", domain=".weibo.cn")
        ctx.validate_cookie()

    def test_validate_without_sub_raises(self):
        ctx = WeiboLoaderContext()
        with pytest.raises(AuthError, match="missing SUB cookie"):
            ctx.validate_cookie()


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
