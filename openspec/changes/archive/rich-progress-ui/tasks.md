# Tasks: rich-progress-ui

## T1: 新增 `weiboloader/ui.py` — Event 定義與 Sink 實作

- [x] 定義 `EventKind` enum (STAGE, TARGET_START, POST_DONE, MEDIA_DONE, TARGET_DONE, INTERRUPTED)
- [x] 定義 `MediaOutcome` enum (DOWNLOADED, SKIPPED, FAILED)
- [x] 定義 `UIEvent` dataclass (slots=True)
- [x] 定義 `ProgressSink` Protocol (emit, close)
- [x] 實作 `NullSink` (no-op)
- [x] 實作 `RichSink` (Progress + Console, pause/resume, markup escape)
- [x] 定義 `DownloadResult` dataclass (outcome: MediaOutcome, path: Path)

---

## T2: 修改 `weiboloader/weiboloader.py` — 注入 Sink 與發送事件

- [x] `WeiboLoader.__init__` 新增 `progress: ProgressSink | None = None`
- [x] 新增 `_safe_emit(event)` 方法
- [x] `_download()` 回傳 `DownloadResult`
- [x] `download_target()` 中 emit TARGET_START, MEDIA_DONE, POST_DONE, TARGET_DONE, INTERRUPTED
- [x] `_resolve_target()` 前 emit STAGE
- [x] interrupt/error 路徑也 emit TARGET_DONE 摘要

---

## T3: 修改 `weiboloader/context.py` — Captcha pause/resume callback

- [x] `WeiboLoaderContext.__init__` 新增 `on_captcha_pause` / `on_captcha_resume`
- [x] `_solve_captcha()` 呼叫前後觸發 pause/resume (try/finally)
- [x] callback 例外不影響 captcha 流程

---

## T4: 修改 `weiboloader/__main__.py` — 組裝 UI 與 Logging

- [x] TTY 時建立 `Console(stderr=True)` + `RichSink` + `RichHandler`
- [x] 非 TTY 時使用 `NullSink`
- [x] 注入 sink 至 `WeiboLoader(progress=sink)`
- [x] 注入 pause/resume 至 `WeiboLoaderContext`
- [x] `finally` 中呼叫 `sink.close()`
- [x] visitor cookies 前 emit STAGE

---

## T5: 修改 `pyproject.toml` — 加入 rich 依賴

- [x] `dependencies` 加入 `"rich>=13.0"`

---

## T6: 測試

- [x] `_safe_emit` 吞掉例外
- [x] `DownloadResult` 三態正確
- [x] 事件序列正確 (TARGET_START → POST_DONE* → TARGET_DONE)
- [x] TARGET_DONE 統計一致
- [x] captcha pause/resume 成對
- [x] 現有測試套件全數通過 (197 passed)
