# Proposal: fix-ctrl-c-and-login-status

## Context

Two user-reported issues:
1. On native Windows (PowerShell), pressing Ctrl+C does not exit the program — it hangs. Works fine in WSL.
2. When loading cookies from browser (`--load-cookies`), there is no feedback on whether login succeeded or what account is active. User wants login status display and session auto-reuse.

## Constraint Sets

### Hard Constraints

- **HC-1**: Program is synchronous — uses `requests` + `ThreadPoolExecutor`, no asyncio.
- **HC-2**: `concurrent.futures.as_completed()` internally uses `threading.Event.wait()`, which on Windows does NOT respond to `KeyboardInterrupt` (CPython limitation — `WaitForSingleObject` ignores pending signals).
- **HC-3**: `requires-python >= 3.10` — `time.sleep()` IS interruptible on Windows since 3.10, but `threading.Event.wait()` is not.
- **HC-4**: The blocking call is at `weiboloader.py:186` — `as_completed(future_to_path, timeout=post_timeout)` where `post_timeout` can be 60+ seconds.
- **HC-5**: `ratecontrol.py:88` uses `time.sleep(wait)` — interruptible on 3.10+ but `wait` can be up to 600s during backoff.
- **HC-6**: Cookie validation (`validate_cookie()`) only checks SUB cookie existence — no actual API verification.
- **HC-7**: Session save/load already exists (`save_session`/`load_session` in `context.py`), serializes cookies as JSON.
- **HC-8**: `browser_cookie3` is an optional dependency (`pip install weiboloader[browser]`).

### Soft Constraints

- **SC-1**: Existing `KeyboardInterrupt` handling at 3 levels (`__main__.py:182`, `weiboloader.py:124`, `weiboloader.py:246`) must be preserved — they handle checkpoint save and cleanup.
- **SC-2**: UI output goes through `RichSink` (TTY) or `NullSink` (non-TTY). Login status display should follow this pattern.
- **SC-3**: Weibo mobile API `GET /api/config` returns `{data: {login: bool, uid: str, st: str}}` — standard endpoint for login verification.
- **SC-4**: Default session path is `~/.config/weiboloader/session.dat`.

### Dependencies

- **D-1**: Ctrl+C fix touches `weiboloader.py` (download loop) — no cross-module impact.
- **D-2**: Login status touches `context.py` (new verify method) + `__main__.py` (display logic) + `ui.py` (new event kind).

### Risks

- **R-1**: Polling `as_completed` with short timeouts may slightly increase CPU usage on idle waits — mitigated by 0.5s poll interval.
- **R-2**: Weibo `/api/config` endpoint may be rate-limited or return unexpected schema — need graceful fallback.
- **R-3**: Saved session cookies may expire between runs — need to handle gracefully without blocking execution.

## Requirements

### Requirement 1: Windows Ctrl+C graceful exit

The program SHALL respond to Ctrl+C (SIGINT) on Windows within 1 second, matching the behavior on Unix/WSL.

#### Scenario: Ctrl+C during media download
- **WHEN** user presses Ctrl+C while `ThreadPoolExecutor` is downloading media on Windows
- **THEN** the main thread SHALL break out of the `as_completed()` wait within 1 second
- **AND** checkpoint and stamps SHALL be saved before exit
- **AND** exit code SHALL be 5

#### Scenario: Ctrl+C during rate-limit backoff
- **WHEN** user presses Ctrl+C while `SlidingWindowRateController` is sleeping
- **THEN** the sleep SHALL be interrupted and program SHALL exit gracefully

#### Implementation Approach
- Replace `as_completed(futures, timeout=post_timeout)` with a polling loop: `concurrent.futures.wait(futures, timeout=0.5)` in a while-loop, checking a `_interrupted` flag set by `signal.signal(SIGINT, handler)`.
- On Windows, register `signal.signal(signal.SIGINT, handler)` that sets a threading.Event, then raise `KeyboardInterrupt` from the main thread's poll loop.
- Keep existing `KeyboardInterrupt` catch blocks unchanged — they handle cleanup correctly.

#### Files Affected
- `weiboloader/weiboloader.py` — replace `as_completed` with polling loop
- `weiboloader/__main__.py` — register signal handler on Windows

---

### Requirement 2: Login status display after cookie load

The system SHALL verify login status via Weibo API after loading cookies from any source, and display the result to the user.

#### Scenario: Successful login from browser cookies
- **WHEN** user runs `--load-cookies chrome` and cookies are valid
- **THEN** system SHALL call `GET /api/config` to verify login
- **AND** display `✓ Logged in: @screen_name` via RichSink
- **AND** save session to disk

#### Scenario: Failed login (expired cookies)
- **WHEN** cookies are loaded but `/api/config` returns `login: false`
- **THEN** system SHALL display `✗ Not logged in` via RichSink
- **AND** continue execution (some targets work without auth)

#### Scenario: API verification fails (network error)
- **WHEN** `/api/config` request fails
- **THEN** system SHALL display `⚠ Login status unknown` and continue
- **AND** fall back to existing SUB cookie check

#### Scenario: Auto-load saved session on next run
- **WHEN** user runs without `--load-cookies` or `--cookie` and a saved session exists
- **THEN** system SHALL auto-load the session
- **AND** verify login status via `/api/config`
- **AND** if expired, display `✗ Session expired` and continue without auth

#### Implementation Approach
- Add `verify_login() -> tuple[bool, str | None]` to `WeiboLoaderContext` — calls `GET /api/config`, returns `(is_logged_in, screen_name)`.
- Add `EventKind.LOGIN_STATUS` to `ui.py` with fields for login state and username.
- In `__main__.py`, after cookie loading, call `verify_login()` and emit login status event.
- `RichSink` renders: `✓ Logged in: @name` (green) or `✗ Not logged in` (red).

#### Files Affected
- `weiboloader/context.py` — add `verify_login()` method
- `weiboloader/ui.py` — add `LOGIN_STATUS` event kind + rendering
- `weiboloader/__main__.py` — call verify and emit event

---

## Success Criteria

1. On Windows PowerShell, `Ctrl+C` exits the program within 1 second during any blocking operation.
2. After `--load-cookies`, the CLI displays login status and account name.
3. Saved sessions are auto-loaded and verified on subsequent runs.
4. All existing tests pass without modification.
5. No regression on Unix/WSL behavior.
