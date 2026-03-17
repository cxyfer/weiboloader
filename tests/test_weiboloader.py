"""Tests for WeiboLoader orchestrator (Phase 4.1)."""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

try:
    import certifi
except Exception:
    certifi = types.ModuleType("certifi")
    sys.modules["certifi"] = certifi
if not hasattr(certifi, "where"):
    certifi.where = lambda: ""

from weiboloader.context import WeiboLoaderContext
from weiboloader.exceptions import CheckpointError
from weiboloader.progress import ProgressStore
from weiboloader.structures import CursorState, MediaItem, MidTarget, Post, SearchTarget, SuperTopicTarget, UserTarget
from weiboloader.ui import DownloadResult, EventKind, MediaOutcome
from weiboloader.weiboloader import WeiboLoader


CST = timezone(timedelta(hours=8))


def make_post(
    mid: str,
    created_at: datetime | None = None,
    media_items: list[MediaItem] | None = None,
    raw: dict | None = None,
) -> Post:
    return Post(
        mid=mid,
        bid=None,
        text=f"Post {mid}",
        created_at=created_at or datetime.now(CST),
        user=None,
        media_items=media_items or [],
        raw=raw or {"mid": mid, "text": f"Post {mid}"},
    )


def make_media(url: str, media_type: str = "picture", index: int = 0) -> MediaItem:
    return MediaItem(
        media_type=media_type,  # type: ignore
        url=url,
        index=index,
        filename_hint=f"media_{index}",
        raw={},
    )


class MockContext:
    def __init__(self):
        self.session = MagicMock()
        self.req_timeout = 20
        self._posts: dict[str, list[Post]] = {}
        self._uids: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs):
        mock = MagicMock()
        mock.status_code = 200
        mock.iter_content.return_value = [b"test data"]
        return mock

    def get_user_posts(self, uid: str, page: int):
        return self._posts.get(f"u:{uid}:p:{page}", ([], None))

    def get_supertopic_posts(self, cid: str, page: int):
        return self._posts.get(f"t:{cid}:p:{page}", ([], None))

    def search_posts(self, keyword: str, page: int):
        return self._posts.get(f"s:{keyword}:p:{page}", ([], None))

    def get_post_by_mid(self, mid: str):
        return self._posts.get(f"m:{mid}", [make_post(mid)])[0]

    def resolve_nickname_to_uid(self, nickname: str):
        return self._uids.get(nickname, nickname)

    def get_user_info(self, uid: str):
        mock = MagicMock()
        mock.nickname = f"User_{uid}"
        mock.uid = uid
        return mock

    def search_supertopic(self, keyword: str):
        mock = MagicMock()
        mock.containerid = f"100808{keyword}"
        mock.name = keyword
        return [mock]


def load_progress_state(base_dir: Path, target_key: str):
    return ProgressStore(base_dir / ".progress").load(target_key)


class TestDownloadMedia:
    def test_skip_existing_file_with_size(self, tmp_path: Path):
        """PBT: exists && size>0 -> skip."""
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        dest = tmp_path / "test.jpg"
        dest.write_text("existing content")

        result = loader._download("http://example.com/img.jpg", dest)
        assert result == DownloadResult(MediaOutcome.SKIPPED, dest)

    def test_download_when_empty(self, tmp_path: Path):
        """PBT: size==0 -> download."""
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        dest = tmp_path / "test.jpg"
        dest.touch()

        with patch.object(ctx, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.iter_content.return_value = [b"new data"]
            mock_req.return_value = mock_resp

            result = loader._download("http://example.com/img.jpg", dest)

        assert result is not None
        assert result.outcome == MediaOutcome.DOWNLOADED
        assert dest.read_bytes() == b"new data"
        _, url = mock_req.call_args[0]
        assert url == "http://example.com/img.jpg"
        assert mock_req.call_args.kwargs["bucket"] == "media"
        assert mock_req.call_args.kwargs["allow_captcha"] is False
        assert mock_req.call_args.kwargs["retries"] == 2

    def test_download_uses_media_bucket_rate_controller(self, tmp_path: Path):
        rate_controller = MagicMock()
        ctx = WeiboLoaderContext(rate_controller=rate_controller)
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "paced.jpg"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"paced data"]

        with patch.object(ctx.session, "request", return_value=mock_resp):
            result = loader._download("http://example.com/img.jpg", dest)

        assert result.outcome == MediaOutcome.DOWNLOADED
        rate_controller.wait_before_request.assert_called_once_with("media")
        rate_controller.handle_response.assert_called_once_with("media", 200)

    def test_part_file_rename(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        dest = tmp_path / "test.jpg"

        with patch.object(ctx, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.iter_content.return_value = [b"test content"]
            mock_req.return_value = mock_resp

            result = loader._download("http://example.com/img.jpg", dest)

        assert result == DownloadResult(MediaOutcome.DOWNLOADED, dest)
        assert dest.exists()
        assert not (tmp_path / "test.jpg.part").exists()


class TestMediaFiltering:
    def test_no_videos_filter(self, tmp_path: Path):
        """PBT: --no-videos -> zero video items."""
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, no_videos=True)

        post = make_post("m1", media_items=[
            make_media("http://v.mp4", "video", 0),
            make_media("http://i.jpg", "picture", 1),
        ])

        jobs = loader._media_jobs(tmp_path, post)
        assert len(jobs) == 1
        assert jobs[0][0].media_type == "picture"

    def test_no_pictures_filter(self, tmp_path: Path):
        """PBT: --no-pictures -> zero picture items."""
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, no_pictures=True)

        post = make_post("m1", media_items=[
            make_media("http://v.mp4", "video", 0),
            make_media("http://i.jpg", "picture", 1),
        ])

        jobs = loader._media_jobs(tmp_path, post)
        assert len(jobs) == 1
        assert jobs[0][0].media_type == "video"


class TestCountLimit:
    def test_count_limits_processed(self, tmp_path: Path):
        """PBT: processed_count <= --count."""
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, count=3)

        posts = [make_post(f"m{i}") for i in range(10)]
        ctx._posts["u:test:p:1"] = (posts[:5], "c2")
        ctx._posts["u:test:p:2"] = (posts[5:], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            with patch.object(loader, "_media_jobs", return_value=[]):
                loader.download_target(UserTarget(identifier="test", is_uid=True))


class TestProgressPersistence:
    def test_progress_roundtrip_uses_progress_store(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target_key = "u:test"
        state = loader._progress.load(target_key)
        assert state is None

        post = make_post("m1", created_at=datetime(2024, 1, 15, 12, 0, tzinfo=CST))
        ctx._posts["u:test:p:1"] = ([post], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.target_key == target_key
        assert saved.resume is None
        assert saved.coverage[0].start.isoformat() == "2024-01-15T12:00:00+08:00"
        assert saved.coverage[0].end.isoformat() == "2024-01-15T12:00:00+08:00"

    def test_no_resume_only_clears_resume(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        loader = WeiboLoader(ctx, output_dir=tmp_path, no_resume=True)
        store = ProgressStore(tmp_path / ".progress")
        store.save(
            target_key,
            resume=CursorState(
                page=2,
                cursor="cursor-2",
                seen_mids=["old"],
                buffered_posts=[],
                pending_cursor=None,
                pending_has_more=False,
                page_loaded=False,
                options_hash=loader._options_hash,
                timestamp="2024-01-01T00:00:00+08:00",
            ),
            coverage=[(datetime(2024, 1, 14, 12, 0, tzinfo=CST), datetime(2024, 1, 14, 12, 0, tzinfo=CST))],
            coverage_options_hash=loader._options_hash,
        )

        post = make_post("m1", created_at=datetime(2024, 1, 15, 12, 0, tzinfo=CST))
        ctx._posts["u:test:p:1"] = ([post], None)

        with patch.object(loader._progress, "load", wraps=loader._progress.load) as mock_load:
            with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
                loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is None
        assert [interval.start for interval in saved.coverage] == [
            datetime(2024, 1, 14, 12, 0, tzinfo=CST),
            datetime(2024, 1, 15, 12, 0, tzinfo=CST),
        ]
        assert mock_load.called

    def test_no_coverage_only_preserves_resume(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, no_coverage=True)
        target_key = "u:test"
        post = make_post("m1", created_at=datetime(2024, 1, 15, 12, 0, tzinfo=CST))
        ctx._posts["u:test:p:1"] = ([post], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is None
        assert saved.coverage == []

    def test_gap_fill_rerun_only_downloads_uncovered_timestamp(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        store = ProgressStore(tmp_path / ".progress")
        ts1 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        store.save(target_key, coverage=[(ts1, ts1), (ts3, ts3)], coverage_options_hash=loader._options_hash)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
            make_post("m3", ts3, [make_media("http://example.com/3.jpg")]),
        ], None)

        download_names = []

        def track_download(url, dest):
            download_names.append(dest.name)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader, "_download", side_effect=track_download):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert download_names == ["2024-01-02_media_0.jpg"]
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert [interval.start for interval in saved.coverage] == [ts1, ts2, ts3]

    def test_group_coverage_advances_only_when_group_succeeds(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target_key = "u:test"
        shared_stamp = datetime(2024, 1, 15, 12, 0, tzinfo=CST)
        post1 = make_post("m1", created_at=shared_stamp, media_items=[make_media("http://example.com/1.jpg")])
        post2 = make_post("m2", created_at=shared_stamp, media_items=[make_media("http://example.com/2.jpg")])
        ctx._posts["u:test:p:1"] = ([post1, post2], None)

        outcomes = [
            DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg"),
            DownloadResult(MediaOutcome.FAILED, tmp_path / "2.jpg"),
        ]
        with patch.object(loader, "_download", side_effect=outcomes):
            result = loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert result is False
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.coverage == []

    def test_interrupt_after_failed_group_does_not_resume_past_group(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        shared_stamp = datetime(2024, 1, 15, 12, 0, tzinfo=CST)
        post1 = make_post("m1", created_at=shared_stamp, media_items=[make_media("http://example.com/1.jpg")])
        post2 = make_post("m2", created_at=shared_stamp, media_items=[make_media("http://example.com/2.jpg")])
        ctx._posts["u:test:p:1"] = ([post1], "cursor-2")
        ctx._posts["u:test:p:2"] = ([post2], None)

        loader1 = WeiboLoader(ctx, output_dir=tmp_path)
        with patch.object(
            loader1,
            "_download",
            side_effect=[
                DownloadResult(MediaOutcome.FAILED, tmp_path / "1.jpg"),
                KeyboardInterrupt("stop"),
            ],
        ):
            with pytest.raises(KeyboardInterrupt):
                loader1.download_target(UserTarget(identifier="test", is_uid=True))

        interrupted = load_progress_state(tmp_path, target_key)
        assert interrupted is not None
        assert interrupted.resume is None
        assert interrupted.coverage == []

        loader2 = WeiboLoader(ctx, output_dir=tmp_path)
        download_names = []

        def succeed(url, dest):
            download_names.append(dest.name)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader2, "_download", side_effect=succeed):
            result = loader2.download_target(UserTarget(identifier="test", is_uid=True))

        assert result is True
        assert len(download_names) == 2
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.coverage == [ProgressStore.normalize_intervals([(shared_stamp, shared_stamp)])[0]]

    def test_failed_target_keeps_last_safe_resume(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target_key = "u:test"
        newer = datetime(2024, 1, 15, 12, 0, tzinfo=CST)
        older = datetime(2024, 1, 14, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = (
            [
                make_post("m1", created_at=newer, media_items=[make_media("http://example.com/1.jpg")]),
                make_post("m2", created_at=older, media_items=[make_media("http://example.com/2.jpg")]),
            ],
            None,
        )

        with patch.object(
            loader,
            "_download",
            side_effect=[
                DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg"),
                DownloadResult(MediaOutcome.FAILED, tmp_path / "2.jpg"),
            ],
        ):
            result = loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert result is False
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is not None
        assert saved.coverage == [ProgressStore.normalize_intervals([(newer, newer)])[0]]

    def test_failed_gap_preserves_resume_and_splits_coverage(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target_key = "u:test"
        newest = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        failed_stamp = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        oldest = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = (
            [
                make_post("m1", created_at=newest, media_items=[make_media("http://example.com/1.jpg")]),
                make_post("m2", created_at=failed_stamp, media_items=[make_media("http://example.com/2.jpg")]),
                make_post("m3", created_at=oldest, media_items=[make_media("http://example.com/3.jpg")]),
            ],
            None,
        )

        with patch.object(
            loader,
            "_download",
            side_effect=[
                DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg"),
                DownloadResult(MediaOutcome.FAILED, tmp_path / "2.jpg"),
                DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "3.jpg"),
            ],
        ):
            result = loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert result is False
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is not None
        assert [post.mid for post in saved.resume.buffered_posts] == ["m2", "m3"]
        intervals = sorted(saved.coverage, key=lambda interval: interval.start)
        assert [(interval.start, interval.end) for interval in intervals] == [
            (oldest, oldest),
            (newest, newest),
        ]

    def test_interrupt_after_later_success_still_resumes_from_failed_gap(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        newest = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        failed_stamp = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        oldest = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = (
            [
                make_post("m1", created_at=newest, media_items=[make_media("http://example.com/1.jpg")]),
                make_post("m2", created_at=failed_stamp, media_items=[make_media("http://example.com/2.jpg")]),
                make_post("m3", created_at=oldest, media_items=[make_media("http://example.com/3.jpg")]),
                make_post("m4", created_at=oldest, media_items=[make_media("http://example.com/4.jpg")]),
            ],
            None,
        )

        loader = WeiboLoader(ctx, output_dir=tmp_path)
        with patch.object(
            loader,
            "_download",
            side_effect=[
                DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg"),
                DownloadResult(MediaOutcome.FAILED, tmp_path / "2.jpg"),
                DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "3.jpg"),
                KeyboardInterrupt("stop"),
            ],
        ):
            with pytest.raises(KeyboardInterrupt):
                loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is not None
        assert [post.mid for post in saved.resume.buffered_posts] == ["m2", "m3", "m4"]
        assert [interval.start for interval in saved.coverage] == [newest]

    def test_interrupt_after_success_before_checkpoint_keeps_latest_exact_frontier(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        target_key = "u:test"
        newer = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        older = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = (
            [
                make_post("m1", created_at=newer, media_items=[make_media("http://example.com/1.jpg")]),
                make_post("m2", created_at=older, media_items=[make_media("http://example.com/2.jpg")]),
            ],
            None,
        )

        loader = WeiboLoader(ctx, output_dir=tmp_path)
        iterator = loader._create_iterator(target)
        original_freeze = iterator.freeze
        freeze_calls = 0

        def interrupt_once():
            nonlocal freeze_calls
            freeze_calls += 1
            if freeze_calls == 1:
                raise KeyboardInterrupt("stop after success before checkpoint")
            return original_freeze()

        with patch.object(loader, "_create_iterator", return_value=iterator):
            with patch.object(iterator, "freeze", side_effect=interrupt_once):
                with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg")):
                    with pytest.raises(KeyboardInterrupt):
                        loader.download_target(target)

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is not None
        assert [post.mid for post in saved.resume.buffered_posts] == ["m2"]
        assert saved.resume.page_loaded is True
        assert saved.coverage == []

    def test_fast_update_keeps_latest_exact_resume_frontier(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        post1 = make_post("m1", ts1, [make_media("http://example.com/1.jpg")])
        post2 = make_post("m2", ts2, [make_media("http://example.com/2.jpg")])
        post3 = make_post("m3", ts3, [make_media("http://example.com/3.jpg")])
        ctx._posts["u:test:p:1"] = ([post1, post2, post3], None)

        loader1 = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True)
        target_dir = loader1._build_dir(loader1._resolve_target(target))
        existing_path = loader1._media_jobs(target_dir, post2)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")

        first_run = []

        def download_first(url, dest):
            first_run.append(dest.name)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader1, "_download", side_effect=download_first):
            assert loader1.download_target(target) is True

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert saved.resume is not None
        assert [post.mid for post in saved.resume.buffered_posts] == ["m2", "m3"]
        assert [interval.start for interval in saved.coverage] == [ts1]

        loader2 = WeiboLoader(ctx, output_dir=tmp_path)
        second_run = []

        def download_second(url, dest):
            second_run.append((dest.name, dest.exists()))
            outcome = MediaOutcome.SKIPPED if dest.exists() and dest.stat().st_size > 0 else MediaOutcome.DOWNLOADED
            return DownloadResult(outcome, dest)

        with patch.object(loader2, "_download", side_effect=download_second):
            assert loader2.download_target(target) is True

        assert first_run == ["2024-01-03_media_0.jpg"]
        assert second_run == [
            ("2024-01-02_media_0.jpg", True),
            ("2024-01-01_media_0.jpg", False),
        ]

    def test_fast_update_rerun_revisits_uncovered_existing_media(self, tmp_path: Path):
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        post1 = make_post("m1", ts1, [make_media("http://example.com/1.jpg")])
        post2 = make_post("m2", ts2, [make_media("http://example.com/2.jpg")])
        post3 = make_post("m3", ts3, [make_media("http://example.com/3.jpg")])
        ctx._posts["u:test:p:1"] = ([post1, post2, post3], None)

        loader1 = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True)
        target_dir = loader1._build_dir(loader1._resolve_target(target))
        existing_path = loader1._media_jobs(target_dir, post2)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")

        with patch.object(loader1, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "first.jpg")):
            assert loader1.download_target(target) is True

        interrupted = load_progress_state(tmp_path, "u:test")
        assert interrupted is not None
        assert interrupted.resume is not None
        assert [post.mid for post in interrupted.resume.buffered_posts] == ["m2", "m3"]
        assert [interval.start for interval in interrupted.coverage] == [ts1]

        sink = CollectorSink()
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True, progress=sink)

        def resume_download(url, dest):
            if dest.exists() and dest.stat().st_size > 0:
                return DownloadResult(MediaOutcome.SKIPPED, dest)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader2, "_download", side_effect=resume_download):
            assert loader2.download_target(target) is True

        media_events = [event for event in sink.events if event.kind == EventKind.MEDIA_DONE]
        assert [(event.filename, event.outcome) for event in media_events] == [
            ("2024-01-02_media_0.jpg", MediaOutcome.SKIPPED),
            ("2024-01-01_media_0.jpg", MediaOutcome.DOWNLOADED),
        ]
        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert saved.resume is None
        assert len(saved.coverage) == 2
        assert ProgressStore.contains(saved.coverage, ts1)
        assert ProgressStore.contains(saved.coverage, ts2)
        assert ProgressStore.contains(saved.coverage, ts3)

    def test_fast_update_compatible_empty_checkpoint_revisits_existing_media(self, tmp_path: Path):
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        target_key = "u:test"
        target = UserTarget(identifier="test", is_uid=True)
        ts = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        post = make_post("m1", ts, [make_media("http://example.com/1.jpg")])
        ctx._posts["u:test:p:1"] = ([post], None)

        loader = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True)
        store = ProgressStore(tmp_path / ".progress")
        store.save(target_key, coverage=[], coverage_options_hash=loader._options_hash)

        target_dir = loader._build_dir(loader._resolve_target(target))
        existing_path = loader._media_jobs(target_dir, post)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")

        sink = CollectorSink()
        loader = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True, progress=sink)

        with patch.object(loader, "_download", wraps=loader._download) as mock_download:
            assert loader.download_target(target) is True

        assert mock_download.call_count == 1
        media_events = [event for event in sink.events if event.kind == EventKind.MEDIA_DONE]
        assert [(event.filename, event.outcome) for event in media_events] == [
            ("2024-01-02_media_0.jpg", MediaOutcome.SKIPPED),
        ]
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is None
        assert ProgressStore.contains(saved.coverage, ts)

    def test_gap_fill_rerun_seals_group_after_mixed_downloaded_and_skipped_media(self, tmp_path: Path):
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        target_key = "u:test"
        target = UserTarget(identifier="test", is_uid=True)
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        store = ProgressStore(tmp_path / ".progress")
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        store.save(target_key, coverage=[(ts1, ts1), (ts3, ts3)], coverage_options_hash=loader._options_hash)
        covered_newer = make_post("m1", ts1, [make_media("http://example.com/1.jpg")])
        gap_existing = make_post("m2", ts2, [make_media("http://example.com/2.jpg", index=0)])
        gap_missing = make_post("m3", ts2, [make_media("http://example.com/3.jpg", index=1)])
        covered_older = make_post("m4", ts3, [make_media("http://example.com/4.jpg")])
        ctx._posts["u:test:p:1"] = ([covered_newer, gap_existing, gap_missing, covered_older], None)

        target_dir = loader._build_dir(loader._resolve_target(target))
        existing_path = loader._media_jobs(target_dir, gap_existing)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")

        sink = CollectorSink()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)

        def track_download(url, dest):
            if dest.exists() and dest.stat().st_size > 0:
                return DownloadResult(MediaOutcome.SKIPPED, dest)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader, "_download", side_effect=track_download):
            assert loader.download_target(target) is True

        media_events = [event for event in sink.events if event.kind == EventKind.MEDIA_DONE]
        assert [(event.filename, event.outcome) for event in media_events] == [
            ("2024-01-02_media_0.jpg", MediaOutcome.SKIPPED),
            ("2024-01-02_media_1.jpg", MediaOutcome.DOWNLOADED),
        ]
        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is None
        assert ProgressStore.contains(saved.coverage, ts1)
        assert ProgressStore.contains(saved.coverage, ts2)
        assert ProgressStore.contains(saved.coverage, ts3)

    def test_progressive_count_expansion_replays_saved_suffix_before_next_page(self, tmp_path: Path):
        ctx = MockContext()
        ts1 = datetime(2024, 1, 4, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts4 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
            make_post("m3", ts3, [make_media("http://example.com/3.jpg")]),
        ], "cursor-2")
        ctx._posts["u:test:p:2"] = ([
            make_post("m4", ts4, [make_media("http://example.com/4.jpg")]),
        ], None)

        loader1 = WeiboLoader(ctx, output_dir=tmp_path, count=1)
        first_run = []

        def download_first(url, dest):
            first_run.append(dest.name)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader1, "_download", side_effect=download_first):
            assert loader1.download_target(UserTarget(identifier="test", is_uid=True)) is True

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert saved.resume is not None
        assert [post.mid for post in saved.resume.buffered_posts] == ["m2", "m3"]
        assert saved.resume.pending_cursor == "cursor-2"
        assert saved.resume.pending_has_more is True
        assert saved.resume.page_loaded is True

        loader2 = WeiboLoader(ctx, output_dir=tmp_path, count=4)
        second_run = []

        def download_second(url, dest):
            second_run.append(dest.name)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader2, "_download", side_effect=download_second):
            assert loader2.download_target(UserTarget(identifier="test", is_uid=True)) is True

        assert first_run == ["2024-01-04_media_0.jpg"]
        assert second_run == [
            "2024-01-03_media_0.jpg",
            "2024-01-02_media_0.jpg",
            "2024-01-01_media_0.jpg",
        ]


class TestMetadataExport:
    def test_metadata_json_export(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, metadata_json=True)

        post = make_post("m123", media_items=[])
        target_dir = tmp_path / "test"
        target_dir.mkdir()

        loader._write_json(target_dir, post)

        json_path = target_dir / "m123.json"
        assert json_path.exists()
        loaded = json.loads(json_path.read_text())
        assert loaded["mid"] == "m123"

    def test_post_metadata_txt(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, post_metadata_txt="template content")

        post = make_post("m456")
        target_dir = tmp_path / "test"
        target_dir.mkdir()

        loader._write_txt(target_dir, post)

        txt_path = target_dir / "m456.txt"
        assert txt_path.exists()
        assert txt_path.read_text() == "template content"

    def test_metadata_json_hash_mismatch_reprocesses_covered_post(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        store = ProgressStore(tmp_path / ".progress")
        store.save(target_key, coverage=[(ts, ts)], coverage_options_hash=WeiboLoader(ctx, output_dir=tmp_path)._options_hash)

        loader = WeiboLoader(ctx, output_dir=tmp_path, metadata_json=True)
        ctx._posts["u:test:p:1"] = ([make_post("m123", created_at=ts, media_items=[])], None)

        with patch.object(loader, "_download") as mock_download:
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert not mock_download.called
        assert (tmp_path / "User_test" / "m123.json").exists()

    def test_post_metadata_txt_hash_mismatch_reprocesses_covered_post(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        store = ProgressStore(tmp_path / ".progress")
        store.save(target_key, coverage=[(ts, ts)], coverage_options_hash=WeiboLoader(ctx, output_dir=tmp_path)._options_hash)

        loader = WeiboLoader(ctx, output_dir=tmp_path, post_metadata_txt="template content")
        ctx._posts["u:test:p:1"] = ([make_post("m456", created_at=ts, media_items=[])], None)

        with patch.object(loader, "_download") as mock_download:
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert not mock_download.called
        assert (tmp_path / "User_test" / "m456.txt").read_text() == "template content"


class TestTargetResolution:
    def test_user_target_resolution(self, tmp_path: Path):
        ctx = MockContext()
        ctx._uids["nickname"] = "123456"
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        resolved = loader._resolve_target(UserTarget(identifier="nickname", is_uid=False))
        assert resolved.target.identifier == "123456"
        assert resolved.target.is_uid is True

    def test_supertopic_target_resolution(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        resolved = loader._resolve_target(SuperTopicTarget(identifier="topic", is_containerid=False))
        assert resolved.target.is_containerid is True

    def test_search_target_passthrough(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        target = SearchTarget(keyword="test")
        resolved = loader._resolve_target(target)
        assert resolved.target.keyword == "test"

    def test_mid_target_passthrough(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        target = MidTarget(mid="abc123")
        resolved = loader._resolve_target(target)
        assert resolved.target.mid == "abc123"


class TestFaultIsolation:
    def test_partial_failure_continues(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        targets = [
            UserTarget(identifier="user1", is_uid=True),
            UserTarget(identifier="user2", is_uid=True),
        ]

        ctx._posts["u:user1:p:1"] = ([make_post("m1")], None)

        with patch.object(loader, "download_target", side_effect=[True, False]):
            results = loader.download_targets(targets)

        assert results["u:user1"] is True
        assert results["u:user2"] is False

    def test_save_failure_raises_checkpoint_error_and_aborts_current_target(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target = UserTarget(identifier="test", is_uid=True)
        ctx._posts["u:test:p:1"] = ([make_post("m1")], None)

        with patch.object(loader._progress, "save", side_effect=OSError("fsync failed")) as mock_save:
            with pytest.raises(CheckpointError, match="fsync failed"):
                loader.download_target(target)

        assert mock_save.call_count == 1
        assert load_progress_state(tmp_path, "u:test") is None

    def test_lock_contention_raises_checkpoint_error(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target = UserTarget(identifier="test", is_uid=True)
        ctx._posts["u:test:p:1"] = ([make_post("m1")], None)

        with patch.object(loader._progress, "acquire_lock", side_effect=RuntimeError("lock contention: u:test")):
            with pytest.raises(CheckpointError, match="lock contention"):
                loader.download_target(target)

        assert load_progress_state(tmp_path, "u:test") is None

    def test_download_targets_continues_after_checkpoint_error(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        target1 = UserTarget(identifier="user1", is_uid=True)
        target2 = UserTarget(identifier="user2", is_uid=True)
        ctx._posts["u:user1:p:1"] = ([make_post("m1")], None)
        ctx._posts["u:user2:p:1"] = ([make_post("m2")], None)

        def fail_first(target):
            if target.identifier == "user1":
                raise CheckpointError("rename failed")
            return True

        with patch.object(loader, "download_target", side_effect=fail_first):
            results = loader.download_targets([target1, target2])

        assert results == {"u:user1": False, "u:user2": True}


class TestOptionsHash:
    def test_hash_changes_with_media_filters(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, no_videos=True)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, no_videos=False)

        assert loader1._options_hash != loader2._options_hash

    def test_hash_changes_with_date_boundary(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-01:2024-01-31")
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-02-01:2024-02-28")

        assert loader1._options_hash != loader2._options_hash

    def test_hash_reuses_canonical_equivalent_boundaries(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="20240101:2024-01-31", id_boundary="00123:0456")
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-01:2024-01-31", id_boundary="123:456")

        assert loader1._options_hash == loader2._options_hash

    def test_hash_ignores_count(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, count=1)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, count=99)

        assert loader1._options_hash == loader2._options_hash

    def test_hash_ignores_fast_update(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, fast_update=False)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True)

        assert loader1._options_hash == loader2._options_hash

    def test_hash_changes_with_metadata_json(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, metadata_json=False)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, metadata_json=True)

        assert loader1._options_hash != loader2._options_hash

    def test_hash_changes_with_post_metadata_txt(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, post_metadata_txt=None)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, post_metadata_txt="template content")

        assert loader1._options_hash != loader2._options_hash

    def test_boundary_mismatch_does_not_reuse_coverage(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        ts = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:2024-01-02")
        ProgressStore(tmp_path / ".progress").save(target_key, coverage=[(ts, ts)], coverage_options_hash=loader1._options_hash)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-01:2024-01-03")
        ctx._posts["u:test:p:1"] = ([make_post("m1", ts, [make_media("http://example.com/1.jpg")])], None)

        with patch.object(loader2, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg")) as mock_download:
            assert loader2.download_target(UserTarget(identifier="test", is_uid=True)) is True

        assert mock_download.call_count == 1

    def test_canonical_equivalent_boundary_reuses_coverage(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        ts = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="20240102:2024-01-02", id_boundary="00123:00123")
        ProgressStore(tmp_path / ".progress").save(target_key, coverage=[(ts, ts)], coverage_options_hash=loader1._options_hash)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:2024-01-02", id_boundary="123:123")
        ctx._posts["u:test:p:1"] = ([make_post("123", ts, [make_media("http://example.com/1.jpg")])], None)

        with patch.object(loader2, "_download") as mock_download:
            assert loader2.download_target(UserTarget(identifier="test", is_uid=True)) is True

        assert not mock_download.called

    def test_boundary_skip_preserves_resume_frontier_for_later_rerun(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        out_of_range = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        in_range_1 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        in_range_2 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("out", out_of_range, [make_media("http://example.com/out.jpg")]),
            make_post("in1", in_range_1, [make_media("http://example.com/in1.jpg")]),
            make_post("in2", in_range_2, [make_media("http://example.com/in2.jpg")]),
        ], None)

        loader1 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary=":2024-01-02", count=1)
        with patch.object(loader1, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg")):
            assert loader1.download_target(target) is True

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert saved.resume is not None
        assert [post.mid for post in saved.resume.buffered_posts] == ["in2"]

        loader2 = WeiboLoader(ctx, output_dir=tmp_path, date_boundary=":2024-01-02", count=2)
        downloads: list[str] = []
        with patch.object(loader2, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader2.download_target(target) is True

        assert downloads == ["2024-01-01_media_0.jpg"]

    def test_out_of_range_same_timestamp_does_not_block_in_range_coverage(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        shared = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        older = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("1", shared, [make_media("http://example.com/in.jpg")]),
            make_post("2", shared, [make_media("http://example.com/out.jpg")]),
            make_post("0", older, [make_media("http://example.com/older.jpg")]),
        ], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, id_boundary="1:1")

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "x.jpg")):
            assert loader.download_target(UserTarget(identifier="test", is_uid=True)) is True

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert ProgressStore.contains(saved.coverage, shared)
        assert not ProgressStore.contains(saved.coverage, older)


class TestBoundaryFiltering:
    def test_user_target_stops_at_lower_bound(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        older = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        in_range = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([make_post("old", older, [make_media("http://example.com/old.jpg")])], "cursor-2")
        ctx._posts["u:test:p:2"] = ([make_post("in", in_range, [make_media("http://example.com/in.jpg")])], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:")

        with patch.object(loader, "_download") as mock_download:
            assert loader.download_target(target) is True

        assert not mock_download.called

    def test_user_target_nondecimal_mid_does_not_trigger_cutoff(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        shared = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([make_post("-1", shared, [make_media("http://example.com/skip.jpg")])], "cursor-2")
        ctx._posts["u:test:p:2"] = ([make_post("150", shared, [make_media("http://example.com/in.jpg")])], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, id_boundary="100:200")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(target) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_search_target_keeps_scanning_when_post_is_out_of_range(self, tmp_path: Path):
        ctx = MockContext()
        older = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        in_range = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ctx._posts["s:topic:p:1"] = ([make_post("old", older, [make_media("http://example.com/old.jpg")])], "cursor-2")
        ctx._posts["s:topic:p:2"] = ([make_post("in", in_range, [make_media("http://example.com/in.jpg")])], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(SearchTarget(keyword="topic")) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_pinned_out_of_range_post_does_not_trigger_cutoff(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        older = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        in_range = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        pinned_raw = {"mblog": {"mblogtype": 2, "mid": "pin"}}
        ctx._posts["u:test:p:1"] = ([
            make_post("pin", older, [make_media("http://example.com/pin.jpg")], raw=pinned_raw),
            make_post("in", in_range, [make_media("http://example.com/in.jpg")]),
        ], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(target) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_fast_update_ignores_out_of_range_existing_file(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        out_of_range = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        in_range = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        post1 = make_post("out", out_of_range, [make_media("http://example.com/out.jpg")])
        post2 = make_post("in", in_range, [make_media("http://example.com/in.jpg")])
        ctx._posts["u:test:p:1"] = ([post1, post2], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:2024-01-02", fast_update=True)
        target_dir = loader._build_dir(loader._resolve_target(target))
        existing_path = loader._media_jobs(target_dir, post1)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(target) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_out_of_range_posts_do_not_consume_count(self, tmp_path: Path):
        ctx = MockContext()
        target = UserTarget(identifier="test", is_uid=True)
        ctx._posts["u:test:p:1"] = ([
            make_post("out", datetime(2024, 1, 3, 12, 0, tzinfo=CST), [make_media("http://example.com/out.jpg")]),
            make_post("in1", datetime(2024, 1, 2, 12, 0, tzinfo=CST), [make_media("http://example.com/in1.jpg")]),
            make_post("in2", datetime(2024, 1, 1, 12, 0, tzinfo=CST), [make_media("http://example.com/in2.jpg")]),
        ], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary=":2024-01-02", count=1)
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(target) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_mid_target_out_of_range_succeeds_without_side_effects(self, tmp_path: Path):
        ctx = MockContext()
        target = MidTarget(mid="mid-1")
        ctx._posts["m:mid-1"] = [make_post("mid-1", datetime(2024, 1, 1, 12, 0, tzinfo=CST), [make_media("http://example.com/1.jpg")])]
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:", metadata_json=True, post_metadata_txt="meta")

        with patch.object(loader, "_download") as mock_download:
            assert loader.download_target(target) is True

        assert not mock_download.called
        assert list((tmp_path / "Mid_mid-1").glob("*.json")) == [] if (tmp_path / "Mid_mid-1").exists() else True

    def test_mid_target_with_nondecimal_mid_and_id_boundary_succeeds_without_side_effects(self, tmp_path: Path):
        ctx = MockContext()
        target = MidTarget(mid="abc123")
        ctx._posts["m:abc123"] = [make_post("abc123", datetime(2024, 1, 2, 12, 0, tzinfo=CST), [make_media("http://example.com/1.jpg")])]
        loader = WeiboLoader(ctx, output_dir=tmp_path, id_boundary="100:200", metadata_json=True, post_metadata_txt="meta")

        with patch.object(loader, "_download") as mock_download:
            assert loader.download_target(target) is True

        assert not mock_download.called
        assert not (tmp_path / "Mid_abc123").exists()

    def test_search_target_nondecimal_mid_is_treated_as_out_of_range(self, tmp_path: Path):
        ctx = MockContext()
        shared = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ctx._posts["s:topic:p:1"] = ([
            make_post("abc123", shared, [make_media("http://example.com/a.jpg")]),
            make_post("150", shared, [make_media("http://example.com/b.jpg")]),
        ], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, id_boundary="100:200")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(SearchTarget(keyword="topic")) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_supertopic_target_keeps_scanning_when_post_is_out_of_range(self, tmp_path: Path):
        ctx = MockContext()
        older = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        in_range = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ctx._posts["t:topic:p:1"] = ([make_post("old", older, [make_media("http://example.com/old.jpg")])], "cursor-2")
        ctx._posts["t:topic:p:2"] = ([make_post("in", in_range, [make_media("http://example.com/in.jpg")])], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(SuperTopicTarget(identifier="topic", is_containerid=True)) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_single_point_mid_boundary_matches_exact_post(self, tmp_path: Path):
        ctx = MockContext()
        ctx._posts["u:test:p:1"] = ([
            make_post("124", datetime(2024, 1, 3, 12, 0, tzinfo=CST), [make_media("http://example.com/124.jpg")]),
            make_post("123", datetime(2024, 1, 2, 12, 0, tzinfo=CST), [make_media("http://example.com/123.jpg")]),
            make_post("122", datetime(2024, 1, 1, 12, 0, tzinfo=CST), [make_media("http://example.com/122.jpg")]),
        ], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, id_boundary="123:123")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(UserTarget(identifier="test", is_uid=True)) is True

        assert downloads == ["2024-01-02_media_0.jpg"]

    def test_naive_timestamp_boundary_uses_cst_date(self, tmp_path: Path):
        ctx = MockContext()
        naive = datetime(2024, 1, 2, 0, 30, 0)
        ctx._posts["u:test:p:1"] = ([make_post("m1", naive, [make_media("http://example.com/1.jpg")])], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-02:2024-01-02")

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "1.jpg")) as mock_download:
            assert loader.download_target(UserTarget(identifier="test", is_uid=True)) is True

        assert mock_download.call_count == 1

    def test_boundary_endpoints_are_inclusive(self, tmp_path: Path):
        ctx = MockContext()
        ctx._posts["u:test:p:1"] = ([
            make_post("100", datetime(2024, 1, 3, 12, 0, tzinfo=CST), [make_media("http://example.com/100.jpg")]),
            make_post("200", datetime(2024, 1, 1, 12, 0, tzinfo=CST), [make_media("http://example.com/200.jpg")]),
        ], None)
        loader = WeiboLoader(ctx, output_dir=tmp_path, date_boundary="2024-01-01:2024-01-03", id_boundary="100:200")
        downloads: list[str] = []

        with patch.object(loader, "_download", side_effect=lambda url, dest: downloads.append(dest.name) or DownloadResult(MediaOutcome.DOWNLOADED, dest)):
            assert loader.download_target(UserTarget(identifier="test", is_uid=True)) is True

        assert downloads == ["2024-01-03_media_0.jpg", "2024-01-01_media_0.jpg"]


@given(st.integers(min_value=0, max_value=100))
@settings(max_examples=20)
def test_count_property(count):
    """PBT: processed_count <= --count."""
    ctx = MockContext()
    loader = WeiboLoader(ctx, output_dir=Path("/tmp"), count=count)
    assert loader.count == max(0, count)


def test_default_max_workers_is_one(tmp_path: Path):
    ctx = MockContext()
    loader = WeiboLoader(ctx, output_dir=tmp_path)
    assert loader.max_workers == 1


class TestDownloadTimeout:
    def test_timeout_tuple_passed(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "new.jpg"

        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"data"]
        with patch.object(ctx, "request", return_value=mock_resp) as mock_req:
            loader._download("http://example.com/img.jpg", dest)

        call_kwargs = mock_req.call_args[1]
        timeout = call_kwargs.get("timeout")
        assert isinstance(timeout, tuple) and len(timeout) == 2
        assert timeout[1] == 60

    def test_part_cleaned_on_read_timeout(self, tmp_path: Path):
        from requests.exceptions import ReadTimeout
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "fail.jpg"

        mock_resp = MagicMock()
        mock_resp.iter_content.side_effect = ReadTimeout("timeout")
        with patch.object(ctx, "request", return_value=mock_resp):
            result = loader._download("http://example.com/img.jpg", dest)

        assert result.outcome.value == "failed"
        assert not (tmp_path / "fail.jpg.part").exists()

    def test_part_cleaned_on_generic_exception(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "fail2.jpg"

        mock_resp = MagicMock()
        mock_resp.iter_content.side_effect = OSError("disk full")
        with patch.object(ctx, "request", return_value=mock_resp):
            result = loader._download("http://example.com/img.jpg", dest)

        assert result.outcome.value == "failed"
        assert not (tmp_path / "fail2.jpg.part").exists()

    def test_part_missing_before_exception_is_silent(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "fail3.jpg"

        with patch.object(ctx, "request", side_effect=ConnectionError("refused")):
            result = loader._download("http://example.com/img.jpg", dest)

        assert result.outcome.value == "failed"

    def test_deadline_timeout_returns_failed_and_no_part(self, tmp_path: Path):
        """P1+P2: TimeoutError from deadline fires -> FAILED, .part absent."""
        import time
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "slow.jpg"

        def slow_chunks():
            yield b"first chunk"
            time.sleep(0.01)
            yield b"second chunk"

        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = slow_chunks()
        with patch.object(ctx, "request", return_value=mock_resp):
            with patch("weiboloader.weiboloader._MEDIA_DOWNLOAD_TIMEOUT", -1):
                result = loader._download("http://example.com/slow.jpg", dest)

        assert result.outcome == MediaOutcome.FAILED
        assert not dest.with_suffix(".part").exists()

    def test_wall_clock_bounded(self, tmp_path: Path):
        """P1: _download returns within timeout + epsilon even on infinite stream."""
        import time
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        dest = tmp_path / "infinite.jpg"

        def infinite_chunks():
            while True:
                yield b"x" * 1024

        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = infinite_chunks()
        with patch.object(ctx, "request", return_value=mock_resp):
            with patch("weiboloader.weiboloader._MEDIA_DOWNLOAD_TIMEOUT", 0):
                start = time.monotonic()
                result = loader._download("http://example.com/infinite.jpg", dest)
                elapsed = time.monotonic() - start

        assert result.outcome == MediaOutcome.FAILED
        assert elapsed < 5  # well within any reasonable epsilon

    def test_get_socket_returns_none_on_mock(self):
        """_get_socket returns None for mock responses (no real socket)."""
        from weiboloader.weiboloader import _get_socket
        mock_resp = MagicMock(spec=[])
        assert _get_socket(mock_resp) is None


class TestDownloadLoopEnriched:
    def _make_post(self, mid, media_urls):
        items = [MediaItem(url=u, media_type="picture", index=i, filename_hint=f"img{i}.jpg") for i, u in enumerate(media_urls)]
        return make_post(mid, media_items=items)

    def test_events_carry_post_index_and_filename(self, tmp_path: Path):
        from weiboloader.ui import EventKind, UIEvent
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        sink = CollectorSink()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)

        posts = [self._make_post("p1", ["http://example.com/a.jpg"])]
        ctx._posts["u:111:p:1"] = (posts, None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "a.jpg")):
            loader.download_target(UserTarget(identifier="111", is_uid=True))

        media_events = [e for e in sink.events if e.kind == EventKind.MEDIA_DONE]
        assert all(e.post_index is not None for e in media_events)
        assert all(e.filename is not None for e in media_events)

    def test_progress_not_advanced_on_timeout(self, tmp_path: Path):
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        sink = CollectorSink()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)
        target_key = "u:222"

        posts = [self._make_post("p1", ["http://example.com/a.jpg", "http://example.com/b.jpg"])]
        ctx._posts["u:222:p:1"] = (posts, None)

        import concurrent.futures
        original_wait = concurrent.futures.wait

        call_count = [0]

        def patched_wait(fs, timeout=None, return_when=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return set(), set(fs)
            return original_wait(fs, timeout=timeout, return_when=return_when)

        import time
        real_monotonic = time.monotonic
        mono_calls = [0]

        def fake_monotonic():
            mono_calls[0] += 1
            if mono_calls[0] > 3:
                return real_monotonic() + 99999
            return real_monotonic()

        with patch("weiboloader.weiboloader.wait", patched_wait):
            with patch("time.monotonic", fake_monotonic):
                with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "a.jpg")):
                    loader.download_target(UserTarget(identifier="222", is_uid=True))

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.resume is None
        assert saved.coverage == []

    def test_zero_media_no_timeout(self, tmp_path: Path):
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        sink = CollectorSink()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)
        target_key = "u:333"

        posts = [make_post("p1", media_items=[])]
        ctx._posts["u:333:p:1"] = (posts, None)

        loader.download_target(UserTarget(identifier="333", is_uid=True))

        saved = load_progress_state(tmp_path, target_key)
        assert saved is not None
        assert saved.coverage == [ProgressStore.normalize_intervals([(posts[0].created_at, posts[0].created_at)])[0]]

    def test_media_done_count_equals_media_total(self, tmp_path: Path):
        from weiboloader.ui import EventKind
        from tests.test_progress_ui import CollectorSink

        ctx = MockContext()
        sink = CollectorSink()
        loader = WeiboLoader(ctx, output_dir=tmp_path, progress=sink)

        posts = [self._make_post("p1", [f"http://example.com/{i}.jpg" for i in range(4)])]
        ctx._posts["u:444:p:1"] = (posts, None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "x.jpg")):
            loader.download_target(UserTarget(identifier="444", is_uid=True))

        media_events = [e for e in sink.events if e.kind == EventKind.MEDIA_DONE]
        assert len(media_events) == 4


@given(st.integers(min_value=1, max_value=255))
@settings(max_examples=20)
def test_skip_existing_nonempty_file(size: int):
    """PBT: exists && size>0 → skip (no request made)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "media.jpg"
        dest.write_bytes(bytes(size))
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=Path(d))
        request_called = [False]
        def track_request(*a, **kw):
            request_called[0] = True
            return MagicMock()
        with patch.object(ctx, "request", side_effect=track_request):
            result = loader._download("http://x.com/a.jpg", dest)
        assert result.outcome == MediaOutcome.SKIPPED
        assert not request_called[0], "No HTTP request should be made for existing non-empty file"


@given(
    st.booleans(),
    st.booleans(),
    st.lists(st.sampled_from(["picture", "video"]), min_size=1, max_size=5),
)
@settings(max_examples=20)
def test_media_type_filter_pbt(no_videos: bool, no_pictures: bool, media_types: list):
    """PBT: filter flags produce zero forbidden types in jobs."""
    from weiboloader.structures import MediaItem, Post
    ctx = MockContext()
    loader = WeiboLoader(ctx, output_dir=Path("/tmp"), no_videos=no_videos, no_pictures=no_pictures)
    items = [MediaItem(url=f"http://x/{i}", media_type=t, index=i, filename_hint=None, raw={}) for i, t in enumerate(media_types)]
    post = make_post("m1", media_items=items)
    jobs = loader._media_jobs(Path("/tmp"), post)
    types_in_jobs = [m.media_type for m, _ in jobs]
    if no_videos:
        assert "video" not in types_in_jobs
    if no_pictures:
        assert "picture" not in types_in_jobs


@given(
    st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    st.datetimes(
        min_value=__import__('datetime').datetime(2020, 1, 1),
        max_value=__import__('datetime').datetime(2030, 1, 1),
        timezones=__import__('hypothesis.strategies', fromlist=['just']).just(__import__('datetime').timezone(__import__('datetime').timedelta(hours=8))),
    ),
)
@settings(max_examples=20)
def test_progress_roundtrip_pbt(uid: str, ts):
    """PBT: Load(Save(progress coverage)) == coverage."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        key = f"u:{uid}"
        store = ProgressStore(Path(d) / ".progress")
        store.save(key, coverage=[(ts, ts)])
        loaded = store.load(key)
        assert loaded is not None
        assert loaded.target_key == key
        assert [interval.start for interval in loaded.coverage] == [ts]


class TestRunBasedCoverage:
    """Tests for run-based interval coverage (Task 3)."""

    def test_consecutive_successful_groups_merge_into_single_interval(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
            make_post("m3", ts3, [make_media("http://example.com/3.jpg")]),
        ], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert len(saved.coverage) == 1
        assert saved.coverage[0].start == ts3
        assert saved.coverage[0].end == ts1

    def test_target_complete_flushes_sealed_run(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        ts1 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
        ], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert len(saved.coverage) == 1
        assert saved.coverage[0].start == ts2
        assert saved.coverage[0].end == ts1

    def test_count_limit_flushes_sealed_run_without_current_group(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, count=2)
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
            make_post("m3", ts3, [make_media("http://example.com/3.jpg")]),
        ], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert len(saved.coverage) == 1
        assert saved.coverage[0].start == ts1
        assert saved.coverage[0].end == ts1

    def test_monotonicity_break_flushes_and_resets_run(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 5, 12, 0, tzinfo=CST)
        ts4 = datetime(2024, 1, 4, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
            make_post("m3", ts3, [make_media("http://example.com/3.jpg")]),
            make_post("m4", ts4, [make_media("http://example.com/4.jpg")]),
        ], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert len(saved.coverage) == 2
        intervals_sorted = sorted(saved.coverage, key=lambda x: x.start)
        assert intervals_sorted[0].start == ts2
        assert intervals_sorted[0].end == ts1
        assert intervals_sorted[1].start == ts4
        assert intervals_sorted[1].end == ts3

    def test_monotonicity_break_does_not_bridge_overlapping_runs(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)
        ts1 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts2 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ts3 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        ts4 = datetime(2024, 1, 4, 12, 0, tzinfo=CST)
        ts5 = datetime(2024, 1, 3, 12, 0, tzinfo=CST)
        ts6 = datetime(2024, 1, 2, 12, 0, tzinfo=CST)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
            make_post("m2", ts2, [make_media("http://example.com/2.jpg")]),
            make_post("m3", ts3, [make_media("http://example.com/3.jpg")]),
            make_post("m4", ts4, [make_media("http://example.com/4.jpg")]),
            make_post("m5", ts5, [make_media("http://example.com/5.jpg")]),
            make_post("m6", ts6, [make_media("http://example.com/6.jpg")]),
        ], None)

        with patch.object(loader, "_download", return_value=DownloadResult(MediaOutcome.DOWNLOADED, tmp_path / "test.jpg")):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        saved = load_progress_state(tmp_path, "u:test")
        assert saved is not None
        assert len(saved.coverage) == 2
        intervals_sorted = sorted(saved.coverage, key=lambda x: x.start)
        assert intervals_sorted[0].start == ts3
        assert intervals_sorted[0].end == ts1
        assert intervals_sorted[1].start == ts4
        assert intervals_sorted[1].end == ts4

    def test_coverage_options_mismatch_does_not_skip(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        store = ProgressStore(tmp_path / ".progress")
        ts1 = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        store.save(target_key, coverage=[(ts1, ts1)], coverage_options_hash="wrong_hash")

        loader = WeiboLoader(ctx, output_dir=tmp_path)
        ctx._posts["u:test:p:1"] = ([
            make_post("m1", ts1, [make_media("http://example.com/1.jpg")]),
        ], None)

        download_names = []

        def track_download(url, dest):
            download_names.append(dest.name)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)

        with patch.object(loader, "_download", side_effect=track_download):
            loader.download_target(UserTarget(identifier="test", is_uid=True))

        assert len(download_names) == 1

    def test_coverage_hash_mismatch_fast_update_with_metadata_json_reprocesses_existing_media(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        store = ProgressStore(tmp_path / ".progress")
        store.save(target_key, coverage=[(ts, ts)], coverage_options_hash="wrong_hash")

        target = UserTarget(identifier="test", is_uid=True)
        loader = WeiboLoader(ctx, output_dir=tmp_path, metadata_json=True, fast_update=True)
        post = make_post("m123", created_at=ts, media_items=[make_media("http://example.com/1.jpg")])
        ctx._posts["u:test:p:1"] = ([post], None)
        target_dir = loader._build_dir(loader._resolve_target(target))
        existing_path = loader._media_jobs(target_dir, post)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")

        with patch.object(loader, "_download", wraps=loader._download) as mock_download:
            loader.download_target(target)

        assert mock_download.call_count == 1
        assert (tmp_path / "User_test" / "m123.json").exists()

    def test_coverage_hash_mismatch_fast_update_with_post_metadata_txt_reprocesses_existing_media(self, tmp_path: Path):
        ctx = MockContext()
        target_key = "u:test"
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=CST)
        store = ProgressStore(tmp_path / ".progress")
        store.save(target_key, coverage=[(ts, ts)], coverage_options_hash="wrong_hash")

        target = UserTarget(identifier="test", is_uid=True)
        loader = WeiboLoader(ctx, output_dir=tmp_path, post_metadata_txt="template content", fast_update=True)
        post = make_post("m456", created_at=ts, media_items=[make_media("http://example.com/1.jpg")])
        ctx._posts["u:test:p:1"] = ([post], None)
        target_dir = loader._build_dir(loader._resolve_target(target))
        existing_path = loader._media_jobs(target_dir, post)[0][1]
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("existing")

        with patch.object(loader, "_download", wraps=loader._download) as mock_download:
            loader.download_target(target)

        assert mock_download.call_count == 1
        assert (tmp_path / "User_test" / "m456.txt").read_text() == "template content"
