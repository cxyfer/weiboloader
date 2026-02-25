# Proposal: progress-display-and-download-reliability

## Context

目前 `weiboloader` 的 Rich 進度顯示過於簡略——狀態欄僅顯示 `Media 5/6` 格式，缺少：
- 正在下載的檔案名稱
- 當前處理的是第幾篇 POST（及已知的總數上限）

此外，使用者觀察到下載偶爾在某個 Media 項目處卡住（例如停在 `Media 5/6` 不繼續），程式不會超時也不會跳過。

### 已發現的約束集（Constraint Sets）

**硬約束：**
- `as_completed(futures)` 未設定 `timeout`，若任一下載執行緒掛起，主執行緒將無限期阻塞（`weiboloader.py:164`）
- `resp.iter_content()` 無分塊級超時——HTTP 連線超時 20 秒僅作用於初始連線，串流傳輸開始後伺服器若停止回應資料，`iter_content()` 將無限期等待（`weiboloader.py:260`）
- `requests.Session` 預設 `HTTPAdapter` 連線池每 host 上限 10，若 `max_workers` 超過此值可能導致執行緒等待連線（`context.py:54`）
- `UIEvent` 不攜帶檔案名稱和 POST 索引資訊，`RichSink` 無法顯示這些資訊（`ui.py:31-43`）
- 迭代器無法提供總 POST 數（API 分頁，總數未知）

**軟約束：**
- 現有事件架構（`UIEvent` + `ProgressSink`）已穩定，變更應保持向後相容
- 狀態欄空間有限，格式需簡潔
- `_download()` 的異常處理已回傳 `DownloadResult(MediaOutcome.FAILED, dest)`，不會向上拋出

**依賴：**
- `requests` 的 `iter_content()` 行為由 urllib3 的 socket 超時控制
- `ThreadPoolExecutor` 的 `as_completed()` 支援 `timeout` 參數

## Requirements

### R1: 狀態欄顯示檔案名稱與 POST 計數

**場景**: 使用者下載某用戶的貼文媒體，需要知道目前處理到哪篇 POST、正在下載哪個檔案

**預期行為**:
- 狀態欄格式：`#3 Media 5/6 image_001.jpg`
- `#N` 為累計已處理 POST 數（因 API 分頁總數未知，不顯示 `X/Y` 形式）
- 檔案名稱取自 `dest.name`（最終檔案路徑的檔名部分）

**約束**:
- `UIEvent` 新增 `filename: str | None` 和 `post_index: int | None` 欄位
- `MEDIA_DONE` 事件攜帶檔案名稱和當前 POST 索引
- `RichSink._handle()` 使用新欄位渲染狀態欄
- 向後相容：新欄位為 optional，`NullSink` 不受影響

### R2: 為串流下載增加讀取超時

**場景**: 微博 CDN 偶爾在傳輸途中停止回應，導致 `iter_content()` 無限期等待

**預期行為**:
- 串流下載的每個分塊讀取有超時保護
- 超時後該檔案標記為 FAILED，繼續處理後續媒體

**約束**:
- 使用 `requests` 的 `timeout` 參數元組形式 `(connect_timeout, read_timeout)` 設定讀取超時
- 讀取超時預設 60 秒（媒體檔案可能較大，需留足時間）
- 不引入額外依賴

### R3: 為 `as_completed` 增加全域超時

**場景**: 單篇 POST 的媒體下載群組不應無限期等待

**預期行為**:
- 每個 POST 的 `as_completed()` 設定合理超時
- 超時後未完成的 futures 視為 FAILED，繼續處理下一篇 POST

**約束**:
- 超時 = `media_count * per_media_timeout`，提供合理上限
- 超時的 futures 呼叫 `cancel()` 嘗試取消
- 錯誤計入 `failed` 統計

## Success Criteria

1. 狀態欄顯示 `#N Media X/Y filename.ext` 格式
2. 串流下載超過 60 秒未收到資料時自動標記為 FAILED 並繼續
3. 單篇 POST 的下載群組不超時卡死，超時後繼續下一篇
4. 現有功能（checkpoint、rate control、fast_update）行為不變
5. 測試套件全數通過
