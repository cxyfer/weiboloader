# Design: rich-progress-ui

## Architecture

```
__main__.py          ui.py                  weiboloader.py
┌──────────┐    ┌──────────────┐    ┌─────────────────┐
│ create    │───>│ RichSink     │<───│ WeiboLoader     │
│ RichSink  │    │ (Progress)   │    │ emit(UIEvent)   │
│ inject    │    ├──────────────┤    │ _safe_emit()    │
│ into      │    │ NullSink     │    └─────────────────┘
│ WeiboLoader│   │ (non-TTY)    │
└──────────┘    └──────────────┘
```

## D1: Event Protocol — `ProgressSink`

`WeiboLoader` 透過 `ProgressSink.emit(UIEvent)` 通知 UI 層，不直接 import rich。

```python
# weiboloader/ui.py

class EventKind(str, Enum):
    STAGE        = "stage"          # 階段性狀態訊息
    TARGET_START = "target_start"   # 開始處理 target
    POST_DONE    = "post_done"      # 一篇貼文的媒體全部完成
    MEDIA_DONE   = "media_done"     # 單個媒體完成（含 outcome）
    TARGET_DONE  = "target_done"    # target 完成摘要
    INTERRUPTED  = "interrupted"    # Ctrl+C

class MediaOutcome(str, Enum):
    DOWNLOADED = "downloaded"
    SKIPPED    = "skipped"
    FAILED     = "failed"

@dataclass(slots=True)
class UIEvent:
    kind: EventKind
    # 各 kind 使用的欄位子集，其餘為 None

@runtime_checkable
class ProgressSink(Protocol):
    def emit(self, event: UIEvent) -> None: ...
    def close(self) -> None: ...
```

## D2: `_download()` 回傳值重構

現有 `Path | None` 無法區分 downloaded/skipped/failed。改為：

```python
@dataclass(slots=True)
class DownloadResult:
    outcome: MediaOutcome   # downloaded | skipped | failed
    path: Path
```

- `dest.exists() and st_size > 0` → `SKIPPED`
- 成功寫入 → `DOWNLOADED`
- exception → `FAILED`

## D3: Thread Safety

- rich `Progress` / `Console` 僅在主執行緒操作。
- `_download()` 在 worker thread 中執行，回傳 `DownloadResult`。
- 主執行緒在 `as_completed` 迴圈中呼叫 `_safe_emit()`。
- 不使用 queue — 目前 `as_completed` 已是同步消費點。

## D4: Non-TTY Fallback

- `__main__.py` 檢查 `sys.stderr.isatty()`。
- TTY → `RichSink`（live progress + styled output）
- 非 TTY → `NullSink`（靜默，僅 logging 輸出）

## D5: Captcha 暫停

- `WeiboLoaderContext` 新增可選 `pause_callback` / `resume_callback`。
- `_solve_captcha()` 呼叫前後觸發 pause/resume。
- `RichSink` 實作 pause → `Progress.stop()`，resume → `Progress.start()`。

## D6: Logging 整合

- `__main__.py` 設定 `rich.logging.RichHandler` 作為 root handler。
- `console` 實例與 `Progress` 共用同一個 `Console(stderr=True)`。
- 確保 log 訊息在進度條上方優雅滾動。

## D7: 依賴

- `rich>=13.0` 加入 `pyproject.toml` 的 `dependencies`。

## Files Changed

| File | Action |
|------|--------|
| `weiboloader/ui.py` | **新增** — EventKind, UIEvent, ProgressSink, NullSink, RichSink |
| `weiboloader/weiboloader.py` | **修改** — 注入 ProgressSink, emit events, DownloadResult |
| `weiboloader/__main__.py` | **修改** — 建立 RichSink/NullSink, 注入, logging 設定 |
| `weiboloader/context.py` | **修改** — pause/resume callback for captcha |
| `pyproject.toml` | **修改** — 加入 rich 依賴 |
