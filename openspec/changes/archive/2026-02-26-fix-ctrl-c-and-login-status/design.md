## Context

The program uses `requests` + `ThreadPoolExecutor` for synchronous media downloads. On Windows, `concurrent.futures.as_completed()` internally blocks on `threading.Event.wait()`, which does not respond to `KeyboardInterrupt` due to a CPython limitation (`WaitForSingleObject` ignores pending signals). Additionally, cookie authentication lacks API-level verification — `validate_cookie()` only checks SUB cookie existence without confirming actual login state.

Current cookie/session flow: `load_session()` → apply explicit cookies → `validate_cookie()` → `save_session()`. This saves sessions unconditionally when any auth source is present, with no login verification and no user feedback.

## Goals / Non-Goals

**Goals:**
- Ctrl+C responds within 1 second on Windows during any blocking operation
- Login status verified via Weibo API and displayed to user after cookie loading
- Multi-account session support with uid-based file naming
- Conditional session persistence (only on verified login)

**Non-Goals:**
- Async migration (no asyncio/aiohttp rewrite)
- Multi-account switching UI or `--session-uid` CLI parameter
- Auto-fallback to browser cookies on expired session
- Captcha handling during login verification

## Decisions

### D1: Polling loop replaces `as_completed`

Replace `as_completed(futures, timeout=post_timeout)` with a `concurrent.futures.wait(pending, timeout=min(0.5, remaining), return_when=FIRST_COMPLETED)` polling loop. A monotonic deadline (`time.monotonic() + post_timeout`) enforces per-post timeout semantics.

**Why not queue-based or asyncio?** Queue-based requires rewriting worker return/error propagation. Asyncio conflicts with the entire `requests`-based stack. The polling loop is minimal-change and sufficient for the 1-second response target.

**Constraint:** `wait()` timeout is clamped to `max(0, min(0.5, remaining))` — never negative. When `remaining <= 0`, one final `wait(timeout=0)` drains already-done futures before entering timeout handling.

### D2: Manual executor lifecycle (no `with` statement)

`ThreadPoolExecutor` is created and shut down manually instead of using a context manager. The `with` statement's `__exit__` calls `shutdown(wait=True)`, which blocks the main thread on in-flight downloads — defeating the Ctrl+C fix.

**Lifecycle contract:**
- Normal path: `executor.shutdown(wait=True)` in `finally` block
- Interrupt path: `executor.shutdown(wait=False, cancel_futures=True)` before re-raising `KeyboardInterrupt`
- The `finally` block detects whether shutdown already occurred (via a flag) to avoid double-shutdown

**Note:** `cancel_futures=True` only cancels queued (not running) futures. Running downloads continue until their socket timeout expires. This is an accepted limitation of Python's thread model.

### D3: `verify_login()` replaces `validate_cookie()`

`validate_cookie()` is removed entirely. A new `verify_login() -> tuple[bool | None, str | None]` method on `WeiboLoaderContext` calls `GET /api/config` on `m.weibo.cn`.

**Request parameters:** `allow_captcha=False`, `retries=1`, `timeout=10`. Uses existing `context.request()` infrastructure.

**Return value mapping:**
| API Response | Return Value |
|---|---|
| `data.login` is `True` (bool) and `data.uid` present | `(True, str(data.uid))` |
| `data.login` is `False` (bool) | `(False, None)` |
| `data.login` is non-bool type | `(None, None)` |
| `data` or `login` key missing | `(None, None)` |
| Network error / timeout / non-JSON | `(None, None)` |

All exceptions are caught internally — `verify_login()` never propagates exceptions.

### D4: Login status does not affect exit code

Login verification failure only produces a UI event. Exit code is determined solely by download results: 0 (all ok), 1 (any failure), 5 (interrupted).

### D5: Session files named by uid (multi-account)

Session files use the pattern `session_{uid}.dat` in the session directory (`~/.config/weiboloader/`). `save_session()` is called only when `verify_login()` returns `(True, uid)`.

**Auto-load logic:** When no explicit cookie flags (`--load-cookies`, `--cookie`, `--cookie-file`, `--visitor-cookies`) are provided, scan the session directory for `session_*.dat` files and load the one with the most recent `mtime`. If no session files exist, no `LOGIN_STATUS` event is emitted.

**Expired sessions are preserved** — never deleted on verification failure. The rationale: cookies may recover validity (e.g., server-side session extension), and deletion would destroy the user's only credential.

### D6: `LOGIN_STATUS` event emitted exactly once

The event is emitted after all cookie loading and verification is complete — never during intermediate steps. This prevents UI noise from partial states.

**UIEvent additions:** `login_ok: bool | None = None`, `uid: str | None = None` (both default `None` to preserve backward compatibility with existing event construction).

**RichSink rendering:**
| `login_ok` | Output |
|---|---|
| `True` | `[green]✓[/green] Logged in: @{uid}` |
| `False` | `[red]✗[/red] Not logged in` (or `Session expired` for auto-loaded sessions) |
| `None` | `[yellow]⚠[/yellow] Login status unknown` |

### D7: Cookie loading flow rewrite

New flow in `__main__.py`:

```
if explicit cookie flags provided:
    apply explicit cookies (--load-cookies / --cookie / --cookie-file / --visitor-cookies)
else:
    auto-load most recent session file (by mtime)

login_ok, uid = context.verify_login()
sink.emit(LOGIN_STATUS event)  # exactly once

if login_ok is True:
    context.save_session(uid)  # saves as session_{uid}.dat
```

**Precedence:** Explicit cookie flags completely suppress session auto-load. Within explicit flags, they stack (all applied to the same session object, matching current behavior).

## Risks / Trade-offs

**[R1] Running downloads continue after Ctrl+C** → Accepted limitation. `cancel_futures=True` only stops queued tasks. Running threads finish at their socket timeout (bounded by `_download`'s existing timeout). The main thread exits immediately; background threads are daemon-like.

**[R2] `/api/config` endpoint instability** → Mitigated by `(None, None)` fallback on any parse/network error. Program continues without auth. No hard dependency on API schema.

**[R3] Polling loop CPU overhead** → Negligible. 0.5s sleep interval with `wait()` is kernel-level blocking, not busy-wait. Under Weibo's rate limits, this adds no measurable overhead.

**[R4] `verify_login()` may trigger rate limiting** → Mitigated by `retries=1` and `allow_captcha=False`. Single lightweight GET request at startup. If rate-limited, returns `(None, None)` gracefully.

**[R5] Session file mtime race on auto-load** → Extremely unlikely in CLI context (single process). If two instances run simultaneously, worst case is loading a slightly stale session — verified by `verify_login()` anyway.

**[R6] uid containing filesystem-unsafe characters** → Weibo uids are numeric strings. No sanitization needed for the `session_{uid}.dat` pattern.
