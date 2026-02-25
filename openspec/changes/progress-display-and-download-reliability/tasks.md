# Implementation Tasks: progress-display-and-download-reliability

> Zero-decision implementation plan derived from multi-model analysis (Codex + Gemini).
> Each task = exact code changes + unit tests + PBT verification.
> Tasks within the same phase have no mutual dependencies and CAN be parallelized.

---

## Phase 1: Data Model Extension (no dependencies)

### Task 1.1: Extend UIEvent with filename and post_index
- **File**: `weiboloader/ui.py`
- **Lines**: 31-43
- **Impl**:
  - Append two fields after `ok: bool | None = None`:
    ```python
    filename: str | None = None
    post_index: int | None = None
    ```
- **Tests** (`tests/test_progress_ui.py`):
  - UIEvent constructed with only legacy fields → `filename is None`, `post_index is None`
  - UIEvent constructed with all fields → values preserved
  - NullSink.emit() accepts UIEvent with new fields without error
  - PBT P1.1: backward compat (no new fields → identical behavior)
  - PBT PC.1: NullSink accepts all UIEvent variants
- [x] Add fields to UIEvent
- [x] Add unit tests

---

## Phase 2: Core Logic Changes (depends on Phase 1)

### Task 2.1: Add .part cleanup and streaming read timeout to _download()
- **File**: `weiboloader/weiboloader.py`
- **Lines**: 251-272
- **Impl**:
  - Add module-level constant: `_STREAM_READ_TIMEOUT = 60`
  - Change line 258 from:
    ```python
    resp = self.context.request("GET", url, bucket="media", allow_captcha=False, stream=True, retries=2)
    ```
    to:
    ```python
    resp = self.context.request(
        "GET", url, bucket="media", allow_captcha=False, stream=True, retries=2,
        timeout=(self.context.req_timeout, _STREAM_READ_TIMEOUT),
    )
    ```
  - In the `except Exception` block (line 267-268), add before `return`:
    ```python
    part.unlink(missing_ok=True)
    ```
- **Tests** (`tests/test_weiboloader.py`):
  - Mock `context.request` → verify `timeout=(20, 60)` kwarg
  - Mock `iter_content` to raise `ReadTimeout` → verify returns `FAILED` + `.part` removed
  - Mock `iter_content` to raise generic `Exception` → verify returns `FAILED` + `.part` removed
  - Test `.part` not existing before exception → `unlink(missing_ok=True)` succeeds silently
  - PBT P2.1: timeout tuple shape
  - PBT P2.2: .part cleanup idempotency
- [x] Add `_STREAM_READ_TIMEOUT` constant
- [x] Pass timeout tuple to context.request
- [x] Add .part cleanup in except block
- [x] Add unit tests

### Task 2.2: Update RichSink to display enriched status bar
- **File**: `weiboloader/ui.py`
- **Lines**: 108-112
- **Impl**:
  - Replace MEDIA_DONE handler from:
    ```python
    description=f"Media {event.media_done}/{event.media_total}",
    ```
    to:
    ```python
    parts: list[str] = []
    if event.post_index is not None:
        parts.append(f"[#{event.post_index}]")
    parts.append(f"Media {event.media_done}/{event.media_total}")
    if event.filename:
        parts.append(f"- {escape(event.filename)}")
    description = " ".join(parts)
    ```
    Then use `description` in the `self._progress.update()` call.
  - Note: `escape` is already imported at line 10
- **Tests** (`tests/test_progress_ui.py`):
  - MEDIA_DONE with `post_index=3, filename="image.jpg"` → description matches `[#3] Media 1/5 - image.jpg`
  - MEDIA_DONE with `post_index=None, filename=None` → description matches `Media 1/5`
  - MEDIA_DONE with `filename="[bold]evil[/bold]"` → escape renders safely
  - PBT P1.2: Rich markup safety with arbitrary filenames
  - PBT P1.3: format string regex invariant
- [x] Update RichSink._handle() MEDIA_DONE branch
- [x] Add unit tests

---

## Phase 3: Orchestrator Integration (depends on Phase 2)

### Task 3.1: Refactor download loop with future-to-path mapping, global timeout, and checkpoint hold
- **File**: `weiboloader/weiboloader.py`
- **Lines**: 9, 161-193
- **Impl**:
  - Add import at line 9: append `TimeoutError as FuturesTimeoutError` to the `concurrent.futures` import
  - Add module-level constant: `_PER_MEDIA_TIMEOUT = 30`
  - Replace lines 161-193 with:
    ```python
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
    if newest is None or created > newest:
        newest = created
    if not timed_out:
        self._save_ck(ck_key, iterator)
    self._safe_emit(UIEvent(kind=EventKind.POST_DONE, posts_processed=processed))
    ```
- **Tests** (`tests/test_weiboloader.py`):
  - Normal flow: all futures complete → events carry `post_index` and `filename`, checkpoint saved
  - Timeout flow: mock futures that never complete → `FuturesTimeoutError` raised, remaining counted as FAILED, checkpoint NOT saved
  - Mixed flow: some complete + timeout → partial results + FAILED for remainder
  - Zero media: empty jobs list → no timeout, no futures, checkpoint saved
  - Event completeness: count(MEDIA_DONE) == media_total in all scenarios
  - `cancel()` called on non-done futures
  - PBT P3.1: bounded duration
  - PBT P3.2: event completeness
  - PBT P3.3: failed accounting (downloaded + skipped + failed == media_total)
  - PBT P3.4: checkpoint hold on timeout
  - PBT P3.5: checkpoint advance on normal failure
  - PBT PC.2: media_done monotonicity
- [x] Add `FuturesTimeoutError` import and `_PER_MEDIA_TIMEOUT` constant
- [x] Refactor download loop with future_to_path mapping
- [x] Add as_completed timeout
- [x] Add checkpoint hold logic
- [x] Add unit tests

---

## Phase 4: Integration Verification (depends on Phase 3)

### Task 4.1: End-to-end integration test
- **File**: `tests/test_integration.py` (extend existing)
- **Impl**:
  - Add test case: mock target with multiple posts, each with multiple media items
  - Use `CollectorSink` to capture all events
  - Verify event sequence: TARGET_START → (MEDIA_DONE* → POST_DONE)* → TARGET_DONE
  - Verify TARGET_DONE stats: `downloaded + skipped + failed == total media across all posts`
  - Verify all MEDIA_DONE events have `post_index` and `filename`
  - PBT PC.3: stat consistency at target level
- [x] Add integration test for enriched events
- [x] Add integration test for timeout scenario
- [x] Verify existing tests still pass
