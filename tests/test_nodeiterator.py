"""Tests for NodeIterator and CheckpointManager (Phase 2.4)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, strategies as st

from weiboloader.nodeiterator import CheckpointManager, NodeIterator
from weiboloader.structures import CursorState, Post, User

if TYPE_CHECKING:
    from collections.abc import Generator


class MockIterator(NodeIterator):
    def __init__(self, posts_per_page: list[list[Post]], options_hash: str = ""):
        super().__init__(options_hash=options_hash)
        self._pages = posts_per_page
        self._page_idx = 0

    def _fetch_page(self) -> tuple[list[Post], str | None, bool]:
        if self._page_idx >= len(self._pages):
            return [], None, False
        posts = self._pages[self._page_idx]
        self._page_idx += 1
        cursor = f"cursor_{self._page_idx}" if self._page_idx < len(self._pages) else None
        return posts, cursor, self._page_idx < len(self._pages)


def make_post(mid: str, text: str = "") -> Post:
    return Post(
        mid=mid,
        bid=None,
        text=text,
        created_at=__import__("datetime").datetime.now(),
        user=None,
        media_items=[],
        raw={"mid": mid},
    )


class TestCheckpointManager:
    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path)
        assert mgr.load("nonexistent") is None

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path, options_hash="abc123")
        state = CursorState(page=5, cursor="c123", seen_mids=["m1", "m2"], options_hash="abc123", timestamp="2024-01-01T00:00:00")

        mgr.save("target1", state)
        loaded = mgr.load("target1")

        assert loaded is not None
        assert loaded.page == 5
        assert loaded.cursor == "c123"
        assert loaded.seen_mids == ["m1", "m2"]
        assert loaded.options_hash == "abc123"

    def test_version_mismatch_returns_none(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path)
        path = tmp_path / "target.json"
        path.write_text(json.dumps({"version": "0", "page": 1, "options_hash": ""}))
        assert mgr.load("target") is None

    def test_options_hash_mismatch_returns_none(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path, options_hash="new")
        path = tmp_path / "target.json"
        path.write_text(json.dumps({"version": "1", "page": 1, "options_hash": "old", "seen_mids": []}))
        assert mgr.load("target") is None

    def test_corrupt_checkpoint_logs_warning(self, tmp_path: Path, caplog):
        mgr = CheckpointManager(tmp_path)
        path = tmp_path / "target.json"
        path.write_text("not valid json")

        with caplog.at_level("WARNING"):
            result = mgr.load("target")

        assert result is None
        assert "corrupt checkpoint" in caplog.text.lower()

    def test_atomic_write(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path)
        state = CursorState(page=1, cursor=None, seen_mids=[], options_hash="", timestamp=None)

        mgr.save("target", state)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    @pytest.mark.parametrize("page", [1, 10, 100])
    def test_checkpoint_file_is_valid_json(self, tmp_path: Path, page: int):
        """PBT: checkpoint file is always valid JSON or absent."""
        mgr = CheckpointManager(tmp_path)
        state = CursorState(page=page, cursor=f"c{page}", seen_mids=[f"m{i}" for i in range(page)], options_hash="", timestamp=None)

        mgr.save("target", state)
        path = tmp_path / "target.json"

        assert path.exists()
        data = json.loads(path.read_text())
        assert "version" in data
        assert "page" in data


class TestNodeIterator:
    def test_empty_iterator(self):
        it = MockIterator([[]])
        with pytest.raises(StopIteration):
            next(it)

    def test_single_page_iteration(self):
        posts = [make_post("m1"), make_post("m2")]
        it = MockIterator([posts])

        assert next(it).mid == "m1"
        assert next(it).mid == "m2"
        with pytest.raises(StopIteration):
            next(it)

    def test_multi_page_iteration(self):
        posts1 = [make_post("m1"), make_post("m2")]
        posts2 = [make_post("m3"), make_post("m4")]
        it = MockIterator([posts1, posts2])

        mids = [p.mid for p in it]
        assert mids == ["m1", "m2", "m3", "m4"]

    def test_no_duplicate_mids(self):
        """PBT: no mid is yielded twice."""
        posts1 = [make_post("m1"), make_post("m2")]
        posts2 = [make_post("m2"), make_post("m3")]
        it = MockIterator([posts1, posts2])

        mids = [p.mid for p in it]
        assert len(mids) == len(set(mids))
        assert set(mids) == {"m1", "m2", "m3"}

    def test_freeze_thaw_roundtrip(self):
        """PBT: thaw(freeze(state)).next() == state.next()."""
        posts1 = [make_post("m1")]
        posts2 = [make_post("m2"), make_post("m3")]
        it = MockIterator([posts1, posts2])

        next(it)
        state = it.freeze()

        it2 = MockIterator([posts1, posts2])
        it2.thaw(state)

        remaining = [p.mid for p in it2]
        assert remaining == ["m2", "m3"]

    def test_freeze_idempotent(self):
        """PBT: freeze without advancing -> identical serialized output."""
        it = MockIterator([[make_post("m1")]], options_hash="test")
        state1 = it.freeze()
        state2 = it.freeze()

        assert state1.page == state2.page
        assert state1.cursor == state2.cursor
        assert state1.seen_mids == state2.seen_mids
        assert state1.options_hash == state2.options_hash

    def test_cursor_monotonic_advances(self):
        """PBT: cursor monotonically advances."""
        posts = [[make_post(f"m{i}")] for i in range(5)]
        it = MockIterator(posts)

        cursors = []
        for _ in range(5):
            try:
                next(it)
                state = it.freeze()
                cursors.append(state.page)
            except StopIteration:
                break

        assert cursors == sorted(cursors)
        assert len(set(cursors)) == len(cursors)


class TestCheckpointManagerLock:
    def test_lock_contention_raises(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path)

        with mgr.acquire_lock("target"):
            with pytest.raises(RuntimeError, match="lock contention"):
                with mgr.acquire_lock("target"):
                    pass

    def test_lock_release_allows_reacquire(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path)

        with mgr.acquire_lock("target"):
            pass

        with mgr.acquire_lock("target"):
            pass

    def test_different_keys_no_contention(self, tmp_path: Path):
        mgr = CheckpointManager(tmp_path)

        with mgr.acquire_lock("target1"):
            with mgr.acquire_lock("target2"):
                pass


@given(st.integers(min_value=1, max_value=100))
def test_checkpoint_roundtrip_property(page):
    """PBT: thaw(freeze(state)) preserves page number."""
    from datetime import datetime

    state = CursorState(
        page=page,
        cursor=f"cursor_{page}",
        seen_mids=[f"mid_{i}" for i in range(min(page, 10))],
        options_hash="test",
        timestamp=datetime.now().isoformat(),
    )

    it = NodeIterator(options_hash="test")
    it.thaw(state)
    frozen = it.freeze()

    assert frozen.page == state.page
    assert frozen.cursor == state.cursor
    assert set(frozen.seen_mids) == set(state.seen_mids)
