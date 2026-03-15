from __future__ import annotations

from collections import deque
from collections.abc import Iterator

from .progress import ProgressStore
from .structures import CursorState, Post


class CheckpointManager:
    def __init__(self, checkpoint_dir, options_hash: str = ""):
        self._store = ProgressStore(checkpoint_dir)
        self.dir = self._store.dir
        self.options_hash = options_hash

    def acquire_lock(self, key: str):
        return self._store.acquire_lock(key)

    def load(self, key: str) -> CursorState | None:
        state = self._store.load(key)
        if state is None or state.resume is None:
            return None
        if state.resume.options_hash != self.options_hash:
            return None
        return state.resume

    def save(self, key: str, state: CursorState) -> None:
        self._store.save(key, resume=state)


class NodeIterator(Iterator[Post]):
    def __init__(self, options_hash: str = ""):
        self._options_hash = options_hash
        self._page = 1
        self._cursor: str | None = None
        self._seen: set[str] = set()
        self._buffer: deque[Post] = deque()
        self._exhausted = False
        self._pending_cursor: str | None = None
        self._pending_has_more = False
        self._page_loaded = False

    def __iter__(self) -> NodeIterator:
        return self

    def __next__(self) -> Post:
        while not self._buffer and not self._exhausted:
            if self._page_loaded:
                if self._pending_has_more:
                    self._page += 1
                    self._cursor = self._pending_cursor
                    self._pending_has_more = False
                    self._page_loaded = False
                else:
                    self._exhausted = True
                    break

            posts, cursor, has_more = self._fetch_page()
            for p in posts:
                if p.mid not in self._seen:
                    self._buffer.append(p)

            self._pending_cursor = cursor
            self._pending_has_more = has_more
            self._page_loaded = True
            if not has_more and not self._buffer:
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
            buffered_posts=list(self._buffer),
            pending_cursor=self._pending_cursor,
            pending_has_more=self._pending_has_more,
            page_loaded=self._page_loaded,
            options_hash=self._options_hash,
            timestamp=datetime.now().isoformat(),
        )

    def thaw(self, state: CursorState) -> None:
        self._page = state.page
        self._cursor = state.cursor
        self._seen = set(state.seen_mids)
        self._options_hash = state.options_hash
        self._buffer = deque(state.buffered_posts)
        self._exhausted = False
        self._pending_cursor = state.pending_cursor
        self._pending_has_more = state.pending_has_more
        self._page_loaded = state.page_loaded
