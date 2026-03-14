from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .structures import CursorState, MediaItem, Post, User

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence

_IS_WIN = sys.platform == "win32"

logger = logging.getLogger(__name__)
VERSION = "3"


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("coverage timestamps must be timezone-aware")
        if self.end < self.start:
            raise ValueError("coverage interval end before start")


@dataclass(slots=True)
class ProgressState:
    target_key: str
    resume: CursorState | None = None
    coverage: list[CoverageInterval] = field(default_factory=list)
    coverage_options_hash: str | None = None


class ProgressStore:
    def __init__(self, progress_dir: Path):
        self.dir = Path(progress_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def target_hash(target_key: str) -> str:
        return hashlib.sha1(target_key.encode()).hexdigest()[:16]

    def _paths(self, target_key: str) -> tuple[Path, Path]:
        base = self.dir / self.target_hash(target_key)
        return base.with_suffix(".json"), base.with_suffix(".lock")

    @contextmanager
    def acquire_lock(self, target_key: str) -> Generator[None, None, None]:
        _, lock_path = self._paths(target_key)
        lock_path.touch(exist_ok=True)
        with open(lock_path, "w") as f:
            try:
                if _IS_WIN:
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            except (BlockingIOError, OSError) as e:
                raise RuntimeError(f"lock contention: {target_key}") from e
            finally:
                if _IS_WIN:
                    import msvcrt

                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    fcntl.flock(f, fcntl.LOCK_UN)

    def load(self, target_key: str) -> ProgressState | None:
        path, _ = self._paths(target_key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise TypeError("progress payload must be an object")
            if data.get("version") != VERSION or data.get("target_key") != target_key:
                return None
            coverage_blob = data.get("coverage", {})
            if coverage_blob is None:
                coverage_blob = {}
            if not isinstance(coverage_blob, dict):
                raise TypeError("coverage payload must be an object")
            return ProgressState(
                target_key=target_key,
                resume=self._deserialize_resume(data.get("resume")),
                coverage=self.deserialize_intervals(coverage_blob.get("intervals", [])),
                coverage_options_hash=coverage_blob.get("options_hash"),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("corrupt progress %s: %s", target_key, e)
            return None

    def save(
        self,
        target_key: str,
        *,
        resume: CursorState | None = None,
        coverage: Iterable[CoverageInterval | tuple[datetime, datetime]] = (),
        coverage_options_hash: str | None = None,
    ) -> None:
        path, _ = self._paths(target_key)
        coverage_blob: dict[str, object] = {"intervals": self.serialize_intervals(coverage)}
        if coverage_options_hash is not None:
            coverage_blob["options_hash"] = coverage_options_hash
        payload = {
            "version": VERSION,
            "target_key": target_key,
            "resume": self._serialize_resume(resume),
            "coverage": coverage_blob,
        }
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception as e:
            logger.error("progress save failed: %s", e)
            Path(tmp).unlink(missing_ok=True)

    def clear(self, target_key: str) -> None:
        path, _ = self._paths(target_key)
        path.unlink(missing_ok=True)

    @classmethod
    def normalize_intervals(
        cls,
        intervals: Iterable[CoverageInterval | tuple[datetime, datetime]],
    ) -> list[CoverageInterval]:
        ordered = sorted((cls._coerce_interval(interval) for interval in intervals), key=lambda item: (item.start, item.end))
        if not ordered:
            return []
        merged = [ordered[0]]
        for interval in ordered[1:]:
            current = merged[-1]
            if interval.start <= current.end:
                if interval.end > current.end:
                    merged[-1] = CoverageInterval(current.start, interval.end)
                continue
            merged.append(interval)
        return merged

    @classmethod
    def contains(
        cls,
        intervals: Sequence[CoverageInterval | tuple[datetime, datetime]],
        point: datetime,
    ) -> bool:
        point = cls._require_aware(point)
        for interval in cls.normalize_intervals(intervals):
            if interval.start <= point <= interval.end:
                return True
        return False

    @classmethod
    def serialize_intervals(
        cls,
        intervals: Iterable[CoverageInterval | tuple[datetime, datetime]],
    ) -> list[dict[str, str]]:
        return [
            {"start": interval.start.isoformat(), "end": interval.end.isoformat()}
            for interval in cls.normalize_intervals(intervals)
        ]

    @classmethod
    def deserialize_intervals(cls, data: object) -> list[CoverageInterval]:
        if data is None:
            return []
        if not isinstance(data, list):
            raise TypeError("coverage intervals must be a list")
        intervals: list[CoverageInterval] = []
        for item in data:
            if not isinstance(item, dict):
                raise TypeError("coverage interval must be an object")
            intervals.append(
                CoverageInterval(
                    start=cls._parse_aware_datetime(item["start"]),
                    end=cls._parse_aware_datetime(item["end"]),
                )
            )
        return cls.normalize_intervals(intervals)

    @classmethod
    def _serialize_resume(cls, state: CursorState | None) -> dict[str, object] | None:
        if state is None:
            return None
        return {
            "page": state.page,
            "cursor": state.cursor,
            "seen_mids": state.seen_mids,
            "buffered_posts": [cls._serialize_post(post) for post in state.buffered_posts],
            "pending_cursor": state.pending_cursor,
            "pending_has_more": state.pending_has_more,
            "page_loaded": state.page_loaded,
            "options_hash": state.options_hash,
            "timestamp": state.timestamp,
        }

    @classmethod
    def _deserialize_resume(cls, data: object) -> CursorState | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("resume payload must be an object")
        page = data["page"]
        if not isinstance(page, int) or page < 1:
            raise TypeError("resume page must be a positive integer")
        cursor = cls._optional_str(data.get("cursor"), "resume cursor")
        seen_raw = data.get("seen_mids", [])
        if not isinstance(seen_raw, list) or not all(isinstance(item, str) for item in seen_raw):
            raise TypeError("resume seen_mids must be a list of strings")
        buffered_posts = data.get("buffered_posts")
        if not isinstance(buffered_posts, list):
            raise TypeError("resume buffered_posts must be a list")
        pending_cursor = cls._optional_str(data.get("pending_cursor"), "resume pending_cursor")
        pending_has_more = data.get("pending_has_more")
        if not isinstance(pending_has_more, bool):
            raise TypeError("resume pending_has_more must be a bool")
        page_loaded = data.get("page_loaded")
        if not isinstance(page_loaded, bool):
            raise TypeError("resume page_loaded must be a bool")
        options_hash = cls._optional_str(data.get("options_hash", ""), "resume options_hash")
        timestamp = cls._optional_str(data.get("timestamp"), "resume timestamp")
        posts = [cls._deserialize_post(post) for post in buffered_posts]
        has_saved_frontier = bool(posts) or pending_has_more or pending_cursor is not None
        if not page_loaded and has_saved_frontier:
            raise ValueError("resume page_loaded inconsistent with saved frontier")
        if not page_loaded and posts:
            raise ValueError("resume buffered_posts require page_loaded")
        if not pending_has_more and pending_cursor is not None:
            raise ValueError("resume pending_cursor requires pending_has_more")
        return CursorState(
            page=page,
            cursor=cursor,
            seen_mids=list(seen_raw),
            buffered_posts=posts,
            pending_cursor=pending_cursor,
            pending_has_more=pending_has_more,
            page_loaded=page_loaded,
            options_hash=options_hash or "",
            timestamp=timestamp,
        )

    @staticmethod
    def _serialize_post(post: Post) -> dict[str, object]:
        return {
            "mid": post.mid,
            "bid": post.bid,
            "text": post.text,
            "created_at": post.created_at.isoformat(),
            "user": ProgressStore._serialize_user(post.user),
            "media_items": [ProgressStore._serialize_media_item(item) for item in post.media_items],
            "raw": post.raw,
        }

    @classmethod
    def _deserialize_post(cls, data: object) -> Post:
        if not isinstance(data, dict):
            raise TypeError("resume post must be an object")
        media_items = data.get("media_items")
        if not isinstance(media_items, list):
            raise TypeError("resume post media_items must be a list")
        return Post(
            mid=str(data["mid"]),
            bid=data.get("bid"),
            text=str(data.get("text", "")),
            created_at=cls._parse_any_datetime(data["created_at"]),
            user=cls._deserialize_user(data.get("user")),
            media_items=[cls._deserialize_media_item(item) for item in media_items],
            raw=cls._require_dict(data.get("raw")),
        )

    @staticmethod
    def _serialize_user(user: User | None) -> dict[str, object] | None:
        if user is None:
            return None
        return {
            "uid": user.uid,
            "nickname": user.nickname,
            "avatar": user.avatar,
            "raw": user.raw,
        }

    @classmethod
    def _deserialize_user(cls, data: object) -> User | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("resume user must be an object")
        return User(
            uid=str(data["uid"]),
            nickname=str(data["nickname"]),
            avatar=data.get("avatar"),
            raw=cls._require_dict(data.get("raw")),
        )

    @staticmethod
    def _serialize_media_item(item: MediaItem) -> dict[str, object]:
        return {
            "media_type": item.media_type,
            "url": item.url,
            "index": item.index,
            "filename_hint": item.filename_hint,
            "raw": item.raw,
        }

    @classmethod
    def _deserialize_media_item(cls, data: object) -> MediaItem:
        if not isinstance(data, dict):
            raise TypeError("resume media item must be an object")
        media_type = data["media_type"]
        if media_type not in {"picture", "video"}:
            raise ValueError("resume media item type invalid")
        return MediaItem(
            media_type=media_type,
            url=str(data["url"]),
            index=int(data["index"]),
            filename_hint=data.get("filename_hint"),
            raw=cls._require_dict(data.get("raw")),
        )

    @staticmethod
    def _require_dict(value: object) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("resume raw payload must be an object")
        return dict(value)

    @staticmethod
    def _optional_str(value: object, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        return value

    @classmethod
    def _coerce_interval(cls, interval: CoverageInterval | tuple[datetime, datetime]) -> CoverageInterval:
        if isinstance(interval, CoverageInterval):
            return interval
        if not isinstance(interval, tuple) or len(interval) != 2:
            raise TypeError("coverage interval must be a pair of datetimes")
        start, end = interval
        return CoverageInterval(start=cls._require_aware(start), end=cls._require_aware(end))

    @staticmethod
    def _require_aware(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError("coverage timestamp must be a datetime")
        if value.tzinfo is None:
            raise ValueError("coverage timestamps must be timezone-aware")
        return value

    @classmethod
    def _parse_aware_datetime(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise TypeError("coverage timestamp must be a string")
        return cls._require_aware(datetime.fromisoformat(value))

    @staticmethod
    def _parse_any_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise TypeError("resume timestamp must be a string")
        return datetime.fromisoformat(value)
