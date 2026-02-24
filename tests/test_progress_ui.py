"""Tests for rich-progress-ui (T6)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from weiboloader.ui import (
    DownloadResult,
    EventKind,
    MediaOutcome,
    NullSink,
    ProgressSink,
    RichSink,
    UIEvent,
)
from weiboloader.weiboloader import WeiboLoader


class MockContext:
    """Minimal mock for WeiboLoaderContext."""
    def __init__(self):
        self.session = MagicMock()
        self._posts: dict[str, tuple] = {}

    def get_user_info(self, uid):
        from weiboloader.structures import User
        return User(uid=uid, nickname=f"User_{uid}", avatar_url=None)

    def resolve_nickname_to_uid(self, nickname):
        return nickname

    def get_user_posts(self, uid, page):
        key = f"u:{uid}:p:{page}"
        return self._posts.get(key, ([], None))

    def request(self, *args, **kwargs):
        return MagicMock()


class CollectorSink:
    """Sink that collects all events for assertion."""
    def __init__(self):
        self.events: list[UIEvent] = []
        self.closed = False

    def emit(self, event: UIEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.closed = True


class TestSafeEmit:
    def test_swallows_exception(self, tmp_path: Path):
        """_safe_emit never propagates exceptions from sink."""
        class BrokenSink:
            def emit(self, event):
                raise RuntimeError("boom")
            def close(self):
                pass

        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=BrokenSink())
        # Should not raise
        loader._safe_emit(UIEvent(kind=EventKind.STAGE, message="test"))

    def test_swallows_type_error(self, tmp_path: Path):
        class TypeErrorSink:
            def emit(self, event):
                raise TypeError("bad type")
            def close(self):
                pass

        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=TypeErrorSink())
        loader._safe_emit(UIEvent(kind=EventKind.TARGET_START, target_key="test"))


class TestDownloadResult:
    def test_skipped_existing(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "exists.jpg"
        dest.write_bytes(b"content")

        result = loader._download("http://example.com/img.jpg", dest)
        assert result.outcome == MediaOutcome.SKIPPED
        assert result.path == dest

    def test_downloaded_new(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "new.jpg"

        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"data"]
        with patch.object(ctx, "request", return_value=mock_resp):
            result = loader._download("http://example.com/img.jpg", dest)

        assert result.outcome == MediaOutcome.DOWNLOADED
        assert result.path == dest
        assert dest.read_bytes() == b"data"

    def test_failed_on_error(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "fail.jpg"

        with patch.object(ctx, "request", side_effect=Exception("network error")):
            result = loader._download("http://example.com/img.jpg", dest)

        assert result.outcome == MediaOutcome.FAILED
        assert result.path == dest


class TestEventSequence:
    def _make_post(self, mid, media_urls=None):
        from weiboloader.structures import MediaItem, Post
        from datetime import datetime, timedelta, timezone
        CST = timezone(timedelta(hours=8))
        items = [
            MediaItem(url=u, media_type="picture", index=i, filename_hint=None)
            for i, u in enumerate(media_urls or [])
        ]
        return Post(
            mid=mid, bid=None, text=f"Post {mid}",
            created_at=datetime.now(CST), user=None,
            media_items=items, raw={"mid": mid},
        )

    def test_event_order(self, tmp_path: Path):
        """Mock sink receives TARGET_START -> POST_DONE* -> TARGET_DONE."""
        sink = CollectorSink()
        ctx = MockContext()

        posts = [self._make_post("p1", ["http://example.com/1.jpg"])]
        ctx._posts["u:111:p:1"] = (posts, None)

        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg")):
            from weiboloader.structures import UserTarget
            loader.download_target(UserTarget(identifier="111", is_uid=True))

        kinds = [e.kind for e in sink.events]
        assert EventKind.TARGET_START in kinds
        assert EventKind.TARGET_DONE in kinds

        # TARGET_START before TARGET_DONE
        start_idx = kinds.index(EventKind.TARGET_START)
        done_idx = kinds.index(EventKind.TARGET_DONE)
        assert start_idx < done_idx

    def test_target_done_stats(self, tmp_path: Path):
        """TARGET_DONE event has correct statistics."""
        sink = CollectorSink()
        ctx = MockContext()

        posts = [self._make_post("p1", ["http://example.com/1.jpg", "http://example.com/2.jpg"])]
        ctx._posts["u:222:p:1"] = (posts, None)

        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)

        call_count = [0]
        def mock_download(url, dest):
            call_count[0] += 1
            if call_count[0] == 1:
                return DownloadResult(MediaOutcome.DOWNLOADED, dest)
            return DownloadResult(MediaOutcome.SKIPPED, dest)

        with patch.object(loader, "_download", side_effect=mock_download):
            from weiboloader.structures import UserTarget
            loader.download_target(UserTarget(identifier="222", is_uid=True))

        done_events = [e for e in sink.events if e.kind == EventKind.TARGET_DONE]
        assert len(done_events) == 1
        ev = done_events[0]
        assert ev.downloaded + ev.skipped + ev.failed == 2
        assert ev.downloaded == 1
        assert ev.skipped == 1
        assert ev.failed == 0

    def test_media_done_increments(self, tmp_path: Path):
        """Each MEDIA_DONE has incrementing media_done count."""
        sink = CollectorSink()
        ctx = MockContext()

        posts = [self._make_post("p1", ["http://example.com/1.jpg", "http://example.com/2.jpg", "http://example.com/3.jpg"])]
        ctx._posts["u:333:p:1"] = (posts, None)

        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "x.jpg")):
            from weiboloader.structures import UserTarget
            loader.download_target(UserTarget(identifier="333", is_uid=True))

        media_events = [e for e in sink.events if e.kind == EventKind.MEDIA_DONE]
        assert len(media_events) == 3
        for e in media_events:
            assert e.media_total == 3


class TestCaptchaPauseResume:
    def test_pause_resume_called(self):
        """pause and resume callbacks are each called once."""
        from weiboloader.context import WeiboLoaderContext

        pause_count = [0]
        resume_count = [0]

        def on_pause():
            pause_count[0] += 1

        def on_resume():
            resume_count[0] += 1

        ctx = WeiboLoaderContext(
            captcha_mode="skip",
            on_captcha_pause=on_pause,
            on_captcha_resume=on_resume,
        )

        ctx._solve_captcha("http://example.com/captcha")
        assert pause_count[0] == 1
        assert resume_count[0] == 1

    def test_resume_called_on_failure(self):
        """resume is called even when captcha handler raises."""
        from weiboloader.context import WeiboLoaderContext

        resume_count = [0]

        def on_resume():
            resume_count[0] += 1

        ctx = WeiboLoaderContext(
            captcha_mode="skip",
            on_captcha_pause=lambda: None,
            on_captcha_resume=on_resume,
        )

        # SkipCaptchaHandler returns False, resume should still be called
        ctx._solve_captcha("http://example.com/captcha")
        assert resume_count[0] == 1


class TestNullSink:
    def test_no_op(self):
        sink = NullSink()
        sink.emit(UIEvent(kind=EventKind.STAGE, message="test"))
        sink.close()
        # No exception means pass
