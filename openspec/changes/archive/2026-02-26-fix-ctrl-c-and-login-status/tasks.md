## 1. Polling-based future wait (weiboloader/weiboloader.py)

- [x] 1.1 Replace `from concurrent.futures import ... as_completed` with `wait, FIRST_COMPLETED`; remove `FuturesTimeoutError` import
- [x] 1.2 Replace `with ThreadPoolExecutor(...) as exe:` with manual lifecycle: `exe = None` before try, `exe = ThreadPoolExecutor(...)` inside try, `shutdown_done = False` flag
- [x] 1.3 Replace `as_completed(future_to_path, timeout=post_timeout)` loop (lines 185-226) with polling loop: `deadline = time.monotonic() + post_timeout` after all futures submitted; `while pending: done, pending = wait(pending, timeout=max(0, min(0.5, deadline - time.monotonic())), return_when=FIRST_COMPLETED)`; process all futures in `done` set per cycle; skip loop body when `future_to_path` is empty
- [x] 1.4 Replace `except FuturesTimeoutError` with deadline check: when `time.monotonic() >= deadline` and pending non-empty, cancel remaining futures via `f.cancel()`, emit MEDIA_DONE FAILED for each, set `timed_out = True`
- [x] 1.5 Add `finally` block after the outer try: `if exe is not None and not shutdown_done: exe.shutdown(wait=True); shutdown_done = True`
- [x] 1.6 In `except KeyboardInterrupt` block (line 246): insert `if exe is not None and not shutdown_done: exe.shutdown(wait=False, cancel_futures=True); shutdown_done = True` before existing handler logic
- [x] 1.7 Verify: event counts (MEDIA_DONE/POST_DONE/TARGET_DONE), checkpoint save, stamps save, and exit code 5 on interrupt all remain identical to pre-change behavior

## 2. Login verification (weiboloader/context.py)

- [x] 2.1 Add `verify_login(self) -> tuple[bool | None, str | None]` method: `GET /api/config` via `self.request("GET", "api/config", allow_captcha=False, retries=1, timeout=10)`; wrap entire body in try/except Exception returning `(None, None)`
- [x] 2.2 Implement return value mapping inside verify_login: parse `resp.json()` → `data = body.get("data", {})`; if `data.get("login") is True` → uid = `str(data["uid"])` if `"uid" in data and data["uid"]` else `"unknown"` → return `(True, uid)`; if `data.get("login") is False` → return `(False, None)`; all other cases → return `(None, None)`
- [x] 2.3 Remove `validate_cookie()` method entirely

## 3. Session uid-based naming (weiboloader/context.py)

- [x] 3.1 Modify `save_session()` signature to `save_session(self, uid: str | None = None, path: str | Path | None = None) -> Path`: when `path` is provided, use that exact path; otherwise use `self._session_path.parent / f"session_{uid}.dat"` (requires uid to be non-None)
- [x] 3.2 Modify `load_session()`: when `path` is provided, load that exact file; when `path` is None, scan `self._session_path.parent` for `session_*.dat` files, pick the one with most recent mtime; return False if no files found or all fail to parse
- [x] 3.3 Remove legacy `session.dat` constant or repurpose `_session_path` to point to the session directory parent only (keep `SESSION_DIR` semantics for auto-scan)

## 4. LOGIN_STATUS event (weiboloader/ui.py)

- [x] 4.1 Add `LOGIN_STATUS = "login_status"` to `EventKind` enum
- [x] 4.2 Add `login_ok: bool | None = None` and `uid: str | None = None` fields to `UIEvent` dataclass (default None for backward compat)
- [x] 4.3 Add `LOGIN_STATUS` branch in `RichSink._handle()`: `True` → `[green]✓[/green] Logged in: @{uid}`; `False` → `[red]✗[/red] Not logged in` (or `Session expired` when loaded from auto-load); `None` → `[yellow]⚠[/yellow] Login status unknown`
- [x] 4.4 Verify `NullSink` silently ignores LOGIN_STATUS (no code change needed, just confirm)

## 5. Cookie loading flow rewrite (weiboloader/__main__.py)

- [x] 5.1 Refactor cookie loading block (lines 140-157): split into two branches — explicit cookie flags (`--load-cookies`, `--cookie`, `--cookie-file`) vs auto-load session; `--visitor-cookies` as separate third branch
- [x] 5.2 Explicit cookie branch: apply cookies → call `context.verify_login()` → emit `LOGIN_STATUS` event → if `login_ok is True`: `context.save_session(uid=uid, path=args.sessionfile)` (explicit path takes priority)
- [x] 5.3 Auto-load branch (no explicit flags, no --visitor-cookies): call `context.load_session()` (scans `~/.config/weiboloader/` for most recent `session_*.dat`) → if loaded: call `context.verify_login()` → emit `LOGIN_STATUS` (use `"Session expired"` message variant when `login_ok is False`) → if `login_ok is True`: `context.save_session(uid=uid)`
- [x] 5.4 Visitor-cookies branch: `context.fetch_visitor_cookies()` → no verify_login, no save_session, no LOGIN_STATUS event
- [x] 5.5 No-cookies branch (no flags, no session files found): no verify_login, no LOGIN_STATUS event, continue silently
- [x] 5.6 Remove `validate_cookie()` call and `has_auth` variable entirely
- [x] 5.7 Verify: `KeyboardInterrupt` during `verify_login()` propagates to existing top-level handler (line 182) without being swallowed

## 6. Regression verification

- [x] 6.1 Verify Ctrl+C during download on polling loop exits within 1s, checkpoint saved, exit code 5
- [x] 6.2 Verify normal download completion produces identical event sequence (MEDIA_DONE counts, POST_DONE, TARGET_DONE)
- [x] 6.3 Verify `--load-cookies chrome` with valid cookies → green ✓ + session saved as `session_{uid}.dat`
- [x] 6.4 Verify expired cookies → red ✗ + no session save + execution continues
- [x] 6.5 Verify network error during verify_login → yellow ⚠ + no session save + execution continues
- [x] 6.6 Verify `--visitor-cookies` → no verify_login call, no session save, no LOGIN_STATUS
- [x] 6.7 Verify `--sessionfile /path/to/file` → explicit path used for load/save, no uid-based naming
- [x] 6.8 Verify auto-load picks most recent `session_*.dat` by mtime from `~/.config/weiboloader/`
- [x] 6.9 Verify no regression on Unix/WSL Ctrl+C behavior
