# Implementation Tasks: weiboloader-initial-design

> Rebuilt from multi-model analysis (Codex backend + Gemini testing).
> Each task = implementation + unit tests + PBT verification.
> Tasks within the same phase have no mutual dependencies and CAN be parallelized.

---

## Phase 1: Core Contracts (no dependencies)

### Task 1.1: Exception Hierarchy + Exit Code Mapping
- **File**: `weiboloader/exceptions.py`
- **Impl**:
  - `WeiboLoaderException(Exception)` base
  - `AuthError` (exit 3), `RateLimitError`, `CheckpointError`, `TargetError`, `APISchemaError`, `InitError` (exit 2)
  - `map_exception_to_exit_code(exc: BaseException) -> int` — maps exception types to {0,1,2,3,5}
- **Tests**:
  - PBT: `exit_code ∈ {0,1,2,3,5}` for all exception subclasses + KeyboardInterrupt + generic Exception
- [x] Implement exception classes
- [x] Implement `map_exception_to_exit_code()`
- [x] Unit tests with PBT coverage

### Task 1.2: Data Structures + Target Model
- **File**: `weiboloader/structures.py`
- **Impl**:
  - `@dataclass User(uid: str, nickname: str, avatar: str | None, raw: dict)`
  - `@dataclass SuperTopic(containerid: str, name: str, raw: dict)`
  - `@dataclass MediaItem(media_type: Literal['picture','video'], url: str, index: int, filename_hint: str | None, raw: dict)`
  - `@dataclass Post(mid: str, bid: str | None, text: str, created_at: datetime, user: User | None, media_items: list[MediaItem], raw: dict)`
  - `@dataclass CursorState(page: int, cursor: str | None, seen_mids: list[str], options_hash: str, timestamp: str | None)`
  - `TargetSpec` union type: `UserTarget | SuperTopicTarget | MidTarget | SearchTarget`
- **Tests**:
  - Dataclass instantiation, field defaults, raw dict preservation
- [x] Implement all dataclasses
- [x] Implement `TargetSpec` typed target model
- [x] Unit tests

### Task 1.3: Project Scaffolding
- **Files**: `pyproject.toml`, `.gitignore`, `weiboloader/__init__.py`, `weiboloader/__main__.py` (stub)
- **Impl**:
  - `pyproject.toml`: dependencies (requests), optional extras `[browser]` (browser_cookie3), `[captcha]` (playwright)
  - `__init__.py`: `__all__` with public API exports, `__version__`
  - `.gitignore`: Python standard
  - `~/.config/weiboloader/` path bootstrap utility
- [x] Create project files
- [x] Define `__all__` and `__version__`

---

## Phase 2: Independent Low-Level Modules (depends on Phase 1)

### Task 2.1: Rate Controller
- **File**: `weiboloader/ratecontrol.py`
- **Impl**:
  - `BaseRateController(ABC)`: `wait_before_request(bucket: str)`, `handle_response(bucket: str, status_code: int)`
  - `SlidingWindowRateController(api_limit=30, api_window=600, base_delay=30, max_delay=600, jitter_ratio=0.5, request_interval=0.0)`
  - Separate buckets: `"api"` (30 req/600s), `"media"` (independent window)
  - Exponential backoff with jitter on 403/418; reset on success
- **Tests** (use `freezegun` for time control):
  - PBT: `∀ 600s window, Count(API requests) ≤ 30`
  - PBT: `API quota and media quota SHALL NOT cross-pollute`
  - PBT: `Backoff delay[k+1] >= Backoff delay[k]` (excluding jitter)
  - Backoff reset on success
  - `--request-interval` minimum gap enforcement
- [x] Implement `BaseRateController` + `SlidingWindowRateController`
- [x] Unit tests with PBT (hypothesis + freezegun)

### Task 2.2: API Adapter + Date Parsing
- **File**: `weiboloader/adapter.py`
- **Impl**:
  - `parse_weibo_datetime(raw: str, now: datetime | None) -> datetime` — handles `%a %b %d %H:%M:%S %z %Y`, `X分钟前`, `昨天`, `MM-DD`, `YYYY-MM-DD`; all output CST (+0800) aware
  - `parse_user_info(raw: Mapping) -> User`
  - `parse_supertopic(raw: Mapping) -> SuperTopic`
  - `parse_post(raw_card: Mapping) -> Post` — includes media extraction: `pic.large.url` for pictures, video priority `stream_url_hd > mp4_720p_mp4 > mp4_hd_url > stream_url`
  - `extract_next_cursor(raw_page: Mapping) -> str | None`
  - Defensive parsing: missing fields → fallback to raw, raise `APISchemaError` on critical failures
- **Tests**:
  - Date parsing variants (all 5+ formats)
  - Defensive parsing with missing/extra fields
  - Media extraction priority verification
- [x] Implement all parse functions
- [x] Implement date parser with CST output
- [x] Unit tests covering all date formats and edge cases

### Task 2.3: CAPTCHA Handlers
- **File**: `weiboloader/_captcha.py`
- **Impl**:
  - `CaptchaHandler(Protocol)`: `solve(verify_url, session, timeout_seconds=300) -> bool`
  - `PlaywrightCaptchaHandler`: launch chromium, navigate, wait, extract cookies
  - `ManualCaptchaHandler`: print URL, wait for Enter, 300s timeout
  - `SkipCaptchaHandler`: immediate abort
  - `is_playwright_available() -> bool`
  - CAPTCHA detection: HTTP 418 / redirect pattern → extract verification URL
- **Tests**:
  - PBT: `CAPTCHA state machine: INIT → WAITING → SOLVED|TIMEOUT` (no reverse transitions)
  - PBT: `CAPTCHA total duration ≤ Config.Timeout`
  - `is_playwright_available()` with mocked import
- [x] Implement handler protocol + 3 concrete handlers
- [x] Implement detection logic
- [x] Unit tests with state machine verification

### Task 2.4: NodeIterator + Checkpoint Manager
- **File**: `weiboloader/nodeiterator.py`
- **Impl**:
  - `NodeIterator(Iterator[Post])`: `__iter__`, `__next__`, `freeze() -> CursorState`, `thaw(state: CursorState)`, track `seen_mids`
  - `CheckpointManager`: `load(target_key) -> CursorState | None`, `save(target_key, state)`, `acquire_lock(target_key) -> ContextManager`
  - Atomic write: tmp + fsync + rename
  - Lock file per target (fail-fast on contention)
  - Corruption handling: invalid JSON → discard + warn + start fresh
  - Version + options_hash validation
- **Tests** (use `tmp_path` / `pyfakefs`):
  - PBT: `thaw(freeze(state)).next() == state.next()` (round-trip)
  - PBT: `freeze without advancing → identical serialized output` (idempotent)
  - PBT: `cursor monotonically advances; no mid is yielded twice`
  - PBT: `checkpoint file is always valid JSON or absent` (atomic write)
  - Corrupted checkpoint recovery
  - Lock contention behavior
- [x] Implement `NodeIterator` with freeze/thaw
- [x] Implement `CheckpointManager` with atomic write + lock
- [x] Unit tests with PBT

### Task 2.5: Filename Template Engine
- **File**: `weiboloader/naming.py` (new, extracted from weiboloader.py for testability)
- **Impl**:
  - Template variable substitution: `{nickname}`, `{uid}`, `{mid}`, `{bid}`, `{date}`, `{date:FORMAT}`, `{index}`, `{index:PAD}`, `{text}`, `{type}`
  - `sanitize_filename(s: str) -> str` — remove `\/:*?"<>|`
  - `{text}` truncation to 50 chars
  - All-illegal-character fallback (use mid)
  - Directory pattern defaults: User → `./{nickname}/`, SuperTopic → `./topic/{topic_name}/`, Search → `./search/{keyword}/`
- **Tests**:
  - PBT: `sanitize(sanitize(x)) == sanitize(x)` (idempotency)
  - PBT: `len({text} after substitution) ≤ 50`
  - PBT: `∀ generated paths, path contains no char in {\/:*?"<>|}`
  - All-illegal fallback
  - Index padding (`{index:03}` → `005`)
  - Date format substitution
- [x] Implement template engine + sanitizer
- [x] Implement directory pattern resolver
- [x] Unit tests with PBT (hypothesis string generation)

---

## Phase 3: Integration Hub (depends on Phase 2)

### Task 3.1: WeiboLoaderContext — HTTP + Auth + API Client
- **File**: `weiboloader/context.py`
- **Impl**:
  - `WeiboLoaderContext` class:
    - `requests.Session` management with RateController integration
    - `request(method, url, *, bucket='api', allow_captcha=True, retries=3, **kwargs) -> Response`
    - Rate control hooks: call `wait_before_request` before, `handle_response` after
    - CAPTCHA routing: detect 418/redirect → dispatch to handler based on `--captcha-mode`
  - Auth providers:
    - `load_browser_cookies(browser_name: Literal['chrome','firefox','edge'])` — graceful ImportError
    - `set_cookies_from_string(cookie_str)`, `set_cookies_from_file(path)`
    - `validate_cookie()` — check SUB field, raise AuthError if missing
  - Session persistence:
    - `save_session(path=None) -> Path`, `load_session(path=None) -> bool`
    - Default path: `~/.config/weiboloader/session.dat`
    - Pickle serialization
  - API client methods:
    - `resolve_nickname_to_uid(nickname)` via 302 redirect
    - `get_user_info(uid)`, `get_user_posts(uid, page)`, `get_supertopic_posts(containerid, page)`
    - `search_supertopic(keyword)`, `search_posts(keyword, page)`, `get_post_by_mid(mid)`
    - All return parsed structures via adapter.py
- **Tests** (use `responses` or `pytest-httpserver`):
  - PBT: `Load(Save(Session)) == Session` (round-trip)
  - PBT: `browser_cookie3 ImportError SHALL NOT crash`
  - Cookie validation (SUB present/absent)
  - Rate control integration (verify `wait_before_request` called)
  - CAPTCHA mode routing
- [x] Implement WeiboLoaderContext core (session + rate control + CAPTCHA routing)
- [x] Implement auth providers (browser cookies, string, file, session persistence)
- [x] Implement API client methods
- [x] Unit tests with mocked HTTP

---

## Phase 4: Orchestrator (depends on Phase 3)

### Task 4.1: WeiboLoader — Download Coordination
- **File**: `weiboloader/weiboloader.py`
- **Impl**:
  - `WeiboLoader` class:
    - `download_targets(targets: Sequence[TargetSpec]) -> dict[str, bool]` — sequential, fault-isolated
    - `download_target(target: TargetSpec) -> bool` — per-target logic
    - `iter_target_posts(target: TargetSpec) -> Iterator[Post]` — NodeIterator integration
    - `download_media(url, dest_path, context) -> Path | None` — skip if exists && size>0, write to `.part`, rename on completion
  - Media type filtering: `--no-videos`, `--no-pictures`
  - Count limit: `--count N`
  - Fast update: stop on existing file
  - Latest stamps: load/save atomic JSON, CST-aware timestamps, incremental filtering
  - Metadata export: `--metadata-json` → `{mid}.json`, `--post-metadata-txt` → `{mid}.txt`
  - ThreadPoolExecutor for media downloads
  - KeyboardInterrupt: flush checkpoints + stamps before exit
- **Tests** (use `tmp_path` + mocked context):
  - PBT: `exists && size>0 → skip; size==0 || !exists → download`
  - PBT: `processed_count ≤ --count`
  - PBT: `--no-videos → zero video items`
  - PBT: `--no-pictures → zero picture items`
  - PBT: `metadata JSON round-trip: json.loads(written) == original`
  - PBT: `Load(Save(stamps)) == stamps` (round-trip, aware CST)
  - Fast update stop behavior
  - Fault isolation (target 2 fails, 3 continues)
  - `.part` rename semantics
- [x] Implement WeiboLoader orchestrator
- [x] Implement media download with .part + skip logic
- [x] Implement filtering (count, fast-update, latest-stamps, media type)
- [x] Implement metadata export
- [x] Unit tests with PBT

---

## Phase 5: CLI Boundary (depends on Phase 4)

### Task 5.1: CLI — Target Parsing + Argument Parser + Entry Point
- **File**: `weiboloader/__main__.py`
- **Impl**:
  - `parse_target(raw: str, mid_flag: str | None) -> TargetSpec`:
    - Priority: URL → Mid flag → `#` SuperTopic → `:` Search → digits UID / else nickname
  - `parse_args(argv=None) -> argparse.Namespace`:
    - All flags: `--load-cookies`, `--cookie`, `--cookie-file`, `--sessionfile`
    - `--no-videos`, `--no-pictures`, `--metadata-json`, `--post-metadata-txt`
    - `--dirname-pattern`, `--filename-pattern`
    - `--post-filter` (stub, v2), `--count`, `--fast-update`, `--latest-stamps`
    - `--no-resume`, `--request-interval`, `--captcha-mode`
  - `main(argv=None) -> int`:
    - Build context → build loader → process targets → map exceptions to exit codes
    - KeyboardInterrupt → flush + exit 5
- **Tests**:
  - PBT: `exit_code ∈ {0,1,2,3,5}` for all argv combinations
  - Target parsing: URL, mid, `#topic`, `:search`, UID, nickname, priority disambiguation
  - Batch processing: partial failure → exit 1
  - Auth failure → exit 3
- [x] Implement target parser
- [x] Implement argument parser
- [x] Implement `main()` entry point
- [x] Unit tests with PBT

---

## Phase 6: Integration Testing (depends on Phase 5)

### Task 6.1: Integration Test Suite
- **Fixtures**:
  - `MockWeiboAPI`: `responses` / `pytest-httpserver` with paginated JSON mocks
  - `FrozenClock`: `freezegun` for sliding window and backoff timing
  - `IsolatedFS`: `tmp_path` for atomic writes and path sanitization
- **Scenarios**:
  - Full-lifecycle: `weiboloader <uid>` → mocked API → files on disk
  - Resume-on-failure: crash mid-download → second run skips completed files via checkpoint
  - Rate-limit recovery: mock 418 → backoff → eventual success
  - Filter verification: `--no-videos` → zero .mp4 in output
  - Incremental update: `--latest-stamps` → second run downloads zero new posts
  - CAPTCHA fallback: Playwright unavailable → ManualCaptchaHandler invoked
- [x] Implement test fixtures (MockWeiboAPI)
- [x] Implement integration test scenarios

---

## Verification Checklist

- [ ] `weiboloader <uid>` downloads user media to `./{nickname}/`
- [ ] `weiboloader "#超話名"` downloads supertopic media
- [ ] `weiboloader -mid <mid>` downloads single post
- [ ] `weiboloader ":search keyword"` downloads search results
- [ ] `--load-cookies chrome` extracts cookies
- [ ] Rate controller prevents 403/418 on extended runs
- [ ] CAPTCHA flow works (or falls back gracefully)
- [ ] Resume from checkpoint works correctly
- [ ] `--fast-update` stops on existing files
- [ ] `--latest-stamps` incremental update works
- [ ] All 20 PBT properties pass
