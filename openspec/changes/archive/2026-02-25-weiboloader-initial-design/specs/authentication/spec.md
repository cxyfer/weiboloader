## ADDED Requirements

### Requirement: Browser cookie extraction (optional dependency)
The system SHALL support extracting cookies from local browsers via `browser_cookie3` when installed as optional extra (`pip install weiboloader[browser]`). Supported browsers: Chrome, Firefox, Edge.

#### Scenario: Extract cookies from Chrome
- **WHEN** user runs `weiboloader --load-cookies chrome <target>` and `browser_cookie3` is installed
- **THEN** system SHALL extract `.weibo.cn` domain cookies from Chrome's cookie store

#### Scenario: browser_cookie3 not installed
- **WHEN** user runs `--load-cookies chrome` but `browser_cookie3` is not installed
- **THEN** system SHALL raise a clear error message indicating the optional dependency is missing, without crashing

#### Scenario: browser_cookie3 extraction fails
- **WHEN** `browser_cookie3` raises an exception (e.g., locked database, unsupported OS)
- **THEN** system SHALL report the error and fall back to other auth methods if available

### Requirement: Manual cookie input
The system SHALL accept cookies via `--cookie` (inline string) or `--cookie-file` (path to file).

#### Scenario: Inline cookie string
- **WHEN** user passes `--cookie "SUB=xxx; SUBP=yyy"`
- **THEN** system SHALL parse the string and set cookies on the HTTP session

#### Scenario: Cookie file
- **WHEN** user passes `--cookie-file ./cookies.txt`
- **THEN** system SHALL read the file content, strip whitespace/newlines, and set cookies on the HTTP session

### Requirement: Cookie validity check
The system SHALL validate that the cookie set contains the `SUB` field. A cookie set without `SUB` MUST be treated as invalid.

#### Scenario: Valid cookie with SUB
- **WHEN** cookie set contains `SUB=abc123`
- **THEN** system SHALL accept the cookie set as valid

#### Scenario: Missing SUB field
- **WHEN** cookie set does not contain `SUB`
- **THEN** system SHALL reject the cookie set and raise AuthError (exit code 3)

### Requirement: Session persistence (hybrid path)
The system SHALL support serializing the authenticated session to a file. When `--sessionfile <path>` is specified, that path MUST be used. When not specified, the system SHALL use `~/.config/weiboloader/session.dat` as default.

#### Scenario: Explicit sessionfile path
- **WHEN** user passes `--sessionfile ./my-session.dat`
- **THEN** system SHALL save/load session from `./my-session.dat`

#### Scenario: Default sessionfile path
- **WHEN** user does not pass `--sessionfile`
- **THEN** system SHALL save/load session from `~/.config/weiboloader/session.dat`

#### Scenario: Session round-trip integrity
- **WHEN** session is saved and then loaded
- **THEN** the loaded cookie set MUST be semantically equivalent to the saved one (name, domain, path, value, expiry preserved)

<!-- PBT: Load(Save(Session)) == Session (round-trip) -->
<!-- PBT: browser_cookie3 ImportError SHALL NOT crash the process -->
