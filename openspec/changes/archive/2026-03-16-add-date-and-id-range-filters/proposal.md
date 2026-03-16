## Why

使用者目前只能用 `--count`、`--fast-update` 與 coverage-based incremental 行為縮小下載範圍，無法直接以日期區間或微博 ID 區間做精準抓取。這讓補抓特定時段、按 MID 做局部回補，以及替代尚未實作的 `--post-filter` 都變得笨重且容易誤用，因此需要提供一組明確、可驗證的 boundary 旗標。

## What Changes

- 新增明確的區間旗標：`-b/--date-boundary START:END` 與 `-B/--id-boundary START:END`。
- 日期 boundary 採包含端點的開區間語法，接受 `YYYYMMDD` 與 `YYYY-MM-DD`；例如 `-b 20251201:` 代表下載 `2025-12-01`（含當天）之後的貼文，省略端點時以 `:` 表示無界。
- ID boundary 以 `Post.mid` 做數值且包含端點的比較，支援相同的 `START:END` 語法。
- 將 boundary 篩選納入既有下載主迴圈，使其與 `--count`、`--fast-update`、metadata 輸出與媒體下載行為一致互動。
- 將 boundary 選項視為 progress compatibility 的一部分；當 boundary 改變時，既有 `resume` 與 `coverage` 不得沿用。
- 補齊 CLI、過濾語義與 progress 相容性測試與規格文件。

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `cli-interface`: 新增 date/id boundary 旗標、help 文案與開區間驗證規則。
- `filtering-incremental`: 擴充貼文過濾規則，定義日期區間與 MID 區間的包含式語義，以及它們和 `--count`、`--fast-update`、coverage skip 的互動。
- `resumable-download`: 將 boundary 選項納入 `options_hash` 相容性，明確規定 boundary 變更時如何忽略不相容的 `resume` / `coverage`。

## Impact

- Affected code: `weiboloader/__main__.py`, `weiboloader/weiboloader.py`, boundary/date parsing helpers, progress compatibility hashing, `tests/test_cli.py`, `tests/test_weiboloader.py`.
- Affected specs: `openspec/specs/cli-interface/spec.md`, `openspec/specs/filtering-incremental/spec.md`, `openspec/specs/resumable-download/spec.md`.
- APIs and fetching model remain page-based; no new external dependency is expected for this change.
