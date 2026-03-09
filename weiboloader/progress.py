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

from .structures import CursorState

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence

_IS_WIN = sys.platform == "win32"

logger = logging.getLogger(__name__)
VERSION = "1"


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
            if data.get("version") != VERSION or data.get("target_key") != target_key:
                return None
            return ProgressState(
                target_key=target_key,
                resume=self._deserialize_resume(data.get("resume")),
                coverage=self.deserialize_intervals(data.get("coverage", {}).get("intervals", [])),
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
    ) -> None:
        path, _ = self._paths(target_key)
        payload = {
            "version": VERSION,
            "target_key": target_key,
            "resume": self._serialize_resume(resume),
            "coverage": {"intervals": self.serialize_intervals(coverage)},
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

    @staticmethod
    def _serialize_resume(state: CursorState | None) -> dict[str, object] | None:
        if state is None:
            return None
        return {
            "page": state.page,
            "cursor": state.cursor,
            "seen_mids": state.seen_mids,
            "options_hash": state.options_hash,
            "timestamp": state.timestamp,
        }

    @staticmethod
    def _deserialize_resume(data: object) -> CursorState | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("resume payload must be an object")
        return CursorState(
            page=data["page"],
            cursor=data.get("cursor"),
            seen_mids=list(data.get("seen_mids", [])),
            options_hash=data.get("options_hash", ""),
            timestamp=data.get("timestamp"),
        )

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
