from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .context import WeiboLoaderContext
from .exceptions import CheckpointError, TargetError
from .naming import build_directory, build_filename
from .nodeiterator import CheckpointManager, NodeIterator
from .structures import MediaItem, MidTarget, Post, SearchTarget, SuperTopicTarget, TargetSpec, UserTarget
from .ui import DownloadResult, EventKind, MediaOutcome, NullSink, ProgressSink, UIEvent

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
_STREAM_READ_TIMEOUT = 60
_PER_MEDIA_TIMEOUT = 30


@dataclass(slots=True)
class _ResolvedTarget:
    target: TargetSpec
    key: str
    dir_kwargs: dict[str, str]


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
        latest_stamps: str | Path | None = None,
        metadata_json: bool = False,
        post_metadata_txt: str | None = None,
        max_workers: int = 4,
        no_resume: bool = False,
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
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._options_hash = self._hash_options()
        self._checkpoint = CheckpointManager(
            Path(checkpoint_dir).expanduser() if checkpoint_dir else self.output_dir / ".checkpoints",
            self._options_hash,
        )

        self._stamps_path = Path(latest_stamps).expanduser() if latest_stamps else None
        self._stamps = self._load_stamps()
        self._saved_stamps = self._serialize_stamps()
        self._active_iters: dict[str, NodeIterator] = {}

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
        self._save_stamps()
        return results

    def download_target(self, target: TargetSpec) -> bool:
        key = self._target_key(target)
        try:
            resolved = self._resolve_target(target)
        except Exception:
            logger.exception("resolve failed: %s", key)
            return False

        ck_key = self._ck_key(resolved.key)
        iterator = self._create_iterator(resolved.target)
        self._active_iters[ck_key] = iterator

        target_dir = self._build_dir(resolved)
        cutoff = self._stamps.get(resolved.key)

        processed = 0
        newest: datetime | None = None
        ok = True
        downloaded = 0
        skipped = 0
        failed = 0

        self._safe_emit(UIEvent(kind=EventKind.TARGET_START, target_key=resolved.key))

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as exe:
                for post in iterator:
                    if self.count and processed >= self.count:
                        break

                    created = self._cst(post.created_at)
                    if cutoff and created <= cutoff:
                        break

                    jobs = self._media_jobs(target_dir, post)
                    if self.fast_update and any(p.exists() and p.stat().st_size > 0 for _, p in jobs):
                        break

                    if self.metadata_json:
                        self._write_json(target_dir, post)
                    if self.post_metadata_txt:
                        self._write_txt(target_dir, post)

                    media_total = len(jobs)
                    media_done = 0
                    post_index = processed + 1
                    timed_out = False

                    future_to_path = {exe.submit(self._download, m.url, p): p for m, p in jobs}
                    post_timeout = max(60, media_total * _PER_MEDIA_TIMEOUT) if future_to_path else None
                    done_futures: set = set()

                    try:
                        for f in as_completed(future_to_path, timeout=post_timeout):
                            done_futures.add(f)
                            try:
                                result = f.result()
                            except Exception:
                                failed += 1
                                ok = False
                                media_done += 1
                                self._safe_emit(UIEvent(
                                    kind=EventKind.MEDIA_DONE, outcome=MediaOutcome.FAILED,
                                    media_done=media_done, media_total=media_total,
                                    post_index=post_index, filename=future_to_path[f].name,
                                ))
                                continue
                            if result.outcome == MediaOutcome.DOWNLOADED:
                                downloaded += 1
                            elif result.outcome == MediaOutcome.SKIPPED:
                                skipped += 1
                            else:
                                failed += 1
                                ok = False
                            media_done += 1
                            self._safe_emit(UIEvent(
                                kind=EventKind.MEDIA_DONE, outcome=result.outcome,
                                media_done=media_done, media_total=media_total,
                                post_index=post_index, filename=future_to_path[f].name,
                            ))
                    except FuturesTimeoutError:
                        timed_out = True
                        for f, path in future_to_path.items():
                            if f not in done_futures:
                                f.cancel()
                                failed += 1
                                ok = False
                                media_done += 1
                                self._safe_emit(UIEvent(
                                    kind=EventKind.MEDIA_DONE, outcome=MediaOutcome.FAILED,
                                    media_done=media_done, media_total=media_total,
                                    post_index=post_index, filename=path.name,
                                ))

                    processed += 1
                    if not timed_out and (newest is None or created > newest):
                        newest = created
                    if not timed_out:
                        self._save_ck(ck_key, iterator)
                    self._safe_emit(UIEvent(kind=EventKind.POST_DONE, posts_processed=processed))

            if newest and (cutoff is None or newest > cutoff):
                self._stamps[resolved.key] = newest
            self._clear_ck(ck_key)
            self._save_stamps()

            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE, target_key=resolved.key,
                posts_processed=processed, downloaded=downloaded,
                skipped=skipped, failed=failed, ok=ok,
            ))
            return ok

        except KeyboardInterrupt:
            self._safe_emit(UIEvent(kind=EventKind.INTERRUPTED, target_key=resolved.key))
            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE, target_key=resolved.key,
                posts_processed=processed, downloaded=downloaded,
                skipped=skipped, failed=failed, ok=False,
            ))
            self._handle_interrupt(ck_key, iterator, newest, cutoff)
            raise
        except Exception:
            logger.exception("download failed: %s", resolved.key)
            self._safe_emit(UIEvent(
                kind=EventKind.TARGET_DONE, target_key=resolved.key,
                posts_processed=processed, downloaded=downloaded,
                skipped=skipped, failed=failed, ok=False,
            ))
            self._handle_error(ck_key, iterator, newest, cutoff)
            return False
        finally:
            self._active_iters.pop(ck_key, None)

    def _create_iterator(self, target: TargetSpec) -> _PostIterator:
        if isinstance(target, UserTarget):
            uid = target.identifier if target.is_uid else self.context.resolve_nickname_to_uid(target.identifier)
            it = _PostIterator(lambda p: self.context.get_user_posts(uid, p), self._options_hash)
        elif isinstance(target, SuperTopicTarget):
            cid = target.identifier
            if not target.is_containerid:
                topics = self.context.search_supertopic(target.identifier)
                if not topics:
                    raise TargetError(f"supertopic not found: {target.identifier}")
                cid = topics[0].containerid
            it = _PostIterator(lambda p: self.context.get_supertopic_posts(cid, p), self._options_hash)
        elif isinstance(target, SearchTarget):
            it = _PostIterator(lambda p: self.context.search_posts(target.keyword, p), self._options_hash)
        elif isinstance(target, MidTarget):
            it = _PostIterator(lambda _: ([self.context.get_post_by_mid(target.mid)], None), self._options_hash, single=True)
        else:
            raise TargetError(f"unsupported target: {target}")

        if (state := self._load_ck(self._ck_key(self._target_key(target)))):
            it.thaw(state)
        return it

    def _download(self, url: str, dest: Path) -> DownloadResult:
        if dest.exists() and dest.stat().st_size > 0:
            return DownloadResult(MediaOutcome.SKIPPED, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        resp = None
        try:
            resp = self.context.request(
                "GET", url, bucket="media", allow_captcha=False, stream=True, retries=2,
                timeout=(self.context.req_timeout, _STREAM_READ_TIMEOUT),
            )
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
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

    def flush(self) -> None:
        for key, it in list(self._active_iters.items()):
            self._save_ck(key, it)
        self._save_stamps()

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

    def _write_json(self, target_dir: Path, post: Post) -> None:
        (target_dir / f"{post.mid}.json").write_text(json.dumps(post.raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_txt(self, target_dir: Path, post: Post) -> None:
        if not self.post_metadata_txt:
            return
        (target_dir / f"{post.mid}.txt").write_text(self.post_metadata_txt, encoding="utf-8")

    def _hash_options(self) -> str:
        payload = {
            "dirname": self.dirname_pattern,
            "filename": self.filename_pattern,
            "no_videos": self.no_videos,
            "no_pictures": self.no_pictures,
            "count": self.count,
            "fast_update": self.fast_update,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _load_stamps(self) -> dict[str, datetime]:
        if not self._stamps_path or not self._stamps_path.exists():
            return {}
        try:
            data = json.loads(self._stamps_path.read_text(encoding="utf-8"))
            return {k: datetime.fromisoformat(v) for k, v in data.items() if isinstance(v, str)}
        except Exception:
            return {}

    def _save_stamps(self) -> None:
        if not self._stamps_path:
            return
        payload = self._serialize_stamps()
        if payload == self._saved_stamps:
            return
        self._stamps_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._stamps_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._stamps_path)
            self._saved_stamps = payload
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _serialize_stamps(self) -> str:
        return json.dumps({k: self._cst(v).isoformat() for k, v in sorted(self._stamps.items())}, ensure_ascii=False, indent=2)

    def _save_ck(self, key: str, it: NodeIterator) -> None:
        if self.no_resume:
            return
        try:
            with self._checkpoint.acquire_lock(key):
                self._checkpoint.save(key, it.freeze())
        except RuntimeError as e:
            raise CheckpointError(str(e)) from e

    def _load_ck(self, key: str):
        if self.no_resume:
            return None
        try:
            return self._checkpoint.load(key)
        except Exception:
            return None

    def _clear_ck(self, key: str) -> None:
        if self.no_resume:
            return
        (self._checkpoint.dir / f"{key}.json").unlink(missing_ok=True)

    def _handle_interrupt(self, key: str, it: NodeIterator, newest: datetime | None, cutoff: datetime | None):
        self._save_ck(key, it)
        if newest and (cutoff is None or newest > cutoff):
            self._stamps[key] = newest
        self._save_stamps()

    def _handle_error(self, key: str, it: NodeIterator, newest: datetime | None, cutoff: datetime | None):
        self._save_ck(key, it)
        if newest and (cutoff is None or newest > cutoff):
            self._stamps[key] = newest
        self._save_stamps()

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

    def _ck_key(self, target_key: str) -> str:
        return hashlib.sha1(target_key.encode()).hexdigest()[:16]

    @staticmethod
    def _cst(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=CST)
