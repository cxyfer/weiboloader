"""Tests for NodeIterator and ProgressStore."""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "weiboloader"
if "weiboloader" not in sys.modules:
    package = types.ModuleType("weiboloader")
    package.__path__ = [str(_PACKAGE_ROOT)]
    sys.modules["weiboloader"] = package

NodeIterator = import_module("weiboloader.nodeiterator").NodeIterator
progress_module = import_module("weiboloader.progress")
structures_module = import_module("weiboloader.structures")
CoverageInterval = progress_module.CoverageInterval
ProgressState = progress_module.ProgressState
ProgressStore = progress_module.ProgressStore
CursorState = structures_module.CursorState
Post = structures_module.Post

UTC = timezone.utc


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
        created_at=datetime.now(),
        user=None,
        media_items=[],
        raw={"mid": mid},
    )


def make_resume_state(page: int = 5, options_hash: str = "abc123") -> CursorState:
    return CursorState(
        page=page,
        cursor=f"c{page}",
        seen_mids=[f"m{i}" for i in range(1, 3)],
        options_hash=options_hash,
        timestamp="2024-01-01T00:00:00+00:00",
    )


class TestProgressStore:
    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        assert store.load("u:nonexistent") is None

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target1"
        resume = make_resume_state()
        start = datetime(2024, 1, 1, tzinfo=UTC)
        intervals = [
            (start + timedelta(hours=2), start + timedelta(hours=3)),
            (start, start + timedelta(hours=1)),
            (start + timedelta(minutes=30), start + timedelta(hours=2, minutes=30)),
        ]

        store.save(target_key, resume=resume, coverage=intervals, coverage_options_hash="opts_abc")
        loaded = store.load(target_key)

        assert loaded is not None
        assert loaded.target_key == target_key
        assert loaded.resume == resume
        assert loaded.coverage == [CoverageInterval(start=start, end=start + timedelta(hours=3))]
        assert loaded.coverage_options_hash == "opts_abc"

        path = store.dir / f"{store.target_hash(target_key)}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["target_key"] == target_key
        assert payload["resume"]["page"] == resume.page
        assert payload["coverage"]["options_hash"] == "opts_abc"
        assert payload["coverage"]["intervals"] == [
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=3)).isoformat(),
            }
        ]

    def test_resume_can_be_absent(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target1"

        store.save(target_key, coverage=[])
        loaded = store.load(target_key)

        assert loaded is not None
        assert loaded.resume is None
        assert loaded.coverage == []

    def test_corrupt_progress_logs_warning(self, tmp_path: Path, caplog):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text("not valid json", encoding="utf-8")

        with caplog.at_level("WARNING"):
            result = store.load(target_key)

        assert result is None
        assert "corrupt progress" in caplog.text.lower()

    def test_target_key_mismatch_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(json.dumps({"version": "1", "target_key": "u:other"}), encoding="utf-8")

        assert store.load(target_key) is None

    def test_atomic_write_leaves_no_tmp_files(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        store.save("u:target", resume=make_resume_state(), coverage=[])

        assert list(store.dir.glob("*.tmp")) == []

    def test_lock_contention_raises(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")

        with store.acquire_lock("u:target"):
            with pytest.raises(RuntimeError, match="lock contention"):
                with store.acquire_lock("u:target"):
                    pass

    def test_lock_release_allows_reacquire(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")

        with store.acquire_lock("u:target"):
            pass

        with store.acquire_lock("u:target"):
            pass

    def test_different_keys_no_contention(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")

        with store.acquire_lock("u:target1"):
            with store.acquire_lock("u:target2"):
                pass

    def test_coverage_operations_keep_intervals_sorted_and_non_overlapping(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        intervals = [
            (start + timedelta(hours=4), start + timedelta(hours=5)),
            (start + timedelta(hours=1), start + timedelta(hours=2)),
            (start + timedelta(hours=2), start + timedelta(hours=3)),
            (start, start + timedelta(minutes=30)),
        ]

        normalized = ProgressStore.normalize_intervals(intervals)

        assert normalized == [
            CoverageInterval(start=start, end=start + timedelta(minutes=30)),
            CoverageInterval(start=start + timedelta(hours=1), end=start + timedelta(hours=3)),
            CoverageInterval(start=start + timedelta(hours=4), end=start + timedelta(hours=5)),
        ]
        assert ProgressStore.contains(normalized, start + timedelta(hours=2, minutes=30)) is True
        assert ProgressStore.contains(normalized, start + timedelta(hours=3, minutes=30)) is False

    def test_coverage_requires_aware_timestamps(self):
        start = datetime(2024, 1, 1)
        end = start + timedelta(hours=1)

        with pytest.raises(ValueError, match="timezone-aware"):
            ProgressStore.normalize_intervals([(start, end)])

    def test_serialize_and_deserialize_intervals_roundtrip(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        intervals = [
            CoverageInterval(start=start, end=start + timedelta(hours=1)),
            CoverageInterval(start=start + timedelta(hours=2), end=start + timedelta(hours=3)),
        ]

        serialized = ProgressStore.serialize_intervals(intervals)
        deserialized = ProgressStore.deserialize_intervals(serialized)

        assert deserialized == intervals

    def test_legacy_progress_loads_without_crash(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:legacy"
        start = datetime(2024, 1, 1, tzinfo=UTC)
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(
            json.dumps({
                "version": "1",
                "target_key": target_key,
                "resume": None,
                "coverage": {
                    "intervals": [
                        {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
                    ]
                },
            }),
            encoding="utf-8",
        )

        loaded = store.load(target_key)

        assert loaded is not None
        assert loaded.coverage == [CoverageInterval(start=start, end=start + timedelta(hours=1))]
        assert loaded.coverage_options_hash is None

    def test_save_without_coverage_options_hash_omits_key(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:nohash"
        store.save(target_key, coverage=[])

        path = store.dir / f"{store.target_hash(target_key)}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "options_hash" not in payload["coverage"]

    def test_coverage_options_hash_roundtrip(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:hashrt"
        start = datetime(2024, 6, 1, tzinfo=UTC)
        intervals = [CoverageInterval(start=start, end=start + timedelta(hours=2))]

        store.save(target_key, coverage=intervals, coverage_options_hash="hash_v1")
        loaded = store.load(target_key)

        assert loaded is not None
        assert loaded.coverage_options_hash == "hash_v1"
        assert loaded.coverage == intervals

    def test_coverage_options_mismatch_detectable(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:mismatch"
        start = datetime(2024, 1, 1, tzinfo=UTC)

        store.save(
            target_key,
            coverage=[CoverageInterval(start=start, end=start + timedelta(hours=1))],
            coverage_options_hash="hash_A",
        )
        loaded = store.load(target_key)

        assert loaded is not None
        current_hash = "hash_B"
        assert loaded.coverage_options_hash != current_hash

    def test_legacy_coverage_treated_as_incompatible(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:legcompat"
        start = datetime(2024, 1, 1, tzinfo=UTC)

        store.save(target_key, coverage=[CoverageInterval(start=start, end=start + timedelta(hours=1))])
        loaded = store.load(target_key)

        assert loaded is not None
        assert len(loaded.coverage) == 1
        assert loaded.coverage_options_hash is None
        compatible = loaded.coverage_options_hash is not None and loaded.coverage_options_hash == "any_hash"
        assert compatible is False


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
        posts1 = [make_post("m1"), make_post("m2")]
        posts2 = [make_post("m2"), make_post("m3")]
        it = MockIterator([posts1, posts2])

        mids = [p.mid for p in it]
        assert len(mids) == len(set(mids))
        assert set(mids) == {"m1", "m2", "m3"}

    def test_freeze_thaw_roundtrip(self):
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
        it = MockIterator([[make_post("m1")]], options_hash="test")
        state1 = it.freeze()
        state2 = it.freeze()

        assert state1.page == state2.page
        assert state1.cursor == state2.cursor
        assert state1.seen_mids == state2.seen_mids
        assert state1.options_hash == state2.options_hash

    def test_cursor_monotonic_advances(self):
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


@given(st.integers(min_value=1, max_value=100))
def test_checkpoint_roundtrip_property(page):
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
