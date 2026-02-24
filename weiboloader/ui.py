from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn

logger = logging.getLogger(__name__)


class EventKind(str, Enum):
    STAGE = "stage"
    TARGET_START = "target_start"
    POST_DONE = "post_done"
    MEDIA_DONE = "media_done"
    TARGET_DONE = "target_done"
    INTERRUPTED = "interrupted"


class MediaOutcome(str, Enum):
    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True)
class UIEvent:
    kind: EventKind
    message: str | None = None
    target_key: str | None = None
    outcome: MediaOutcome | None = None
    media_done: int | None = None
    media_total: int | None = None
    posts_processed: int | None = None
    downloaded: int | None = None
    skipped: int | None = None
    failed: int | None = None
    ok: bool | None = None


@dataclass(slots=True)
class DownloadResult:
    outcome: MediaOutcome
    path: Path


@runtime_checkable
class ProgressSink(Protocol):
    def emit(self, event: UIEvent) -> None: ...
    def close(self) -> None: ...


class NullSink:
    def emit(self, event: UIEvent) -> None:
        pass

    def close(self) -> None:
        pass


class RichSink:
    def __init__(self, console: Console) -> None:
        self._console = console
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            console=console,
            transient=True,
        )
        self._task_id = self._progress.add_task("Initializing...", total=None)
        self._progress.start()

    def emit(self, event: UIEvent) -> None:
        try:
            self._handle(event)
        except Exception:
            logger.debug("RichSink.emit failed", exc_info=True)

    def close(self) -> None:
        try:
            self._progress.stop()
        except Exception:
            logger.debug("RichSink.close failed", exc_info=True)

    def pause(self) -> None:
        try:
            self._progress.stop()
        except Exception:
            logger.debug("RichSink.pause failed", exc_info=True)

    def resume(self) -> None:
        try:
            self._progress.start()
        except Exception:
            logger.debug("RichSink.resume failed", exc_info=True)

    def _handle(self, event: UIEvent) -> None:
        kind = event.kind
        if kind == EventKind.STAGE:
            self._progress.update(self._task_id, description=escape(event.message or ""))
        elif kind == EventKind.TARGET_START:
            self._progress.update(self._task_id, description=f"Target: {escape(event.target_key or '')}")
        elif kind == EventKind.MEDIA_DONE:
            self._progress.update(
                self._task_id,
                description=f"Media {event.media_done}/{event.media_total}",
            )
        elif kind == EventKind.POST_DONE:
            self._progress.update(
                self._task_id,
                description=f"Processing posts: {event.posts_processed}",
            )
        elif kind == EventKind.TARGET_DONE:
            self._progress.update(self._task_id, description="")
            key = escape(event.target_key or "")
            if event.failed:
                self._console.print(
                    f"[red]✗[/red] {key}: {event.posts_processed} posts, "
                    f"{event.downloaded} downloaded, {event.failed} failed"
                )
            else:
                self._console.print(
                    f"[green]✓[/green] {key}: {event.posts_processed} posts, "
                    f"{event.downloaded} downloaded, {event.skipped} skipped"
                )
        elif kind == EventKind.INTERRUPTED:
            self._progress.update(self._task_id, description=f"Interrupted: {escape(event.target_key or '')}")
