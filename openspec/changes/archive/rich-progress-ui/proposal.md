# Proposal: rich-progress-ui — CLI 進度與狀態回饋

## Context

目前 `weiboloader` 在下載過程中完全沒有使用者回饋——沒有進度條、沒有狀態訊息、沒有完成摘要。使用者執行 `weiboloader --visitor-cookies <target>` 後只能看到空白的終端，無法判斷程式是否正在運作、已處理多少內容、或是否遇到錯誤。

## Requirements

### R1: 依賴管理 — rich 作為核心依賴

**場景**: 安裝 weiboloader 時自動包含 rich

**約束**:
- `rich` 加入 `pyproject.toml` 的 `dependencies`（非 optional）
- 版本約束: `rich>=13.0`
- 理由: 進度回饋是 CLI 工具的基本功能，不應為 optional

### R2: 整體進度條 — 貼文與媒體層級

**場景**: 使用者下載某用戶的所有貼文媒體

**預期行為**:
- 顯示已處理貼文數（因 API 分頁，總數未知，使用 spinner + counter 形式）
- 顯示當前貼文的媒體下載進度（已完成/總數）
- 下載完成或中斷時顯示摘要統計（已處理貼文數、已下載媒體數、失敗數）

**約束**:
- 不顯示單檔位元組級進度條（使用者選擇「僅整體進度」）
- 進度輸出至 stderr，不污染 stdout
- 支援非 TTY 環境（CI/pipe）graceful fallback：靜默或純文字

### R3: 階段性狀態訊息

**場景**: 使用者需要知道程式目前在做什麼

**預期行為**:
- 解析目標時：顯示目標名稱/UID
- 取得 visitor cookies 時：顯示取得中狀態
- 開始下載時：顯示目標目錄
- 遇到 rate limit / captcha 時：顯示等待訊息
- 跳過已存在檔案時：計入 skipped 統計

**約束**:
- 訊息簡潔，不超過一行
- 使用 `rich.console.Console(stderr=True)` 統一輸出

### R4: 完成摘要

**場景**: 下載結束後使用者需要知道結果

**預期行為**:
```
✓ target_name: 12 posts, 47 media downloaded, 3 skipped, 1 failed
```

**約束**:
- 每個 target 一行摘要
- 失敗時使用不同顏色/符號區分

### R5: 架構約束 — 最小侵入

**約束**:
- 新增獨立模組 `weiboloader/ui.py` 封裝所有 rich 相關邏輯
- `WeiboLoader` 透過回呼（callback）或事件介面與 UI 層溝通，不直接 import rich
- `__main__.py` 負責建立 UI 實例並注入 `WeiboLoader`
- 測試不依賴 rich 的渲染輸出

## Success Criteria

1. 執行 `weiboloader --visitor-cookies <target>` 時，終端即時顯示進度與狀態
2. 非 TTY 環境下不產生 ANSI escape codes
3. Ctrl+C 中斷時仍顯示已完成的摘要
4. 現有功能（下載、checkpoint、rate control）行為不變
5. 測試套件全數通過
