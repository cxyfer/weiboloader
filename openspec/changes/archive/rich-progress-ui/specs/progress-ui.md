# Spec: Progress UI

## S1: Event Protocol

### S1.1: ProgressSink Protocol
- `ProgressSink` 定義 `emit(UIEvent) -> None` 和 `close() -> None`
- `WeiboLoader.__init__` 接受可選 `progress: ProgressSink | None`，預設 `None`（等同 NullSink）
- `_safe_emit()` 包裝所有 `emit` 呼叫，吞掉任何 UI 例外（log debug + continue）

**PBT 屬性**:
- INVARIANT: `_safe_emit()` 永不拋出例外 — 對任意 UIEvent 與任意會拋例外的 ProgressSink，`_safe_emit` 回傳 None
- FALSIFICATION: 注入一個 `emit()` 隨機拋出 Exception/TypeError/RuntimeError 的 sink，驗證 `_safe_emit` 不傳播

### S1.2: NullSink
- `emit()` 和 `close()` 皆為 no-op
- 用於非 TTY 環境或未注入 sink 時

### S1.3: UIEvent 完整性
- `EventKind.TARGET_START`: 必須攜帶 `target_key`
- `EventKind.STAGE`: 必須攜帶 `message`
- `EventKind.MEDIA_DONE`: 必須攜帶 `outcome`, `media_done`, `media_total`
- `EventKind.POST_DONE`: 必須攜帶 `posts_processed`
- `EventKind.TARGET_DONE`: 必須攜帶 `target_key`, `posts_processed`, `downloaded`, `skipped`, `failed`, `ok`
- `EventKind.INTERRUPTED`: 必須攜帶 `target_key`

**PBT 屬性**:
- INVARIANT: 每個 EventKind 的必要欄位不為 None — 對所有 emit 呼叫點，驗證對應欄位已填充
- FALSIFICATION: Mock sink 收集所有 events，assert 每個 event 的必要欄位 is not None

## S2: DownloadResult

### S2.1: 三態回傳
- `_download()` 回傳 `DownloadResult(outcome, path)` 取代 `Path | None`
- `outcome` 為 `MediaOutcome` enum: `DOWNLOADED`, `SKIPPED`, `FAILED`
- 檔案已存在且 size > 0 → `SKIPPED`
- 成功寫入並 rename → `DOWNLOADED`
- 任何 exception → `FAILED`

**PBT 屬性**:
- INVARIANT: outcome 與檔案系統狀態一致 — `DOWNLOADED` 時 dest 存在且 size > 0；`SKIPPED` 時 dest 存在且未被修改；`FAILED` 時 dest 不存在或為 .part
- ROUND-TRIP: 對同一 (url, dest) 連續呼叫兩次，第二次必為 `SKIPPED`
- IDEMPOTENCY: `SKIPPED` 結果不改變檔案的 mtime 或 content

## S3: RichSink 渲染

### S3.1: TTY 模式
- 使用 `rich.progress.Progress` 搭配 `SpinnerColumn` + `TextColumn`
- 貼文進度: spinner + "Processing posts: {N}" (total=None, 因分頁總數未知)
- 媒體進度: 每篇貼文的媒體以 "{done}/{total}" 更新 task description
- `Console(stderr=True)` — 所有輸出走 stderr

### S3.2: 完成摘要
- `TARGET_DONE` 事件觸發一行摘要:
  - 成功: `✓ {target_key}: {posts} posts, {downloaded} downloaded, {skipped} skipped`
  - 有失敗: `✗ {target_key}: {posts} posts, {downloaded} downloaded, {failed} failed`
- 使用 `Console.print()` 輸出（非 Progress 內部）

### S3.3: 非 TTY
- `__main__.py` 檢查 `sys.stderr.isatty()`
- 非 TTY → 注入 `NullSink`，僅靠 RichHandler logging 輸出

## S4: Captcha 暫停

### S4.1: Pause/Resume 機制
- `WeiboLoaderContext.__init__` 接受可選 `on_captcha_pause: Callable | None` 和 `on_captcha_resume: Callable | None`
- `_solve_captcha()` 呼叫前觸發 `on_captcha_pause()`，呼叫後觸發 `on_captcha_resume()`
- `RichSink` 提供 `pause()` → `Progress.stop()` 和 `resume()` → `Progress.start()`

**PBT 屬性**:
- INVARIANT: pause/resume 必成對 — 對任意 captcha 解決序列（成功/失敗），pause 次數 == resume 次數
- FALSIFICATION: Mock pause/resume counters，跑隨機 captcha 場景，驗證計數相等

## S5: Logging 整合

### S5.1: RichHandler
- `__main__.py` 設定 `logging.basicConfig(handlers=[RichHandler(console=console)])`
- `console` 與 `RichSink` 共用同一個 `Console(stderr=True)` 實例
- 確保 `logger.exception()` 訊息在進度條上方滾動

## S6: Stage Messages

### S6.1: 發送時機
| 時機 | message |
|------|---------|
| 解析目標 | `"Resolving {target_key}"` |
| 取得 visitor cookies | `"Fetching visitor cookies"` |
| Rate limit 等待 | `"Rate limited, waiting..."` |
| Captcha 偵測 | `"Captcha detected"` |

### S6.2: Rate Limit 去抖
- rate limit stage event 僅在等待時間 > 2 秒時發送，避免刷屏
