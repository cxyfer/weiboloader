## Context

weiboloader 的 Rich 進度顯示僅提供 `Media X/Y` 格式，缺少檔名與 POST 計數。此外，串流下載與 `as_completed` 均無超時保護，導致程式可能無限期卡死。

## Goals / Non-Goals

**Goals:**
- 狀態欄顯示 POST 索引、媒體進度、檔名
- 為串流下載增加讀取超時（每分塊 60s）
- 為每篇 POST 的媒體下載群組增加全域超時
- 超時失敗時清理 `.part` 暫存檔

**Non-Goals:**
- 不實作斷點續傳（partial content / range request）
- 不變更 CLI 參數介面（超時值為內部常數）
- 不遷移至 async I/O

## Decisions

### D1: UIEvent 欄位擴展（非新事件類型）

**選擇**: 在 `UIEvent` dataclass 末尾新增 `filename: str | None` 和 `post_index: int | None`

**替代方案**:
- 新增專用事件類型 `MEDIA_PROGRESS_DETAIL`：更清晰但增加事件處理分支和 API 面積
- 在事件中攜帶預格式化字串：UI 邏輯簡單但表現層洩漏至核心邏輯

**理由**: 現有 UIEvent 採平坦結構、全 optional 欄位模式。新增 2 個 optional 欄位保持一致性，NullSink 無需變更，向後完全相容。

### D2: 串流讀取超時 — requests timeout 元組

**選擇**: `_download()` 傳遞 `timeout=(connect_timeout, read_timeout)` 元組至 `context.request()`，`read_timeout = 60`

**替代方案**:
- urllib3 adapter 級全域超時：集中管理但影響 API 請求路徑
- socket 級預設超時：粒度太粗

**理由**: `requests` 原生支援 `(connect, read)` 元組，變更僅影響 `_download()` 呼叫點。`context.request()` 已支援 `kwargs.pop("timeout", self.req_timeout)` 透傳機制，無需修改 context 層。

**重要澄清**: `requests` 的 scalar timeout 同時作用於 connect 和 read。目前 `req_timeout=20` 意味著 read timeout 也是 20s。此變更將 streaming 的 read timeout 從 20s 提升至 60s（因為大型媒體檔案需要更長的分塊等待時間），connect timeout 維持 20s。

### D3: as_completed 全域超時 — 保持單一 executor

**選擇**: `as_completed(futures, timeout=max(60, media_count * 30))` + `cancel()` 未完成 futures

**替代方案**:
- `threading.Event` 協作取消：更乾淨的取消但需重構 `_download()` 內部迴圈
- 每個 POST 獨立 executor 生命週期：隔離性好但建立開銷高
- `shutdown(wait=False, cancel_futures=True)` + 重建 executor：複雜且有邊際條件

**理由**: R2 的 60s read_timeout 已限制了 zombie 執行緒最長存活時間。`cancel()` 可阻止排隊中的 futures 啟動；已在執行中的 futures 最遲在 60s 後因 read_timeout 自然終止。保持單一 executor 簡化程式碼。

### D4: 超時時 Checkpoint 不前進

**選擇**: `as_completed` 全域超時觸發時，跳過 `_save_ck()` 呼叫，使該 POST 在下次執行時被重新處理

**替代方案**:
- 照常前進 checkpoint：簡單但超時的媒體永遠不會被重試
- 任何 FAILED 都不前進：過於保守，單次網路錯誤就導致重複處理

**理由**: 個別 `_download()` 失敗（含 R2 read_timeout）已回傳 `FAILED` 且所有 futures 正常完成，checkpoint 應照常前進（已存在的檔案會被 SKIPPED）。僅 R3 全域超時（部分 futures 未完成）時不前進，確保未處理的媒體在下次執行時有機會重試。

### D5: .part 暫存檔清理

**選擇**: `_download()` 的 `except` 路徑增加 `part.unlink(missing_ok=True)`

**理由**: 避免磁碟殘留。配合 D4（checkpoint 不前進），下次重試時 `dest` 不存在，`_download()` 會重新嘗試。

## Constants

| 常數 | 值 | 位置 | 說明 |
|------|-----|------|------|
| `_STREAM_READ_TIMEOUT` | `60` | `weiboloader.py` (module-level) | 串流下載每分塊讀取超時（秒） |
| `_PER_MEDIA_TIMEOUT` | `30` | `weiboloader.py` (module-level) | as_completed 公式中每個媒體項目的時間配額（秒） |
