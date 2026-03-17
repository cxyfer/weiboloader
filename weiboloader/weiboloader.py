from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .boundary import DateBoundary, IdBoundary, parse_date_boundary, parse_id_boundary, parse_mid_value, serialize_boundary
from .context import WeiboLoaderContext
from .exceptions import CheckpointError, TargetError
from .naming import build_directory, build_filename
from .nodeiterator import NodeIterator
from .progress import CoverageInterval, ProgressStore
from .structures import CursorState, MediaItem, MidTarget, Post, SearchTarget, SuperTopicTarget, TargetSpec, UserTarget
from .ui import DownloadResult, EventKind, MediaOutcome, NullSink, ProgressSink, UIEvent

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
_STREAM_READ_TIMEOUT = 60
_MEDIA_DOWNLOAD_TIMEOUT = 60
_PER_MEDIA_TIMEOUT = 30
_MONOTONIC_WINDOW_SIZE = 5


@dataclass(slots=True)
class _ResolvedTarget:
    target: TargetSpec
    key: str
    dir_kwargs: dict[str, str]


@dataclass(slots=True)
class _ActiveProgress:
    target_key: str
    resume: CursorState | None
    committed_coverage: list[CoverageInterval]
    coverage_options_hash: str | None
    run_start: datetime | None = None
    run_end: datetime | None = None
    monotonic_window: deque[datetime] = field(default_factory=lambda: deque(maxlen=_MONOTONIC_WINDOW_SIZE))


class _PostIterator(NodeIterator):
    def __init__(self, fetch, options_hash: str, single: bool = False):
        super().__init__(options_hash=options_hash)
        self._fetch = fetch
        self._single = single
        self._done = False

    def _fetch_page(self):
        if self._single:
            if self._done:
                return [], None, False
            self._done = True
            posts, _ = self._fetch(1)
            return posts, None, False
        posts, cursor = self._fetch(self._page)
        return posts, cursor, bool(posts and cursor)


def _get_socket(resp):
    try:
        return resp.raw.fp.fp.raw._sock
    except AttributeError:
        pass
    try:
        return resp.raw._original_response.fp.raw._sock
    except AttributeError:
        return None


class WeiboLoader:
    def __init__(
        self,
        context: WeiboLoaderContext,
        *,
        dirname_pattern: str | None = None,
        filename_pattern: str = "{date}_{name}",
        no_videos: bool = False,
        no_pictures: bool = False,
        count: int = 0,
        fast_update: bool = False,
        metadata_json: bool = False,
        post_metadata_txt: str | None = None,
        max_workers: int = 1,
        no_resume: bool = False,
        no_coverage: bool = False,
        date_boundary: str | None = None,
        id_boundary: str | None = None,
        checkpoint_dir: str | Path | None = None,
        output_dir: str | Path = ".",
        progress: ProgressSink | None = None,
    ):
        self.context = context
        self._sink: ProgressSink = progress or NullSink()
        self.dirname_pattern = dirname_pattern
        self.filename_pattern = filename_pattern
        self.no_videos = no_videos
        self.no_pictures = no_pictures
        self.count = max(0, count)
        self.fast_update = fast_update
        self.metadata_json = metadata_json
        self.post_metadata_txt = post_metadata_txt
        self.max_workers = max(1, max_workers)
        self.no_resume = no_resume
        self.no_coverage = no_coverage
        self.date_boundary: DateBoundary | None = parse_date_boundary(date_boundary)
        self.id_boundary: IdBoundary | None = parse_id_boundary(id_boundary)
        self._date_boundary_key = serialize_boundary(self.date_boundary)
        self._id_boundary_key = serialize_boundary(self.id_boundary)
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._options_hash = self._hash_options()
        progress_dir = Path(checkpoint_dir).expanduser() if checkpoint_dir else self.output_dir / ".progress"
        self._progress = ProgressStore(progress_dir)
        self._active_progress: dict[str, _ActiveProgress] = {}

    def _safe_emit(self, event: UIEvent) -> None:
        try:
            self._sink.emit(event)
        except Exception:
            logger.debug("sink.emit failed", exc_info=True)

    def download_targets(self, targets: Sequence[TargetSpec]) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for target in targets:
            key = self._target_key(target)
            try:
                results[key] = self.download_target(target)
            except KeyboardInterrupt:
                self.flush()
                raise
            except Exception:
                logger.exception("target failed: %s", key)
                results[key] = False
        return results

    def download_target(self, target: TargetSpec) -> bool:
        key = self._target_key(target)
        try:
            resolved = self._resolve_target(target)
        except Exception:
            logger.exception("resolve failed: %s", key)
            return False

        iterator = self._create_iterator(resolved.target)
        state = self._load_progress(resolved.key)
        resume = None
        committed_coverage: list[CoverageInterval] = []
        coverage_options_hash: str | None = None
        if state:
            coverage_options_hash = state.coverage_options_hash
            if coverage_options_hash == self._options_hash:
                committed_coverage = list(state.coverage)
            if not self.no_resume and state.resume and state.resume.options_hash == self._options_hash:
                iterator.thaw(state.resume)
                resume = state.resume
        has_checkpoint_state = state is not None
        output_compatible = coverage_options_hash == self._options_hash

        active = _ActiveProgress(
            target_key=resolved.key,
            resume=resume,
            committed_coverage=committed_coverage,
            coverage_options_hash=self._options_hash,
        )
        self._active_progress[resolved.key] = active

        target_dir: Path | None = None

        processed = 0
        ok = True
        downloaded = 0
        skipped = 0
        failed = 0
        current_group_stamp: datetime | None = None
        current_group_ok = True
        safe_resume: CursorState | None = active.resume
        group_entry_resume: CursorState | None = active.resume
        group_resume: CursorState | None = active.resume
        gap_open = False
        target_complete = True

        self._safe_emit(UIEvent(kind=EventKind.TARGET_START, target_key=resolved.key))

        exe = None
        shutdown_done = False
        can_recover_latest_frontier = False
        try:
            exe = ThreadPoolExecutor(max_workers=self.max_workers)
            for post in iterator:
                can_recover_latest_frontier = False
                if self.count and processed >= self.count:
                    target_complete = False
                    break

                created = self._cst(post.created_at)
                if current_group_stamp is not None and created != current_group_stamp:
                    self._seal_group(active, current_group_stamp, current_group_ok)
                    if current_group_ok and not gap_open:
                        safe_resume = group_resume
                    if not current_group_ok:
                        self._commit_coverage_run(active)
                        gap_open = True
                    active.resume = safe_resume
                    current_group_stamp = None
                    current_group_ok = True

                boundary_action = self._boundary_action(resolved.target, post)
                if boundary_action == "break":
                    break
                if boundary_action == "continue":
                    continue

                if not self.no_coverage and ProgressStore.contains(self._materialized_coverage(active), created):
                    continue

                if current_group_stamp is None:
                    current_group_stamp = created
                    current_group_ok = True
                    group_entry_resume = active.resume if gap_open else safe_resume
                    group_resume = group_entry_resume

                if target_dir is None:
                    target_dir = self._build_dir(resolved)
                jobs = self._media_jobs(target_dir, post)
                if self.fast_update and not has_checkpoint_state and any(p.exists() and p.stat().st_size > 0 for _, p in jobs):
                    target_complete = False
                    break

                if self.metadata_json:
                    self._write_json(target_dir, post, output_compatible=output_compatible)
                if self.post_metadata_txt:
                    self._write_txt(target_dir, post, output_compatible=output_compatible)

                media_total = len(jobs)
                media_done = 0
                post_index = processed + 1
                timed_out = False
                post_ok = True

                future_to_path = {exe.submit(self._download_media, post, m.url, p): p for m, p in jobs}
                post_timeout = max(60, media_total * _PER_MEDIA_TIMEOUT) if future_to_path else None

                if future_to_path:
                    deadline = time.monotonic() + post_timeout
                    pending = set(future_to_path)
                    while pending:
                        remaining = deadline - time.monotonic()
                        poll_timeout = max(0, min(0.5, remaining))
                        done_batch, pending = wait(pending, timeout=poll_timeout, return_when=FIRST_COMPLETED)
                        for f in done_batch:
                            try:
                                result = f.result()
                            except Exception:
                                failed += 1
                                ok = False
                                post_ok = False
                                media_done += 1
                                self._safe_emit(UIEvent(
                                    kind=EventKind.MEDIA_DONE,
                                    outcome=MediaOutcome.FAILED,
                                    media_done=media_done,
                                    media_total=media_total,
                                    post_index=post_index,
                                    filename=future_to_path[f].name,
                                ))
                                continue
                            if result.outcome == MediaOutcome.DOWNLOADED:
                                downloaded += 1
                            elif result.outcome == MediaOutcome.SKIPPED:
                                skipped += 1
                            else:
                                failed += 1
                                ok = False
                                post_ok = False
                            media_done += 1
                            self._safe_emit(UIEvent(
                                kind=EventKind.MEDIA_DONE,
                                outcome=result.outcome,
                                media_done=media_done,
                                media_total=media_total,
                                post_index=post_index,
                                filename=future_to_path[f].name,
                            ))
                        if pending and time.monotonic() >= deadline:
                            timed_out = True
                            post_ok = False
                            for f in pending:
                                f.cancel()
                                failed += 1
                                ok = False
                                media_done += 1
                                self._safe_emit(UIEvent(
                                    kind=EventKind.MEDIA_DONE,
                                    outcome=MediaOutcome.FAILED,
                                    media_done=media_done,
                                    media_total=media_total,
                                    post_index=post_index,
                                    filename=future_to_path[f].name,
                                ))
                            break

                processed += 1
                if not post_ok:
                    current_group_ok = False
                    group_resume = group_entry_resume
                if not timed_out:
                    can_recover_latest_frontier = True
                    frozen = iterator.freeze()
                    if not self.no_resume:
                        if post_ok:
                            group_resume = frozen
                        active.resume = safe_resume if gap_open or not current_group_ok else group_resume
                    self._persist_progress(active)
                    can_recover_latest_frontier = False
                self._safe_emit(UIEvent(kind=EventKind.POST_DONE, posts_processed=processed))

            if target_complete:
                self._seal_group(active, current_group_stamp, current_group_ok)
                if current_group_ok and not gap_open:
                    safe_resume = group_resume
                elif current_group_stamp is not None:
                    gap_open = True
                    safe_resume = group_entry_resume
            self._finalize_coverage(active)

            if target_complete:
                active.resume = safe_resume
                if ok and not gap_open:
                    active.resume = None

            self._persist_progress(active)

            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE,
                target_key=resolved.key,
                posts_processed=processed,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                ok=ok,
            ))
            return ok

        except KeyboardInterrupt:
            if can_recover_latest_frontier and not self.no_resume:
                frozen = iterator.freeze()
                if current_group_ok and not gap_open:
                    group_resume = frozen
                active.resume = safe_resume if gap_open or not current_group_ok else group_resume
            if exe is not None and not shutdown_done:
                exe.shutdown(wait=False, cancel_futures=True)
                shutdown_done = True
            self._safe_emit(UIEvent(kind=EventKind.INTERRUPTED, target_key=resolved.key))
            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE,
                target_key=resolved.key,
                posts_processed=processed,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                ok=False,
            ))
            self._commit_coverage_run(active)
            self._persist_progress(active)
            raise
        except CheckpointError:
            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE,
                target_key=resolved.key,
                posts_processed=processed,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                ok=False,
            ))
            raise
        except Exception:
            logger.exception("download failed: %s", resolved.key)
            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE,
                target_key=resolved.key,
                posts_processed=processed,
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                ok=False,
            ))
            self._commit_coverage_run(active)
            self._persist_progress(active)
            return False
        finally:
            if exe is not None and not shutdown_done:
                exe.shutdown(wait=True)
                shutdown_done = True
            self._active_progress.pop(resolved.key, None)

    def _create_iterator(self, target: TargetSpec) -> _PostIterator:
        if isinstance(target, UserTarget):
            uid = target.identifier if target.is_uid else self.context.resolve_nickname_to_uid(target.identifier)
            return _PostIterator(lambda p: self.context.get_user_posts(uid, p), self._options_hash)
        if isinstance(target, SuperTopicTarget):
            cid = target.identifier
            if not target.is_containerid:
                topics = self.context.search_supertopic(target.identifier)
                if not topics:
                    raise TargetError(f"supertopic not found: {target.identifier}")
                cid = topics[0].containerid
            return _PostIterator(lambda p: self.context.get_supertopic_posts(cid, p), self._options_hash)
        if isinstance(target, SearchTarget):
            return _PostIterator(lambda p: self.context.search_posts(target.keyword, p), self._options_hash)
        if isinstance(target, MidTarget):
            return _PostIterator(lambda _: ([self.context.get_post_by_mid(target.mid)], None), self._options_hash, single=True)
        raise TargetError(f"unsupported target: {target}")

    def _download(self, url: str, dest: Path) -> DownloadResult:
        if dest.exists() and dest.stat().st_size > 0:
            return DownloadResult(MediaOutcome.SKIPPED, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        resp = None
        deadline = time.monotonic() + _MEDIA_DOWNLOAD_TIMEOUT
        try:
            resp = self.context.request(
                "GET",
                url,
                bucket="media",
                allow_captcha=False,
                stream=True,
                retries=2,
                timeout=(self.context.req_timeout, _STREAM_READ_TIMEOUT),
            )
            sock = _get_socket(resp)
            if sock is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("download timeout")
                sock.settimeout(remaining)
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("download timeout")
                    if sock is not None:
                        sock.settimeout(remaining)
                    if chunk:
                        f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            os.replace(part, dest)
            return DownloadResult(MediaOutcome.DOWNLOADED, dest)
        except Exception:
            logger.exception("download failed: %s", url)
            part.unlink(missing_ok=True)
            return DownloadResult(MediaOutcome.FAILED, dest)
        finally:
            if resp:
                resp.close()

    def _apply_post_mtime(self, path: Path, post: Post) -> None:
        epoch = self._cst(post.created_at).timestamp()
        atime = path.stat().st_atime
        os.utime(path, (atime, epoch))

    def _discard_failed_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            logger.exception("failed to remove landed file: %s", path)
            raise OSError(f"failed to remove landed file: {path}") from exc

    def _write_sidecar(self, path: Path, content: str) -> None:
        part = path.with_suffix(f"{path.suffix}.part")
        try:
            part.write_text(content, encoding="utf-8")
            os.replace(part, path)
        except Exception:
            part.unlink(missing_ok=True)
            raise

    def _download_media(self, post: Post, url: str, dest: Path) -> DownloadResult:
        result = self._download(url, dest)
        if result.outcome != MediaOutcome.DOWNLOADED or not result.path.exists():
            return result
        try:
            self._apply_post_mtime(result.path, post)
            return result
        except Exception as mtime_exc:
            logger.exception("failed to apply media mtime: %s", result.path)
            try:
                self._discard_failed_file(result.path)
            except Exception as cleanup_exc:
                logger.exception("failed cleanup after media mtime error: %s", result.path)
                raise mtime_exc from cleanup_exc
            return DownloadResult(MediaOutcome.FAILED, result.path)

    def flush(self) -> None:
        for active in list(self._active_progress.values()):
            self._commit_coverage_run(active)
            self._persist_progress(active)

    def _resolve_target(self, target: TargetSpec) -> _ResolvedTarget:
        self._safe_emit(UIEvent(kind=EventKind.STAGE, message=f"Resolving {self._target_key(target)}"))
        if isinstance(target, UserTarget):
            uid = target.identifier if target.is_uid else self.context.resolve_nickname_to_uid(target.identifier)
            nickname = uid
            try:
                nickname = self.context.get_user_info(uid).nickname or uid
            except Exception:
                pass
            resolved = UserTarget(identifier=uid, is_uid=True)
            return _ResolvedTarget(resolved, self._target_key(resolved), {"uid": uid, "nickname": nickname})

        if isinstance(target, SuperTopicTarget):
            name = target.identifier
            cid = target.identifier
            if not target.is_containerid:
                topics = self.context.search_supertopic(target.identifier)
                if not topics:
                    raise TargetError(f"supertopic not found: {target.identifier}")
                cid = topics[0].containerid
                name = topics[0].name
            resolved = SuperTopicTarget(identifier=cid, is_containerid=True)
            return _ResolvedTarget(resolved, self._target_key(resolved), {"topic_name": name})

        if isinstance(target, SearchTarget):
            return _ResolvedTarget(target, self._target_key(target), {"keyword": target.keyword})

        if isinstance(target, MidTarget):
            return _ResolvedTarget(target, self._target_key(target), {"mid": target.mid})

        raise TargetError(f"unsupported target: {target}")

    def _build_dir(self, rt: _ResolvedTarget) -> Path:
        rel = build_directory(rt.target, pattern=self.dirname_pattern, **rt.dir_kwargs)
        path = self.output_dir / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _media_jobs(self, target_dir: Path, post: Post) -> list[tuple[MediaItem, Path]]:
        jobs: list[tuple[MediaItem, Path]] = []
        seen: set[Path] = set()
        for media in post.media_items:
            if media.media_type == "video" and self.no_videos:
                continue
            if media.media_type == "picture" and self.no_pictures:
                continue
            path = self._media_path(target_dir, post, media, seen)
            seen.add(path)
            jobs.append((media, path))
        return jobs

    def _media_path(self, target_dir: Path, post: Post, media: MediaItem, seen: set[Path]) -> Path:
        user = post.user
        name = media.filename_hint or f"{media.media_type}_{media.index}"
        filename = build_filename(
            self.filename_pattern,
            mid=post.mid,
            bid=post.bid,
            date=self._cst(post.created_at),
            text=post.text,
            index=media.index,
            type=media.media_type,
            name=name,
            nickname=user.nickname if user else "",
            uid=user.uid if user else "",
        )
        ext = Path(urlparse(media.url).path).suffix or (".mp4" if media.media_type == "video" else ".jpg")
        if not filename.lower().endswith(ext.lower()):
            filename = f"{filename}{ext}"
        path = target_dir / filename
        if path not in seen:
            return path
        stem, suffix = path.stem, path.suffix
        i = 1
        while True:
            candidate = path.with_name(f"{stem}_{i}{suffix}")
            if candidate not in seen:
                return candidate
            i += 1

    def _write_json(self, target_dir: Path, post: Post, output_compatible: bool) -> None:
        path = target_dir / f"{post.mid}.json"
        if output_compatible and path.exists() and path.stat().st_size > 0:
            return
        self._write_sidecar(path, json.dumps(post.raw, ensure_ascii=False, indent=2))
        try:
            self._apply_post_mtime(path, post)
        except Exception as mtime_exc:
            logger.exception("failed to apply sidecar mtime: %s", path)
            try:
                self._discard_failed_file(path)
            except Exception as cleanup_exc:
                logger.exception("failed cleanup after sidecar mtime error: %s", path)
                raise mtime_exc from cleanup_exc
            raise

    def _write_txt(self, target_dir: Path, post: Post, output_compatible: bool) -> None:
        if not self.post_metadata_txt:
            return
        path = target_dir / f"{post.mid}.txt"
        if output_compatible and path.exists() and path.stat().st_size > 0:
            return
        self._write_sidecar(path, self.post_metadata_txt)
        try:
            self._apply_post_mtime(path, post)
        except Exception as mtime_exc:
            logger.exception("failed to apply sidecar mtime: %s", path)
            try:
                self._discard_failed_file(path)
            except Exception as cleanup_exc:
                logger.exception("failed cleanup after sidecar mtime error: %s", path)
                raise mtime_exc from cleanup_exc
            raise

    def _hash_options(self) -> str:
        # Progress compatibility depends on output-shaping options plus
        # canonical boundary selection. Traversal-only flags such as count
        # and fast_update must keep existing resume and coverage reusable.
        payload = {
            "dirname": self.dirname_pattern,
            "filename": self.filename_pattern,
            "no_videos": self.no_videos,
            "no_pictures": self.no_pictures,
            "metadata_json": self.metadata_json,
            "post_metadata_txt": self.post_metadata_txt,
            "date_boundary": self._date_boundary_key,
            "id_boundary": self._id_boundary_key,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _load_progress(self, target_key: str):
        try:
            return self._progress.load(target_key)
        except Exception:
            return None

    def _persist_progress(self, active: _ActiveProgress) -> None:
        try:
            with self._progress.acquire_lock(active.target_key):
                self._progress.save(
                    active.target_key,
                    resume=None if self.no_resume else active.resume,
                    coverage=active.committed_coverage,
                    coverage_options_hash=active.coverage_options_hash,
                )
        except CheckpointError:
            raise
        except Exception as e:
            raise CheckpointError(str(e)) from e

    def _materialized_coverage(self, active: _ActiveProgress) -> list[CoverageInterval]:
        if active.run_start is None or active.run_end is None:
            return active.committed_coverage
        return ProgressStore.normalize_intervals([
            *active.committed_coverage,
            CoverageInterval(active.run_start, active.run_end)
        ])

    def _commit_coverage_run(self, active: _ActiveProgress) -> None:
        if self.no_coverage:
            return
        if active.run_start is not None and active.run_end is not None:
            active.committed_coverage = ProgressStore.normalize_intervals([
                *active.committed_coverage,
                CoverageInterval(active.run_start, active.run_end)
            ])
        active.run_start = None
        active.run_end = None
        active.monotonic_window.clear()

    def _seal_group(self, active: _ActiveProgress, stamp: datetime | None, ok: bool) -> None:
        if stamp is None or not ok or self.no_coverage:
            return

        if active.run_start is None:
            active.run_start = stamp
            active.run_end = stamp
            active.monotonic_window.append(stamp)
            return

        if len(active.monotonic_window) > 0 and stamp > active.monotonic_window[-1]:
            self._commit_coverage_run(active)
            active.run_start = stamp
            active.run_end = stamp
            active.monotonic_window.append(stamp)
            return

        active.run_start = stamp
        active.monotonic_window.append(stamp)

    def _finalize_coverage(self, active: _ActiveProgress, seal_last: bool = False) -> None:
        if self.no_coverage:
            return
        self._commit_coverage_run(active)

    def _boundary_action(self, target: TargetSpec, post: Post) -> str:
        if self._is_in_range(post):
            return "process"
        if isinstance(target, MidTarget):
            return "continue"
        if isinstance(target, UserTarget) and not self._is_pinned(post) and self._is_below_lower_bound(post):
            return "break"
        return "continue"

    def _is_in_range(self, post: Post) -> bool:
        if self.date_boundary is not None and not self.date_boundary.contains(self._boundary_datetime(post.created_at)):
            return False
        if self.id_boundary is not None and not self.id_boundary.contains(post.mid):
            return False
        return True

    def _is_below_lower_bound(self, post: Post) -> bool:
        boundary_dt = self._boundary_datetime(post.created_at)
        if self.date_boundary is not None and self.date_boundary.start is not None and boundary_dt.date() < self.date_boundary.start:
            return True
        if self.id_boundary is not None and self.id_boundary.start is not None:
            mid_value = parse_mid_value(post.mid)
            if mid_value is None:
                return False
            if mid_value < self.id_boundary.start:
                return True
        return False

    @staticmethod
    def _is_pinned(post: Post) -> bool:
        payload = post.raw.get("mblog", post.raw)
        return isinstance(payload, dict) and payload.get("mblogtype") == 2

    @staticmethod
    def _boundary_datetime(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=CST)

    @staticmethod
    def _target_key(target: TargetSpec) -> str:
        if isinstance(target, UserTarget):
            return f"u:{target.identifier}"
        if isinstance(target, SuperTopicTarget):
            return f"t:{target.identifier}"
        if isinstance(target, SearchTarget):
            return f"s:{target.keyword}"
        if isinstance(target, MidTarget):
            return f"m:{target.mid}"
        return str(target)

    @staticmethod
    def _cst(dt: datetime) -> datetime:
        return dt.astimezone(CST) if dt.tzinfo else dt.replace(tzinfo=CST)
