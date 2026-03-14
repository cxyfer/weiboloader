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

    def _fetch_page(self) -> tuple[list[Post], str | None, bool]:
        page_idx = self._page - 1
        if page_idx >= len(self._pages):
            return [], None, False
        posts = self._pages[page_idx]
        has_more = self._page < len(self._pages)
        cursor = f"cursor_{self._page + 1}" if has_more else None
        return posts, cursor, has_more


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
        buffered_posts=[],
        pending_cursor=None,
        pending_has_more=False,
        page_loaded=False,
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

    def test_terminal_final_page_resume_roundtrip(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target1"
        post = make_post("m1")
        it = MockIterator([[post]], options_hash="opts_abc")

        assert next(it).mid == "m1"
        state = it.freeze()
        assert state.page_loaded is True
        assert state.buffered_posts == []
        assert state.pending_cursor is None
        assert state.pending_has_more is False

        store.save(target_key, resume=state, coverage_options_hash="opts_abc")
        loaded = store.load(target_key)

        assert loaded is not None
        assert loaded.resume is not None
        assert loaded.resume.page_loaded is True
        assert loaded.resume.buffered_posts == []
        assert loaded.resume.pending_cursor is None
        assert loaded.resume.pending_has_more is False

        resumed = MockIterator([[post]], options_hash="opts_abc")
        resumed.thaw(loaded.resume)
        with pytest.raises(StopIteration):
            next(resumed)

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
        path.write_text(json.dumps({"version": progress_module.VERSION, "target_key": "u:other"}), encoding="utf-8")

        assert store.load(target_key) is None

    def test_version_mismatch_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(json.dumps({"version": "2", "target_key": target_key}), encoding="utf-8")

        assert store.load(target_key) is None

    def test_missing_version_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(json.dumps({"target_key": target_key}), encoding="utf-8")

        assert store.load(target_key) is None

    def test_legacy_resume_payload_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(
            json.dumps({
                "version": progress_module.VERSION,
                "target_key": target_key,
                "resume": {
                    "page": 1,
                    "cursor": None,
                    "seen_mids": [],
                    "options_hash": "abc123",
                    "timestamp": "2024-01-01T00:00:00+00:00",
                },
                "coverage": {"intervals": [], "options_hash": "abc123"},
            }),
            encoding="utf-8",
        )

        assert store.load(target_key) is None

    def test_malformed_top_level_payload_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text("[]", encoding="utf-8")

        assert store.load(target_key) is None

    def test_malformed_coverage_payload_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(
            json.dumps({
                "version": progress_module.VERSION,
                "target_key": target_key,
                "resume": None,
                "coverage": [],
            }),
            encoding="utf-8",
        )

        assert store.load(target_key) is None

    def test_inconsistent_resume_suffix_payload_returns_none(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(
            json.dumps({
                "version": progress_module.VERSION,
                "target_key": target_key,
                "resume": {
                    "page": 1,
                    "cursor": None,
                    "seen_mids": [],
                    "buffered_posts": [
                        {
                            "mid": "m2",
                            "bid": None,
                            "text": "",
                            "created_at": datetime(2024, 1, 1, tzinfo=UTC).isoformat(),
                            "user": None,
                            "media_items": [],
                            "raw": {"mid": "m2"},
                        }
                    ],
                    "pending_cursor": None,
                    "pending_has_more": False,
                    "page_loaded": False,
                    "options_hash": "abc123",
                    "timestamp": "2024-01-01T00:00:00+00:00",
                },
                "coverage": {"intervals": [], "options_hash": "abc123"},
            }),
            encoding="utf-8",
        )

        assert store.load(target_key) is None

    def test_seen_mids_must_be_list_of_strings(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(
            json.dumps({
                "version": progress_module.VERSION,
                "target_key": target_key,
                "resume": {
                    "page": 1,
                    "cursor": None,
                    "seen_mids": "m123",
                    "buffered_posts": [],
                    "pending_cursor": None,
                    "pending_has_more": False,
                    "page_loaded": False,
                    "options_hash": "abc123",
                    "timestamp": "2024-01-01T00:00:00+00:00",
                },
                "coverage": {"intervals": [], "options_hash": "abc123"},
            }),
            encoding="utf-8",
        )

        assert store.load(target_key) is None

    def test_atomic_write_leaves_no_tmp_files(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        store.save("u:target", resume=make_resume_state(), coverage=[])

        assert list(store.dir.glob("*.tmp")) == []

    @pytest.mark.parametrize("failure_point", ["mkstemp", "json.dump", "fsync", "replace"])
    def test_save_failure_preserves_last_durable_checkpoint_bytes(
        self,
        tmp_path: Path,
        monkeypatch,
        failure_point: str,
    ):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:target"
        store.save(target_key, resume=make_resume_state(page=1), coverage=[])
        path = store.dir / f"{store.target_hash(target_key)}.json"
        durable_bytes = path.read_bytes()
        error = OSError(f"{failure_point} failed")

        if failure_point == "mkstemp":
            def fail_mkstemp(*args, **kwargs):
                raise error

            monkeypatch.setattr(progress_module.tempfile, "mkstemp", fail_mkstemp)
        elif failure_point == "json.dump":
            def fail_dump(*args, **kwargs):
                raise error

            monkeypatch.setattr(progress_module.json, "dump", fail_dump)
        elif failure_point == "fsync":
            def fail_fsync(*args, **kwargs):
                raise error

            monkeypatch.setattr(progress_module.os, "fsync", fail_fsync)
        else:
            def fail_replace(*args, **kwargs):
                raise error

            monkeypatch.setattr(progress_module.os, "replace", fail_replace)

        with pytest.raises(OSError, match=f"{failure_point} failed"):
            store.save(target_key, resume=make_resume_state(page=2), coverage=[])

        assert path.read_bytes() == durable_bytes
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

    def test_schema_v3_coverage_without_resume_loads(self, tmp_path: Path):
        store = ProgressStore(tmp_path / ".progress")
        target_key = "u:legacy"
        start = datetime(2024, 1, 1, tzinfo=UTC)
        path = store.dir / f"{store.target_hash(target_key)}.json"
        path.write_text(
            json.dumps({
                "version": progress_module.VERSION,
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

    def test_freeze_captures_current_page_suffix_snapshot(self):
        posts1 = [make_post("m1"), make_post("m2"), make_post("m3")]
        posts2 = [make_post("m4")]
        it = MockIterator([posts1, posts2], options_hash="test")

        assert next(it).mid == "m1"
        state = it.freeze()

        assert [post.mid for post in state.buffered_posts] == ["m2", "m3"]
        assert state.pending_cursor == "cursor_2"
        assert state.pending_has_more is True
        assert state.page_loaded is True
        assert state.page == 1
        assert state.options_hash == "test"

    def test_thaw_replays_saved_suffix_before_fetching_later_pages(self):
        posts1 = [make_post("m1"), make_post("m2"), make_post("m3")]
        posts2 = [make_post("m4"), make_post("m5")]
        state = CursorState(
            page=1,
            cursor=None,
            seen_mids=["m1"],
            buffered_posts=[posts1[1], posts1[2]],
            pending_cursor="cursor_2",
            pending_has_more=True,
            page_loaded=True,
            options_hash="test",
            timestamp="2024-01-01T00:00:00+00:00",
        )

        it = MockIterator([posts1, posts2], options_hash="test")
        it.thaw(state)

        remaining = [p.mid for p in it]
        assert remaining == ["m2", "m3", "m4", "m5"]

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
        buffered_posts=[],
        pending_cursor=None,
        pending_has_more=False,
        page_loaded=False,
        options_hash="test",
        timestamp=datetime.now().isoformat(),
    )

    it = NodeIterator(options_hash="test")
    it.thaw(state)
    frozen = it.freeze()

    assert frozen.page == state.page
    assert frozen.cursor == state.cursor
    assert set(frozen.seen_mids) == set(state.seen_mids)
