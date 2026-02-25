# Specs: Fix Download Hang — Per-File Wall-Clock Timeout

## REQ-1: Per-File Total Timeout

**Requirement**: `_download()` MUST return within `_MEDIA_DOWNLOAD_TIMEOUT` seconds of
being invoked, regardless of CDN throughput.

**Constraint**: `_MEDIA_DOWNLOAD_TIMEOUT = 60` (seconds, integer constant).

**Scenario — stalled CDN**:
```
GIVEN a streaming HTTP response that sends headers immediately
AND then delivers < 1 byte/second of body data
WHEN _download() is called
THEN it returns within 60 + epsilon seconds
AND the return value is DownloadResult(MediaOutcome.FAILED, dest)
```

**Scenario — trickling CDN (bypasses per-chunk timeout)**:
```
GIVEN a streaming HTTP response that sends 1 byte every 30 seconds
AND _STREAM_READ_TIMEOUT = 60 (per-chunk timeout would NOT fire)
WHEN _download() is called
THEN it returns within 60 + epsilon seconds
AND the return value is DownloadResult(MediaOutcome.FAILED, dest)
```

---

## REQ-2: No .part File Left Behind on Timeout

**Requirement**: If the per-file timeout fires, the `.part` file MUST be removed.

**Scenario**:
```
GIVEN _download() is interrupted by TimeoutError at any point during body streaming
WHEN the exception is handled
THEN dest.with_suffix(".part").exists() == False
AND dest.exists() == False (partial file never becomes the destination)
```

**Constraint**: The existing `except Exception: part.unlink(missing_ok=True)` path handles
this; no new cleanup logic needed.

---

## REQ-3: Happy Path Unaffected

**Requirement**: Fast, complete downloads MUST complete with `MediaOutcome.DOWNLOADED`.

**Scenario**:
```
GIVEN a streaming HTTP response that delivers the full body in < 60 seconds
WHEN _download() is called
THEN result.outcome == MediaOutcome.DOWNLOADED
AND dest.exists() == True
AND dest.with_suffix(".part").exists() == False
```

---

## REQ-4: Skip Behavior Unchanged

**Requirement**: Files that already exist (size > 0) MUST still be skipped without any
HTTP request.

**Scenario**:
```
GIVEN dest.exists() == True AND dest.stat().st_size > 0
WHEN _download() is called
THEN result.outcome == MediaOutcome.SKIPPED
AND no HTTP request is made
AND deadline logic is never evaluated
```

---

## REQ-5: Response Always Closed

**Requirement**: `resp.close()` MUST be called regardless of timeout or exception.

**Constraint**: Enforced by existing `finally: if resp: resp.close()` — unchanged.

---

## PBT Properties

### P1 — Bounded Execution Time (Monotone Upper Bound)
```
INVARIANT: wall_clock(end) - wall_clock(start) <= _MEDIA_DOWNLOAD_TIMEOUT + epsilon
FOR ALL download scenarios including: stalled, trickling, network error
FALSIFICATION: Feed a mock server that stalls indefinitely; measure wall clock.
BOUNDARY: epsilon = 5s (socket settimeout granularity + OS scheduler jitter)
```

### P2 — No Partial File Leakage (Idempotency of Failure)
```
INVARIANT: after _download() returns MediaOutcome.FAILED:
  - dest.with_suffix(".part").exists() == False
  - dest.exists() == False (or was pre-existing, unchanged)
FALSIFICATION: Interrupt mock response at random byte offset; check filesystem state.
BOUNDARY: Applies even if TimeoutError fires between f.write() and os.replace().
```

### P3 — Destination File Integrity (Round-Trip)
```
INVARIANT: after _download() returns MediaOutcome.DOWNLOADED:
  - dest.exists() == True
  - dest.stat().st_size == Content-Length (or actual bytes received)
  - dest.with_suffix(".part").exists() == False
FALSIFICATION: Download a known-size file; compare size.
```

### P4 — Skip Idempotency
```
INVARIANT: calling _download() N times on an already-downloaded dest:
  - all N calls return MediaOutcome.SKIPPED
  - dest content is unchanged
  - no HTTP request made
FALSIFICATION: Pre-create dest; call _download() twice; assert no request was issued.
```

### P5 — Thread Isolation
```
INVARIANT: socket.settimeout() in thread T1 does not affect socket in thread T2
  (each thread has its own resp/socket handle)
FALSIFICATION: Run two concurrent _download() calls; verify independent timeouts.
```

---

## Acceptance Criteria

| ID | Criterion | Verifiable |
|---|---|---|
| AC-1 | Stalled CDN exits within 65 s | Unit test with mock server + `time.monotonic()` |
| AC-2 | `.part` absent after any failure | Unit test: check `dest.with_suffix(".part").exists()` |
| AC-3 | Fast download returns DOWNLOADED | Unit test: normal mock server |
| AC-4 | Existing file returns SKIPPED | Unit test: pre-create dest |
| AC-5 | Net code change ≤ 10 lines | Code review: diff `weiboloader.py` |
| AC-6 | All existing tests pass | `pytest` green |
