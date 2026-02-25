# Tasks: Fix Download Hang — Per-File Wall-Clock Timeout

## Implementation Plan

All tasks operate on `weiboloader/weiboloader.py` only. No other file requires modification.

---

### T1 — Add `_MEDIA_DOWNLOAD_TIMEOUT` constant

**File**: `weiboloader/weiboloader.py`
**Location**: Line ~25, alongside `_STREAM_READ_TIMEOUT` and `_PER_MEDIA_TIMEOUT`

**Change**:
```python
# Before (line 24-25):
_STREAM_READ_TIMEOUT = 60
_PER_MEDIA_TIMEOUT = 30

# After:
_STREAM_READ_TIMEOUT = 60
_MEDIA_DOWNLOAD_TIMEOUT = 60  # total wall-clock limit per file
_PER_MEDIA_TIMEOUT = 30
```

**Acceptance**: Constant exists at module level; type is `int`.

---

### T2 — Add `_get_socket()` helper function

**File**: `weiboloader/weiboloader.py`
**Location**: Near the `_download()` method (module-level or staticmethod)

**Change** (add before `class WeiboLoader` or as a module-level function):
```python
def _get_socket(resp: requests.Response):
    """Extract underlying TCP socket from a streaming response. Returns None if unavailable."""
    try:
        return resp.raw.fp.fp.raw._sock
    except AttributeError:
        pass
    try:
        return resp.raw._original_response.fp.raw._sock
    except AttributeError:
        return None
```

**Acceptance**: Returns a socket object for a real `requests` streaming response; returns
`None` for mock responses.

---

### T3 — Modify `_download()` to enforce deadline

**File**: `weiboloader/weiboloader.py`
**Location**: Lines 277-302

**Change** (exact diff):
```diff
+import time

 def _download(self, url: str, dest: Path) -> DownloadResult:
     if dest.exists() and dest.stat().st_size > 0:
         return DownloadResult(MediaOutcome.SKIPPED, dest)
     dest.parent.mkdir(parents=True, exist_ok=True)
     part = dest.with_suffix(".part")
     resp = None
+    deadline = time.monotonic() + _MEDIA_DOWNLOAD_TIMEOUT
     try:
         resp = self.context.request(
             "GET", url, bucket="media", allow_captcha=False, stream=True, retries=2,
             timeout=(self.context.req_timeout, _STREAM_READ_TIMEOUT),
         )
+        sock = _get_socket(resp)
         with open(part, "wb") as f:
             for chunk in resp.iter_content(chunk_size=64 * 1024):
+                remaining = deadline - time.monotonic()
+                if remaining <= 0:
+                    raise TimeoutError("download timeout")
+                if sock is not None:
+                    sock.settimeout(remaining)
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
```

**Note**: `import time` goes in the existing imports block at the top of the file (check
if already imported first).

**Acceptance**:
- `_download()` returns within 60 + epsilon seconds for a stalled mock server
- `.part` is absent after timeout
- Existing happy-path tests pass

---

### T4 — Add unit tests for timeout behavior

**File**: `tests/test_download_timeout.py` (new file) or append to existing download tests

**Required test cases**:

1. **test_download_stalled_server**: Mock HTTP server stalls indefinitely after headers.
   Assert `result.outcome == MediaOutcome.FAILED` and wall-clock ≤ 65 s.

2. **test_download_trickling_server**: Mock server sends 1 byte every 30 s (bypasses
   `_STREAM_READ_TIMEOUT`). Assert `result.outcome == MediaOutcome.FAILED` within 65 s.

3. **test_download_no_part_after_timeout**: After stalled server timeout, assert
   `dest.with_suffix(".part").exists() == False`.

4. **test_download_fast_succeeds**: Normal mock server delivers full content in < 1 s.
   Assert `result.outcome == MediaOutcome.DOWNLOADED`.

**Acceptance**: All 4 tests pass; test runtime < 70 s per test case.

---

## Verification Checklist

- [x] T1: `_MEDIA_DOWNLOAD_TIMEOUT = 60` exists in module scope
- [x] T2: `_get_socket()` handles `AttributeError` silently
- [x] T3: `deadline` set at top of `_download()`, before `context.request()`
- [x] T3: `remaining <= 0` guard fires before `sock.settimeout()`
- [x] T3: `sock.settimeout()` only called when `sock is not None`
- [x] T3: `import time` present in imports
- [x] T4: All 4 test cases implemented and passing
- [x] Net diff: ≤ 10 lines added to `weiboloader.py`
- [x] `pytest` green (no regressions)
