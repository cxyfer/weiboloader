from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from .structures import CursorState, Post

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)
VERSION = "1"


class CheckpointManager:
    def __init__(self, checkpoint_dir: Path, options_hash: str = ""):
        self.dir = Path(checkpoint_dir)
        self.options_hash = options_hash
        self.dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str) -> tuple[Path, Path]:
        base = self.dir / key
        return base.with_suffix(".json"), base.with_suffix(".lock")

    @contextmanager
    def acquire_lock(self, key: str) -> Generator[None, None, None]:
        _, lock_path = self._paths(key)
        lock_path.touch(exist_ok=True)
        with open(lock_path, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            except BlockingIOError:
                raise RuntimeError(f"lock contention: {key}")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def load(self, key: str) -> CursorState | None:
        path, _ = self._paths(key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != VERSION or data.get("options_hash") != self.options_hash:
                return None
            return CursorState(
                page=data["page"],
                cursor=data.get("cursor"),
                seen_mids=data.get("seen_mids", []),
                options_hash=data.get("options_hash", ""),
                timestamp=data.get("timestamp"),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("corrupt checkpoint %s: %s", key, e)
            return None

    def save(self, key: str, state: CursorState) -> None:
        path, _ = self._paths(key)
        data = {
            "version": VERSION,
            "page": state.page,
            "cursor": state.cursor,
            "seen_mids": state.seen_mids,
            "options_hash": state.options_hash,
            "timestamp": state.timestamp,
        }
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, path)
        except Exception as e:
            logger.error("checkpoint save failed: %s", e)
            Path(tmp).unlink(missing_ok=True)


class NodeIterator(Iterator[Post]):
    def __init__(self, options_hash: str = ""):
        self._options_hash = options_hash
        self._page = 1
        self._cursor: str | None = None
        self._seen: set[str] = set()
        self._buffer: deque[Post] = deque()
        self._exhausted = False

    def __iter__(self) -> NodeIterator:
        return self

    def __next__(self) -> Post:
        while not self._buffer and not self._exhausted:
            posts, cursor, has_more = self._fetch_page()
            for p in posts:
                if p.mid not in self._seen:
                    self._buffer.append(p)
            self._cursor = cursor
            self._page += 1
            if not has_more:
                self._exhausted = True

        if not self._buffer:
            raise StopIteration

        post = self._buffer.popleft()
        self._seen.add(post.mid)
        return post

    def _fetch_page(self) -> tuple[list[Post], str | None, bool]:
        raise NotImplementedError

    def freeze(self) -> CursorState:
        from datetime import datetime
        return CursorState(
            page=self._page,
            cursor=self._cursor,
            seen_mids=list(self._seen),
            options_hash=self._options_hash,
            timestamp=datetime.now().isoformat(),
        )

    def thaw(self, state: CursorState) -> None:
        self._page = state.page
        self._cursor = state.cursor
        self._seen = set(state.seen_mids)
        self._options_hash = state.options_hash
        self._buffer.clear()
        self._exhausted = False
