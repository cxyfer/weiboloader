## CHANGED Requirements

### Requirement: Streaming read timeout for media downloads

The `_download()` method SHALL use a `(connect_timeout, read_timeout)` tuple when making streaming HTTP requests, with `read_timeout = 60` seconds.

#### Scenario: Normal streaming download
- **WHEN** `_download()` calls `context.request()` with `stream=True`
- **THEN** it SHALL pass `timeout=(self.context.req_timeout, 60)`

#### Scenario: CDN stops sending data mid-stream
- **WHEN** `iter_content()` blocks for >60s waiting for the next chunk
- **THEN** `requests` SHALL raise a timeout exception
- **AND** `_download()` SHALL catch the exception, log it, clean up `.part` file, and return `DownloadResult(MediaOutcome.FAILED, dest)`

#### Scenario: Non-streaming requests unaffected
- **WHEN** API requests are made via `context.request()` without explicit timeout override
- **THEN** the scalar `self.req_timeout` (20s) SHALL continue to be used

#### Constraint: No new dependencies
- The implementation SHALL use only `requests` native timeout tuple support

### Requirement: .part file cleanup on download failure

The `_download()` method SHALL delete the `.part` temporary file when a download fails for any reason.

#### Scenario: Exception during download
- **WHEN** any exception occurs during `_download()` (including timeout)
- **THEN** `part.unlink(missing_ok=True)` SHALL be called before returning `DownloadResult(FAILED, dest)`

#### Scenario: .part file does not exist
- **WHEN** exception occurs before `.part` file is created
- **THEN** `unlink(missing_ok=True)` SHALL succeed silently

### Requirement: Global timeout for per-post media download group

The `as_completed()` call SHALL include a timeout parameter to prevent indefinite blocking when download threads hang.

#### Timeout formula
- `post_timeout = max(60, media_count * 30)` seconds
- When `media_count == 0`, no futures are submitted and no timeout is needed

#### Scenario: All downloads complete within timeout
- **WHEN** all futures complete before `post_timeout`
- **THEN** behavior SHALL be identical to current implementation (no change)

#### Scenario: Some downloads exceed timeout
- **WHEN** `as_completed()` raises `concurrent.futures.TimeoutError`
- **THEN** the system SHALL:
  1. Mark `timed_out = True`
  2. Iterate remaining (non-yielded) futures
  3. Call `future.cancel()` on each (best-effort; running tasks cannot be canceled)
  4. Increment `failed` by the count of non-yielded futures
  5. Set `ok = False`
  6. Emit `MEDIA_DONE(FAILED)` for each non-yielded future with correct `media_done`, `media_total`, `post_index`, `filename`
  7. Continue to next POST normally

#### Scenario: Zero media items
- **WHEN** a POST has no media items matching the filter
- **THEN** no futures are submitted, no timeout applies, processing continues normally

### Requirement: Checkpoint hold on global timeout

When `as_completed` global timeout occurs, the checkpoint SHALL NOT be advanced for that POST.

#### Scenario: Global timeout triggers
- **WHEN** `timed_out == True` after processing a POST's media
- **THEN** `_save_ck()` SHALL be skipped for that POST
- **AND** `processed` counter SHALL still be incremented (for accurate POST_DONE event)
- **AND** the POST SHALL be reprocessed on the next run

#### Scenario: Individual download failure (no global timeout)
- **WHEN** some media items fail individually (R2 timeout or network error) but `as_completed` completes normally
- **THEN** `_save_ck()` SHALL proceed normally (checkpoint advances)
- **AND** already-downloaded files will be SKIPPED on rerun

#### Constraint: Eventual consistency
- Files downloaded before the global timeout remain on disk
- On rerun, the iterator resumes from the last saved checkpoint
- `_download()` skips files where `dest.exists() and dest.stat().st_size > 0`

---

## PBT Properties

### P2.1: Timeout enforcement
- **Property**: `∀ _download() call with stream=True, the timeout passed to context.request() == (req_timeout, 60)`
- **Falsification**: Mock context.request, verify timeout kwarg shape and values

### P2.2: Part file cleanup idempotency
- **Property**: `∀ failed _download(), dest.with_suffix('.part').exists() == False after return`
- **Falsification**: Create .part file before call, inject exception, verify .part removed

### P2.3: Non-interference with non-streaming requests
- **Property**: `∀ context.request() call where stream is not True, timeout is scalar`
- **Falsification**: Trace all context.request calls during a full target download, verify only _download calls use tuple

### P3.1: Bounded duration
- **Property**: `∀ POST with N media items, as_completed returns within max(60, N*30) + ε seconds`
- **Falsification**: Mock futures that never complete, verify TimeoutError raised within expected window

### P3.2: Event completeness
- **Property**: `∀ POST, count(MEDIA_DONE events) == media_total` (including timeout-failed items)
- **Falsification**: Mix completing and hanging futures, collect events, verify count matches total

### P3.3: Failed accounting
- **Property**: `downloaded + skipped + failed == media_total` for every POST (including timed-out)
- **Falsification**: Run with various timeout scenarios, verify invariant on TARGET_DONE event

### P3.4: Checkpoint hold on timeout
- **Property**: `∀ POST where as_completed timed out, _save_ck() is NOT called`
- **Falsification**: Mock _save_ck, inject TimeoutError, verify _save_ck not called for that POST

### P3.5: Checkpoint advance on normal failure
- **Property**: `∀ POST where all futures completed (some FAILED), _save_ck() IS called`
- **Falsification**: Mock _download to return FAILED, verify _save_ck called normally

### PC.1: NullSink compatibility
- **Property**: `∀ UIEvent variant (with/without new fields), NullSink.emit() does not raise`
- **Falsification**: Construct all possible UIEvent configurations, call NullSink.emit()

### PC.2: Media done monotonicity
- **Property**: `∀ sequence of MEDIA_DONE events within a POST, media_done values are strictly increasing`
- **Falsification**: Collect media_done from events, verify `events[i].media_done < events[i+1].media_done`

### PC.3: Stat consistency at target level
- **Property**: `∀ TARGET_DONE event, downloaded + skipped + failed == Σ(media_total across all POSTs)`
- **Falsification**: Run full target download with mixed outcomes, verify TARGET_DONE aggregates

---

## ADDED Requirements (fix-download-hang-timeout-retry)

### Requirement: Per-file wall-clock timeout

`_download()` MUST return within `_MEDIA_DOWNLOAD_TIMEOUT = 60` seconds of being invoked,
regardless of CDN throughput. Implemented via `_get_socket(resp)` + `sock.settimeout(remaining)`
deadline loop; deadline is set before `context.request()` call.

#### Scenario: Stalled CDN
- **GIVEN** a streaming response that delivers < 1 byte/second of body data
- **WHEN** `_download()` is called
- **THEN** it returns within 60 + epsilon seconds with `MediaOutcome.FAILED`

#### Scenario: Trickling CDN (bypasses per-chunk timeout)
- **GIVEN** a streaming response that sends 1 byte every 30 seconds (within `_STREAM_READ_TIMEOUT`)
- **WHEN** `_download()` is called
- **THEN** it returns within 60 + epsilon seconds with `MediaOutcome.FAILED`

#### Constraint: Mechanism
- `_MEDIA_DOWNLOAD_TIMEOUT = 60` constant in `weiboloader.py`
- `_get_socket(resp)` extracts socket via `resp.raw.fp.fp.raw._sock`; returns `None` if unavailable
- `sock.settimeout(remaining)` called before first chunk read and after each yielded chunk
- `TimeoutError` raised when `remaining <= 0`; caught by existing `except Exception`

### Requirement: No .part file left behind on timeout

If the per-file timeout fires, the `.part` file MUST be removed. Enforced by existing
`except Exception: part.unlink(missing_ok=True)` — unchanged.

---

## ADDED PBT Properties (fix-download-hang-timeout-retry)

### P-ft.1: Bounded execution time
- **Property**: `wall_clock(_download end) - wall_clock(_download start) ≤ _MEDIA_DOWNLOAD_TIMEOUT + epsilon`
- **Boundary**: `epsilon = 5s`
- **Falsification**: Mock server stalls indefinitely; measure wall clock

### P-ft.2: No partial file leakage on timeout
- **Property**: After `_download()` returns `FAILED`, `dest.with_suffix(".part").exists() == False`
- **Falsification**: Interrupt mock response at random byte offset; check filesystem

### P-ft.3: Thread isolation
- **Property**: `sock.settimeout()` in one thread does not affect socket in another thread
- **Falsification**: Two concurrent `_download()` calls; verify independent timeout behavior
