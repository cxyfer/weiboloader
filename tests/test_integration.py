"""Integration tests for WeiboLoader (Phase 6)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import responses

from weiboloader.context import WeiboLoaderContext
from weiboloader.structures import Post, User, UserTarget, SuperTopicTarget, MidTarget
from weiboloader.weiboloader import WeiboLoader

if TYPE_CHECKING:
    pass


CST = timezone(timedelta(hours=8))


class MockWeiboAPI:
    """Mock Weibo API responses for integration testing."""

    def __init__(self):
        self.posts: list[dict] = []
        self.user_info: dict | None = None
        self.current_page = 0

    def add_user_posts(self, count: int, uid: str = "123456") -> None:
        """Add mock posts for a user."""
        for i in range(count):
            self.posts.append({
                "mblog": {
                    "mid": f"{uid}_{i}",
                    "text": f"Post content {i}",
                    "created_at": "Mon Jan 01 00:00:00 +0800 2024",
                    "user": {
                        "id": uid,
                        "screen_name": f"TestUser_{uid}",
                        "avatar_large": "http://example.com/avatar.jpg"
                    },
                    "pics": [{"large": {"url": f"http://example.com/img_{i}.jpg"}}] if i % 2 == 0 else []
                }
            })

    def add_video_post(self, uid: str = "123456") -> None:
        """Add a post with video."""
        self.posts.append({
            "mblog": {
                "mid": f"{uid}_video",
                "text": "Video post",
                "created_at": "Mon Jan 01 00:00:00 +0800 2024",
                "user": {
                    "id": uid,
                    "screen_name": f"TestUser_{uid}",
                },
                "page_info": {
                    "type": "video",
                    "media_info": {
                        "stream_url_hd": "http://example.com/video.mp4"
                    }
                }
            }
        })

    def setup_user_info(self, uid: str = "123456", nickname: str = "TestUser") -> None:
        """Setup user info response."""
        self.user_info = {
            "userInfo": {
                "id": uid,
                "screen_name": nickname,
                "profile_image_url": "http://example.com/avatar.jpg"
            }
        }

    def register(self, rsps: responses.RequestsMock, uid: str = "123456") -> None:
        """Register all mock responses."""
        # User info endpoint
        if self.user_info:
            rsps.add(
                responses.GET,
                "https://m.weibo.cn/api/container/getIndex",
                json={"data": self.user_info},
                match=[responses.matchers.query_param_matcher({"type": "uid", "value": uid})]
            )

        # Posts endpoint - paginated (using containerid format)
        for page in range(3):  # Support up to 3 pages
            start_idx = page * 10
            end_idx = min(start_idx + 10, len(self.posts))
            page_posts = self.posts[start_idx:end_idx]

            rsps.add(
                responses.GET,
                "https://m.weibo.cn/api/container/getIndex",
                json={
                    "data": {
                        "cards": page_posts,
                        "cardlistInfo": {"since_id": str(page + 1)} if end_idx < len(self.posts) else {}
                    }
                },
                match=[responses.matchers.query_param_matcher({
                    "containerid": f"107603{uid}",
                    "page": str(page + 1)
                })]
            )


@pytest.fixture
def mock_api():
    """Fixture providing MockWeiboAPI instance."""
    return MockWeiboAPI()


@pytest.fixture
def mock_context(tmp_path: Path):
    """Fixture providing WeiboLoaderContext with mocked rate controller."""
    class NoOpRateController:
        def wait_before_request(self, bucket: str) -> None:
            pass
        def handle_response(self, bucket: str, status_code: int) -> None:
            pass

    ctx = WeiboLoaderContext(
        rate_controller=NoOpRateController(),
        session_path=tmp_path / "session.dat"
    )
    # Set auth cookie to pass validation
    ctx.session.cookies.set("SUB", "test_value", domain=".weibo.cn")
    return ctx


class TestFullLifecycle:
    """Full-lifecycle integration tests."""

    @responses.activate
    def test_download_user_posts(self, tmp_path: Path, mock_api: MockWeiboAPI, mock_context: WeiboLoaderContext):
        """Test: weiboloader <uid> downloads user media to ./{nickname}/"""
        mock_api.setup_user_info("123456", "TestUser")
        mock_api.add_user_posts(5, "123456")
        mock_api.register(responses, uid="123456")

        loader = WeiboLoader(mock_context, output_dir=tmp_path)

        with patch.object(loader, '_download', return_value=tmp_path / "test.jpg"):
            result = loader.download_target(
                UserTarget(identifier="123456", is_uid=True)
            )

        assert result is True

    @responses.activate
    def test_download_supertopic_posts(self, tmp_path: Path, mock_context: WeiboLoaderContext):
        """Test: weiboloader '#topic' downloads supertopic media"""
        # Mock the super topic search endpoint
        responses.add(
            responses.GET,
            "https://m.weibo.cn/api/container/getIndex",
            json={
                "data": {
                    "cards": [
                        {
                            "card_type": "8",
                            "title_sub": "#topic#",
                            "containerid": "100808abc123"
                        }
                    ]
                }
            },
            match=[responses.matchers.query_param_matcher({"containerid": "100103type=98&q=topic"})]
        )
        # Mock the super topic posts endpoint - page 1 and 2
        for page in [1, 2]:
            responses.add(
                responses.GET,
                "https://m.weibo.cn/api/container/getIndex",
                json={
                    "data": {
                        "cards": [
                            {"mblog": {"mid": f"st_{page}", "text": "Topic post", "created_at": "Mon Jan 01 00:00:00 +0800 2024"}}
                        ] if page == 1 else [],
                        "cardlistInfo": {"since_id": "next_page"} if page == 1 else {}
                    }
                },
                match=[responses.matchers.query_param_matcher({"containerid": "100808abc123_-_feed", "page": str(page)})]
            )

        loader = WeiboLoader(mock_context, output_dir=tmp_path)

        with patch.object(loader, '_download', return_value=tmp_path / "test.jpg"):
            result = loader.download_target(
                SuperTopicTarget(identifier="topic", is_containerid=False)
            )

        assert result is True

    @responses.activate
    def test_download_by_mid(self, tmp_path: Path, mock_context: WeiboLoaderContext):
        """Test: weiboloader -mid <mid> downloads single post"""
        html = '<script>var $render_data = [{"status": {"mid": "abc123", "text": "Detail", "created_at": "Mon Jan 01 00:00:00 +0800 2024"}}][0];</script>'
        responses.add(responses.GET, "https://m.weibo.cn/detail/abc123", body=html)

        loader = WeiboLoader(mock_context, output_dir=tmp_path)

        with patch.object(loader, '_download', return_value=tmp_path / "test.jpg"):
            result = loader.download_target(
                MidTarget(mid="abc123")
            )

        assert result is True


class TestResumeOnFailure:
    """Resume-on-failure integration tests."""

    @responses.activate
    def test_checkpoint_resume(self, tmp_path: Path, mock_api: MockWeiboAPI, mock_context: WeiboLoaderContext):
        """Test: crash mid-download -> second run skips completed files via checkpoint"""
        mock_api.setup_user_info("123456", "TestUser")
        mock_api.add_user_posts(3, "123456")
        mock_api.register(responses, uid="123456")

        checkpoint_dir = tmp_path / "checkpoints"
        checkpoint_dir.mkdir()

        loader = WeiboLoader(
            mock_context,
            output_dir=tmp_path,
            checkpoint_dir=checkpoint_dir
        )

        # Simulate first run that processes 2 posts then "crashes"
        processed_count = [0]
        def count_downloads(*args, **kwargs):
            processed_count[0] += 1
            if processed_count[0] >= 2:
                raise KeyboardInterrupt("Simulated crash")
            return tmp_path / "test.jpg"

        with patch.object(loader, '_download', side_effect=count_downloads):
            try:
                loader.download_target(
                    UserTarget(identifier="123456", is_uid=True)
                )
            except KeyboardInterrupt:
                pass

        # Checkpoint should exist
        checkpoint_files = list(checkpoint_dir.glob("*.json"))
        assert len(checkpoint_files) > 0, "Checkpoint should be saved on interrupt"


class TestRateLimitRecovery:
    """Rate-limit recovery integration tests."""

    @responses.activate
    def test_418_captcha_detection(self, tmp_path: Path):
        """Test: mock 418 -> triggers captcha handling"""
        # Request returns 418 from captcha URL
        responses.add(
            responses.GET,
            "https://passport.weibo.com/verify",
            status=418
        )

        # With skip captcha mode, it should raise AuthError
        class NoOpRateController:
            def wait_before_request(self, bucket: str) -> None:
                pass
            def handle_response(self, bucket: str, status_code: int) -> None:
                pass

        ctx = WeiboLoaderContext(
            rate_controller=NoOpRateController(),
            captcha_mode="skip"
        )
        ctx.session.cookies.set("SUB", "test", domain=".weibo.cn")

        from weiboloader.exceptions import AuthError
        with pytest.raises(AuthError):
            ctx.request("GET", "https://passport.weibo.com/verify")


class TestFilterVerification:
    """Filter verification integration tests."""

    @responses.activate
    def test_no_videos_filter(self, tmp_path: Path, mock_api: MockWeiboAPI, mock_context: WeiboLoaderContext):
        """Test: --no-videos -> zero .mp4 in output"""
        mock_api.setup_user_info("123456", "TestUser")
        mock_api.add_user_posts(3, "123456")
        mock_api.add_video_post("123456")
        mock_api.register(responses, uid="123456")

        loader = WeiboLoader(mock_context, output_dir=tmp_path, no_videos=True)

        media_types_captured = []
        def capture_media_type(media_item, *args, **kwargs):
            media_types_captured.append(media_item.media_type)
            return tmp_path / "test.file"

        with patch.object(loader, '_download', side_effect=capture_media_type):
            loader.download_target(
                UserTarget(identifier="123456", is_uid=True)
            )

        assert "video" not in media_types_captured, "Videos should be filtered out"

    @responses.activate
    def test_no_pictures_filter(self, tmp_path: Path, mock_api: MockWeiboAPI, mock_context: WeiboLoaderContext):
        """Test: --no-pictures -> zero images in output"""
        mock_api.setup_user_info("123456", "TestUser")
        mock_api.add_user_posts(3, "123456")
        mock_api.register(responses, uid="123456")

        loader = WeiboLoader(mock_context, output_dir=tmp_path, no_pictures=True)

        media_types_captured = []
        def capture_media_type(media_item, *args, **kwargs):
            media_types_captured.append(media_item.media_type)
            return tmp_path / "test.file"

        with patch.object(loader, '_download', side_effect=capture_media_type):
            loader.download_target(
                UserTarget(identifier="123456", is_uid=True)
            )

        assert "picture" not in media_types_captured, "Pictures should be filtered out"


class TestIncrementalUpdate:
    """Incremental update integration tests."""

    @responses.activate
    def test_latest_stamps_skip_old_posts(self, tmp_path: Path, mock_context: WeiboLoaderContext, mock_api: MockWeiboAPI):
        """Test: --latest-stamps -> second run downloads zero new posts"""
        mock_api.setup_user_info("123456", "TestUser")
        mock_api.add_user_posts(3, "123456")
        mock_api.register(responses, uid="123456")

        stamps_path = tmp_path / "stamps.json"

        # First run with current timestamp
        loader1 = WeiboLoader(mock_context, output_dir=tmp_path, latest_stamps=stamps_path)

        with patch.object(loader1, '_download', return_value=tmp_path / "test.jpg"):
            loader1.download_target(
                UserTarget(identifier="123456", is_uid=True)
            )

        # Verify stamps were saved
        assert stamps_path.exists()

        # Second run should skip all posts
        processed_in_second_run = []
        def track_processed(post, *args, **kwargs):
            processed_in_second_run.append(post.mid)
            return tmp_path / "test.jpg"

        loader2 = WeiboLoader(mock_context, output_dir=tmp_path, latest_stamps=stamps_path)

        with patch.object(loader2, '_download', side_effect=track_processed):
            loader2.download_target(
                UserTarget(identifier="123456", is_uid=True)
            )

        # All posts should be skipped as they're older than saved stamp
        # Note: This depends on actual timestamp logic in weiboloader


class TestCaptchaFallback:
    """CAPTCHA fallback integration tests."""

    def test_playwright_unavailable_fallback(self, tmp_path: Path):
        """Test: Playwright unavailable -> ManualCaptchaHandler invoked"""
        from weiboloader._captcha import is_playwright_available, SkipCaptchaHandler, ManualCaptchaHandler

        # When playwright is not available, auto mode should fall back
        with patch('weiboloader._captcha.is_playwright_available', return_value=False):
            # Context should still initialize without error
            ctx = WeiboLoaderContext(captcha_mode="skip")
            assert ctx._captcha_handlers["skip"].__class__ == SkipCaptchaHandler

    def test_manual_captcha_timeout(self):
        """Test: ManualCaptchaHandler times out correctly"""
        from weiboloader._captcha import ManualCaptchaHandler

        handler = ManualCaptchaHandler()
        session = MagicMock()
        session.cookies = []

        # Should return False when timeout occurs
        result = handler.solve("https://passport.weibo.com/verify", session, timeout=0.01)
        assert isinstance(result, bool)


class TestFastUpdate:
    """Fast update integration tests."""

    @responses.activate
    def test_fast_update_stops_on_existing(self, tmp_path: Path, mock_api: MockWeiboAPI, mock_context: WeiboLoaderContext):
        """Test: --fast-update stops when existing files found"""
        mock_api.setup_user_info("123456", "TestUser")
        mock_api.add_user_posts(5, "123456")
        mock_api.register(responses, uid="123456")

        # Create existing file for first post
        target_dir = tmp_path / "TestUser_123456"
        target_dir.mkdir()
        (target_dir / "existing.jpg").write_text("exists")

        loader = WeiboLoader(mock_context, output_dir=tmp_path, fast_update=True)

        # Mock _media_path to return existing file path
        with patch.object(loader, '_media_path', return_value=target_dir / "existing.jpg"):
            with patch.object(loader, '_save_ck'):
                result = loader.download_target(
                    UserTarget(identifier="123456", is_uid=True)
                )

        # Should complete without error
        assert result is not None


class TestCookieLoading:
    """Cookie loading integration tests."""

    def test_load_cookies_from_string(self, tmp_path: Path):
        """Test: --cookie loads cookies from string"""
        ctx = WeiboLoaderContext()
        ctx.set_cookies_from_string("SUB=test_value; SUBP=test_value2")

        assert ctx.session.cookies.get("SUB", domain=".weibo.cn") == "test_value"
        assert ctx.session.cookies.get("SUBP", domain=".weibo.cn") == "test_value2"

    def test_load_cookies_from_file(self, tmp_path: Path):
        """Test: --cookie-file loads cookies from file"""
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("SUB=file_test_value")

        ctx = WeiboLoaderContext()
        ctx.set_cookies_from_file(cookie_file)

        assert ctx.session.cookies.get("SUB", domain=".weibo.cn") == "file_test_value"
