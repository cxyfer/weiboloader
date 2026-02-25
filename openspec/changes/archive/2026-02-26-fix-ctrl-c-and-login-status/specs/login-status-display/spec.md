## ADDED Requirements

### Requirement: Login verification via API
The system SHALL verify login status by calling `GET /api/config` on `m.weibo.cn` after loading cookies from any source. A new method `verify_login()` SHALL be added to `WeiboLoaderContext`, returning `tuple[bool, str | None]` where the first element indicates login success and the second is the screen name (or None).

#### Scenario: Successful login verification
- **WHEN** cookies are loaded and `GET /api/config` returns `{data: {login: true, uid: "<uid>"}}`
- **THEN** `verify_login()` SHALL return `(True, "<screen_name>")`
- **AND** screen name SHALL be resolved from the uid via existing `get_user_info()` or extracted from the config response if available

#### Scenario: Failed login (expired or invalid cookies)
- **WHEN** cookies are loaded and `GET /api/config` returns `{data: {login: false}}`
- **THEN** `verify_login()` SHALL return `(False, None)`

#### Scenario: Network error during verification
- **WHEN** the `GET /api/config` request fails due to network error, timeout, or non-200 status
- **THEN** `verify_login()` SHALL return `(None, None)` indicating unknown status
- **AND** SHALL NOT raise an exception

#### Scenario: Malformed API response
- **WHEN** `GET /api/config` returns non-JSON or unexpected schema (missing `data` or `login` field)
- **THEN** `verify_login()` SHALL return `(None, None)`
- **AND** SHALL NOT raise an exception

### Requirement: Login status UI event
A new `EventKind.LOGIN_STATUS` SHALL be added to the event system. `UIEvent` SHALL gain two optional fields: `login_ok: bool | None` and `screen_name: str | None`.

#### Scenario: Display successful login
- **WHEN** `verify_login()` returns `(True, "screen_name")`
- **THEN** a `LOGIN_STATUS` event SHALL be emitted with `login_ok=True, screen_name="screen_name"`
- **AND** `RichSink` SHALL render `[green]✓[/green] Logged in: @screen_name`

#### Scenario: Display failed login
- **WHEN** `verify_login()` returns `(False, None)`
- **THEN** a `LOGIN_STATUS` event SHALL be emitted with `login_ok=False, screen_name=None`
- **AND** `RichSink` SHALL render `[red]✗[/red] Not logged in`

#### Scenario: Display unknown login status
- **WHEN** `verify_login()` returns `(None, None)`
- **THEN** a `LOGIN_STATUS` event SHALL be emitted with `login_ok=None, screen_name=None`
- **AND** `RichSink` SHALL render `[yellow]⚠[/yellow] Login status unknown`

#### Scenario: NullSink ignores login status
- **WHEN** a `LOGIN_STATUS` event is emitted and the sink is `NullSink`
- **THEN** the event SHALL be silently ignored (no output, no error)

### Requirement: Conditional session save on verified login
`save_session()` SHALL only be called when `verify_login()` confirms login success (`login_ok=True`). This replaces the current behavior of saving whenever `has_auth=True`.

#### Scenario: Save session on successful verification
- **WHEN** cookies are loaded from any source and `verify_login()` returns `(True, _)`
- **THEN** `save_session()` SHALL be called to persist the session

#### Scenario: Do not save on failed verification
- **WHEN** cookies are loaded but `verify_login()` returns `(False, None)` or `(None, None)`
- **THEN** `save_session()` SHALL NOT be called
- **AND** any existing session file SHALL NOT be overwritten

### Requirement: Auto-load saved session with verification
On startup, if no explicit cookie source is provided (`--load-cookies`, `--cookie`, `--cookie-file`), the system SHALL attempt to load a saved session and verify it.

#### Scenario: Auto-load valid session
- **WHEN** user runs without cookie flags and a saved session exists at the default path
- **THEN** `load_session()` SHALL be called
- **AND** `verify_login()` SHALL be called to verify the loaded session
- **AND** if verified, `LOGIN_STATUS` event SHALL be emitted with `login_ok=True`

#### Scenario: Auto-load expired session
- **WHEN** a saved session exists but `verify_login()` returns `(False, None)`
- **THEN** `LOGIN_STATUS` event SHALL be emitted with `login_ok=False`
- **AND** `RichSink` SHALL render `[red]✗[/red] Session expired`
- **AND** execution SHALL continue without authentication (no auto-fallback to browser)
- **AND** the expired session file SHALL NOT be overwritten

#### Scenario: No saved session exists
- **WHEN** no session file exists and no cookie flags are provided
- **THEN** no `LOGIN_STATUS` event SHALL be emitted
- **AND** execution SHALL continue without authentication

### Requirement: No auto-fallback to browser cookies
When a session is expired or login verification fails, the system SHALL NOT automatically attempt to load cookies from the browser. Users MUST explicitly provide `--load-cookies` to trigger browser cookie loading.

#### Scenario: Expired session does not trigger browser fallback
- **WHEN** saved session is loaded but verification returns `login_ok=False`
- **THEN** `load_browser_cookies()` SHALL NOT be called
- **AND** no browser-related imports or operations SHALL be triggered

## PBT Properties

### Property: Login status event consistency
- **INVARIANT**: For any `verify_login()` return value `(ok, name)`, the emitted `LOGIN_STATUS` event SHALL have `login_ok == ok` and `screen_name == name`
- **FALSIFICATION**: Fuzz `/api/config` responses (valid JSON, invalid JSON, missing fields, wrong types), verify event fields match return value

### Property: Session save idempotency guard
- **INVARIANT**: `save_session()` is called if and only if `verify_login()` returns `(True, _)`
- **FALSIFICATION**: Generate sequences of cookie loads with varying `/api/config` responses, monitor `save_session` call count

### Property: Cookie round-trip integrity
- **INVARIANT**: Cookies saved via `save_session()` and loaded via `load_session()` SHALL preserve all `name`, `value`, `domain`, `path` fields for identity-critical cookies (SUB, SUBP)
- **FALSIFICATION**: Generate cookie jars with special characters, long values, multiple domains; save→load→compare

### Property: No browser fallback invariant
- **INVARIANT**: `load_browser_cookies()` is never called unless the user explicitly passes `--load-cookies`
- **FALSIFICATION**: Generate all combinations of session states (exists/missing, valid/expired) without `--load-cookies` flag, verify `load_browser_cookies` is never invoked
