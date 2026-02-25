# Proposal: Fix Download Hang — Per-File Timeout & Connection Pool Tuning

## Context

### User Problem
`weiboloader` occasionally hangs mid-download (e.g., "Media 6/9") with the spinner still
spinning but no progress. The process neither errors out nor advances, requiring manual
interruption (Ctrl-C).

### Observed Symptom
```
⠹ [#9] Media 6/9 - 2026-02-01_007swU0pgy1i9va8yazbaj31r02c3kjl.jpg
```

The `⠹` spinner continues rotating but media count stays frozen.

### Root Cause Analysis

The `_download()` method calls `requests.Response.iter_content()` in a chunk loop
(`weiboloader.py:289`). The HTTP `timeout` tuple `(req_timeout, _STREAM_READ_TIMEOUT)`
controls only the time between *consecutive chunks*, **not** the total streaming duration.

**If the CDN server stalls mid-stream** (sends headers then pauses data delivery), the
read timeout counter resets with each byte received. A malformed or slow CDN response
can keep `iter_content()` alive indefinitely as long as *any* data trickles through,
even if actual throughput has collapsed to near zero.

Additionally: `requests.Session` uses the default `HTTPAdapter` with no explicit
connection pool size, so a `max_workers=4` executor can create up to 4 simultaneous
connections without any idle-connection ceiling.

### Confirmed Code Locations

| Location | Concern |
|---|---|
| `weiboloader.py:24` | `_STREAM_READ_TIMEOUT = 60` — inter-chunk timeout only |
| `weiboloader.py:25` | `_PER_MEDIA_TIMEOUT = 30` — post-level budget per file |
| `weiboloader.py:169` | `post_timeout = max(60, media_total * 30)` — executor-level timeout |
| `weiboloader.py:286` | `timeout=(req_timeout, _STREAM_READ_TIMEOUT)` passed to requests |
| `weiboloader.py:289` | `iter_content(chunk_size=64*1024)` — no total-duration guard |
| `context.py:54` | `requests.Session()` created with default `HTTPAdapter` |

## Requirements

### R1 — Per-File Total Download Timeout
Each `_download()` call MUST complete (or fail) within a bounded wall-clock duration.
The bound should be configurable but default to a sensible value (e.g., 120 s).

**Scenario:** A single CDN file hangs at 5 MB of 8 MB for longer than the per-file
limit → the download raises an exception, `.part` is cleaned up, outcome is `FAILED`.

**Constraint:** Must not use `threading.Thread` + `join(timeout)` for this guard.
The existing `ThreadPoolExecutor` already owns the thread. Use a `requests`-compatible
mechanism (`socket.settimeout` or wrapping `iter_content` with a deadline) instead.

### R2 — Chunk-Level Stall Detection
The existing 60 s read timeout governs the wait for the *first* chunk and between
chunks. This value must remain, but an **additional** total-transfer wall-clock limit
(R1) is the primary fix. Both work together.

**Constraint:** Do not remove or reduce `_STREAM_READ_TIMEOUT`. It defends against
per-chunk stalls; R1 defends against cumulative drift.

### R3 — `iter_content` Exception Propagates Cleanly
If the per-file limit fires (via socket timeout, requests exception, or any mechanism),
the existing `except Exception` in `_download()` MUST catch it, clean up `.part`, and
return `MediaOutcome.FAILED` — no thread leak, no file corruption.

**Constraint:** The current cleanup path (`weiboloader.py:296-298`) is correct and must
not be altered.

### R4 — No Regression on Happy Path
All existing tests must pass. Download, skip, and failed outcomes must continue to
emit the correct `UIEvent`.

**Constraint:** `DownloadResult`, `MediaOutcome`, and `UIEvent` dataclass signatures
must remain unchanged.

### R5 — Connection Pool Tuning (Optional Hardening)
If the root cause analysis confirms that socket-level stalls are the primary driver,
mount an explicit `HTTPAdapter` on the session to limit pool size and set
`pool_block=False` so connection acquisition does not itself block indefinitely.

**Scope:** Implement only if R1 alone is insufficient or if pool exhaustion is
confirmed through testing.

## Success Criteria

1. **SC-1**: `weiboloader` no longer hangs indefinitely on a stalled CDN file.
   Observable: process completes (or prints `FAILED` for the stalled file) within
   `per_file_timeout` seconds of the stall beginning.

2. **SC-2**: All existing unit tests pass without modification.

3. **SC-3**: A stalled download emits `MediaOutcome.FAILED` and the corresponding
   `.part` file is removed.

4. **SC-4**: Non-stalled downloads complete normally with `MediaOutcome.DOWNLOADED`.

5. **SC-5**: The fix adds ≤ 10 net lines of production code.

## Scope

**In scope:**
- `weiboloader/weiboloader.py` — `_download()` and constants
- `weiboloader/context.py` — `HTTPAdapter` tuning (R5 only, conditional)

**Out of scope:**
- Retry count changes (currently `retries=2` for media — acceptable as-is)
- Checkpoint granularity (per-media checkpoints — separate concern)
- Hash verification of downloaded files
- `max_workers` changes

## Open Questions

| # | Question | Default Assumption |
|---|---|---|
| Q1 | Acceptable per-file timeout? | 120 s |
| Q2 | Should per-file timeout be CLI-configurable? | No (hardcoded constant) |
| Q3 | Is R5 (pool tuning) in scope for this change? | Conditional on testing |
