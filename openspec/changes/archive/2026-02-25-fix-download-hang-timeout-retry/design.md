# Design: Fix Download Hang — Per-File Wall-Clock Timeout

## Decision Log

| # | Decision | Rationale |
|---|---|---|
| D1 | Timeout scope = entire `_download()` function | User requirement: guard the whole operation including file I/O, not just streaming |
| D2 | Mechanism = `socket.settimeout()` with deadline loop | Least invasive; reuses existing socket; no new dependencies |
| D3 | Fallback on missing socket path = raise `TimeoutError` after deadline | Defensive: if socket is inaccessible, fail safe on timeout |
| D4 | `retries=2` unchanged | Retry is for header-level errors; per-chunk hang is orthogonal |
| D5 | Constant name = `_MEDIA_DOWNLOAD_TIMEOUT = 60` | Gemini recommendation; co-located with `_STREAM_READ_TIMEOUT` |
| D6 | Deadline starts at top of `_download()` | Covers all paths including retry inside `context.request()` |

## Technical Design

### Mechanism

`requests` streaming uses urllib3 under the hood. The live TCP socket is accessible via
`resp.raw.fp.fp.raw._sock` (or similar private path). Setting `socket.settimeout(remaining)`
before each `iter_content` chunk-read enforces a per-chunk time budget that **shrinks as wall
clock advances**, converting per-chunk timeouts into a total deadline.

```
deadline = time.monotonic() + _MEDIA_DOWNLOAD_TIMEOUT

for chunk in iter_content():
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("per-file download timeout")
    sock.settimeout(remaining)   # replaces previous chunk timeout
    # ... write chunk
```

If the socket path is unavailable (mocked transport, custom adapter), the deadline check
`if remaining <= 0` still fires and raises `TimeoutError` — no hang, just a slightly later
detection (at the next chunk boundary rather than mid-read).

### Socket Path Extraction

```python
def _get_socket(resp: requests.Response):
    """Extract underlying socket from a streaming response. Returns None if unavailable."""
    try:
        raw = resp.raw
        # urllib3 ≥1.26: HTTPResponse → fp → socket
        return raw.fp.fp.raw._sock
    except AttributeError:
        pass
    try:
        return raw._original_response.fp.raw._sock
    except AttributeError:
        return None
```

The extraction happens **once** after `context.request()` returns and before the chunk loop.

### Modified `_download()` Structure

```python
_MEDIA_DOWNLOAD_TIMEOUT = 60   # total wall-clock seconds per file

def _download(self, url: str, dest: Path) -> DownloadResult:
    if dest.exists() and dest.stat().st_size > 0:
        return DownloadResult(MediaOutcome.SKIPPED, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    resp = None
    deadline = time.monotonic() + _MEDIA_DOWNLOAD_TIMEOUT      # NEW
    try:
        resp = self.context.request(
            "GET", url, bucket="media", allow_captcha=False, stream=True, retries=2,
            timeout=(self.context.req_timeout, _STREAM_READ_TIMEOUT),
        )
        sock = _get_socket(resp)                                # NEW
        with open(part, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                remaining = deadline - time.monotonic()         # NEW
                if remaining <= 0:                              # NEW
                    raise TimeoutError("download timeout")      # NEW
                if sock is not None:                            # NEW
                    sock.settimeout(remaining)                  # NEW
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

### Constants Location

Both constants live in `weiboloader/weiboloader.py` near line 24:

```python
_STREAM_READ_TIMEOUT = 60    # existing: per-chunk read timeout (seconds)
_MEDIA_DOWNLOAD_TIMEOUT = 60  # new: total per-file wall-clock limit (seconds)
_PER_MEDIA_TIMEOUT = 30      # existing: post-level budget per media item
```

Note: `_STREAM_READ_TIMEOUT` and `_MEDIA_DOWNLOAD_TIMEOUT` are both 60 but serve different
roles. They may diverge independently in the future.

### No Changes Required

- `context.py` — no modification needed (R5 out of scope)
- `ui.py` — no modification needed
- `DownloadResult`, `MediaOutcome`, `UIEvent` — signatures unchanged

## Interaction with Existing Systems

| System | Interaction |
|---|---|
| `as_completed(timeout=post_timeout)` | Unchanged; post-level timeout remains the outer guard |
| `context.request(retries=2)` | Unchanged; retry is for connection/header errors, not body streaming |
| `.part` cleanup in `except Exception` | Unchanged; correctly fires on `TimeoutError` |
| `resp.close()` in `finally` | Unchanged; always fires, reclaims socket back to pool |
| `ThreadPoolExecutor` threads | Each thread owns its own `resp`/socket; no sharing |

## Worst-Case Duration

With `retries=2` and `_MEDIA_DOWNLOAD_TIMEOUT=60`:
- First attempt may take up to 60 s (deadline fires)
- `context.request()` retry loop wraps only `session.request()` — body timeout is **outside** the retry loop
- Therefore: retries do not compound with body timeout; only one 60 s window applies per `_download()` invocation

Actual worst case: `req_timeout * (retries+1) + 60` for header timeouts + one body timeout.
With `req_timeout=20`, `retries=2`: `20*3 + 60 = 120` s per `_download()` call.
