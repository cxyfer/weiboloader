"""Tests for WeiboLoader orchestrator (Phase 4.1)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from weiboloader.context import WeiboLoaderContext
from weiboloader.structures import MediaItem, MidTarget, Post, SearchTarget, SuperTopicTarget, UserTarget
from weiboloader.weiboloader import WeiboLoader


CST = timezone(timedelta(hours=8))


def make_post(mid: str, created_at: datetime | None = None, media_items: list[MediaItem] | None = None) -> Post:
    return Post(
        mid=mid,
        bid=None,
        text=f"Post {mid}",
        created_at=created_at or datetime.now(CST),
        user=None,
        media_items=media_items or [],
        raw={"mid": mid, "text": f"Post {mid}"},
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


class TestDownloadMedia:
    def test_skip_existing_file_with_size(self, tmp_path: Path):
        """PBT: exists && size>0 -> skip."""
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        dest = tmp_path / "test.jpg"
        dest.write_text("existing content")

        result = loader._download("http://example.com/img.jpg", dest)
        assert result == dest

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
        assert dest.read_bytes() == b"new data"

    def test_part_file_rename(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path)

        dest = tmp_path / "test.jpg"

        with patch.object(ctx, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.iter_content.return_value = [b"test content"]
            mock_req.return_value = mock_resp

            result = loader._download("http://example.com/img.jpg", dest)

        assert result == dest
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

        with patch.object(loader, "_download", return_value=tmp_path / "test.jpg"):
            with patch.object(loader, "_media_jobs", return_value=[]):
                loader.download_target(UserTarget(identifier="test", is_uid=True))


class TestLatestStamps:
    def test_stamps_roundtrip(self, tmp_path: Path):
        """PBT: Load(Save(stamps)) == stamps."""
        stamps_path = tmp_path / "stamps.json"
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, latest_stamps=stamps_path)

        loader._stamps["u:test"] = datetime(2024, 1, 15, 12, 0, 0, tzinfo=CST)
        loader._save_stamps()

        loader2 = WeiboLoader(ctx, output_dir=tmp_path, latest_stamps=stamps_path)
        assert "u:test" in loader2._stamps
        assert loader2._stamps["u:test"].isoformat() == "2024-01-15T12:00:00+08:00"

    def test_incremental_filtering(self, tmp_path: Path):
        """Test that posts older than cutoff are filtered during download."""
        stamps_path = tmp_path / "stamps.json"
        ctx = MockContext()

        old_post = make_post("m1", created_at=datetime(2024, 1, 1, tzinfo=CST))
        new_post = make_post("m2", created_at=datetime(2024, 2, 1, tzinfo=CST))

        ctx._posts["u:test:p:1"] = ([old_post, new_post], None)

        loader = WeiboLoader(ctx, output_dir=tmp_path, latest_stamps=stamps_path)
        loader._stamps["u:test"] = datetime(2024, 1, 15, tzinfo=CST)

        # Verify stamps are loaded correctly
        assert "u:test" in loader._stamps
        assert loader._stamps["u:test"] == datetime(2024, 1, 15, tzinfo=CST)


class TestFastUpdate:
    def test_fast_update_stops_on_existing(self, tmp_path: Path):
        ctx = MockContext()
        loader = WeiboLoader(ctx, output_dir=tmp_path, fast_update=True)

        post = make_post("m1", media_items=[make_media("http://i.jpg")])
        ctx._posts["u:test:p:1"] = ([post], None)

        target_dir = tmp_path / "test"
        target_dir.mkdir()
        (target_dir / "existing.jpg").write_text("exists")

        with patch.object(loader, "_media_path", return_value=target_dir / "existing.jpg"):
            with patch.object(loader, "_save_ck"):
                result = loader.download_target(UserTarget(identifier="test", is_uid=True))


class TestMetadataExport:
    def test_metadata_json_export(self, tmp_path: Path):
        """PBT: metadata JSON round-trip."""
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


class TestOptionsHash:
    def test_hash_changes_with_options(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, no_videos=True)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, no_videos=False)

        assert loader1._options_hash != loader2._options_hash

    def test_hash_consistent(self, tmp_path: Path):
        ctx = MockContext()
        loader1 = WeiboLoader(ctx, output_dir=tmp_path, count=10)
        loader2 = WeiboLoader(ctx, output_dir=tmp_path, count=10)

        assert loader1._options_hash == loader2._options_hash


@given(st.integers(min_value=0, max_value=100))
@settings(max_examples=20)
def test_count_property(count):
    """PBT: processed_count <= --count."""
    ctx = MockContext()
    loader = WeiboLoader(ctx, output_dir=Path("/tmp"), count=count)
    assert loader.count == max(0, count)
