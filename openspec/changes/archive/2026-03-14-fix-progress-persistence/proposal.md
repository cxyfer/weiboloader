## Why

逐步放大 `--count` 重新執行時，現有 progress persistence 會讓 resume 與 coverage 呈現不一致：`seen_mids` 可能回退、coverage 可能以單一區間掩蓋真實進度，且已下載內容仍會再次進入處理流程。這使自動恢復在增量擴張下載範圍時不再可靠，必須釐清並修正其持久化語義。

## What Changes

- 修正 count-limited 與其他非完成停止點下的 progress persistence，確保 resume 不會回退到過舊的 safe checkpoint。
- 補強 partial-page / partial-group 恢復語義，避免逐步放大 `--count` 時遺失尚未處理的貼文或重複處理已見內容。
- 釐清 coverage 與 resume 的協作規則，確保 coverage 只代表真實完整成功的範圍，不會錯誤掩蓋 gap 或未完成 group。
- 明確約束已覆蓋或已落盤內容在 rerun 時的可觀察行為，避免使用者看到誤導性的重複下載流程。
- 新增針對 `--count` 逐步放大、page 內中斷與 rerun 的回歸驗證。

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `resumable-download`: change resume and coverage requirements so progressively increasing `--count` preserves monotonic recovery, does not lose partially fetched page state, and does not misreport already completed content as needing download work.

## Impact

- Affected code: `weiboloader/weiboloader.py`, `weiboloader/nodeiterator.py`, `weiboloader/progress.py`
- Affected validation: `tests/test_weiboloader.py`, `tests/test_integration.py`
- Affected behavior: per-target progress persistence, rerun skip semantics, user-visible progress during resumed downloads
