## Why

目前 `weiboloader` 已經能從 Weibo 貼文解析出 `created_at`，並把它用在檔名 `{date}` 上，但實際下載出的圖片、影片與 metadata sidecar 檔案仍保留本機下載時刻作為 filesystem `mtime`。這會讓檔案管理器排序、同步工具與人工檢查時看到的時間軸，和貼文實際時間不一致，需要把檔案時間語意補齊並明確規格化。

## What Changes

- 將新下載成功的圖片與影片檔案 `mtime` 設為現有流程已解析出的貼文時間戳 `post.created_at`。
- 將 `--metadata-json` 與 `--post-metadata-txt` 產生的 sidecar 檔案也對齊為同一個貼文時間戳。
- 保持既有 skip 語意：對於已存在且非空、因此被判定為 `SKIPPED` 的檔案，不額外修改其 `mtime`。
- 明確規範時間來源沿用現有 timezone-aware `post.created_at`，讓檔名 `{date}` 與 filesystem `mtime` 的語意一致。
- 新增回歸驗證，覆蓋新下載檔案與 metadata sidecar 的時間戳傳遞，以及 skip 路徑不產生新副作用。

## Capabilities

### New Capabilities
- `file-timestamps`: 規範下載產物與 sidecar 檔案的 filesystem 時間戳來源、套用範圍、skip 行為與可驗證結果。

### Modified Capabilities
- None.

## Impact

- Affected code: `weiboloader/weiboloader.py` 的 media download 與 metadata 寫檔路徑。
- Affected data flow: 沿用 `weiboloader/adapter.py` 與 `weiboloader/structures.py` 中既有的 `Post.created_at` 傳遞，不預期新增外部依賴。
- Affected validation: `tests/test_weiboloader.py`（必要時含 `tests/test_integration.py`）需要驗證 `st_mtime` 與 `post.created_at` 的一致性及 skip 邊界。
- Non-goals: 不新增 CLI 選項、不改變 progress schema、不變更既有 fast-update / coverage / resume 的判定邏輯。
