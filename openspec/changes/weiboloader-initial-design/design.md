## Context

weiboloader 是一個從零開始設計的微博媒體下載工具，靈感來自 instaloader 的架構。現有參考實作 weiboPicDownloader 存在以下核心缺陷：無限流控制（僅 `time.sleep`）、全量記憶體收集後才下載、依賴 Selenium 取 cookie（過重且脆弱）、無可恢復下載機制、用戶/超話抓取邏輯重複。

目標 API 為 `m.weibo.cn` 行動端介面，該 API 無官方文件、頻繁變動欄位結構、風控嚴格（30 req/10min 即可能觸發 403/418）。

## Goals / Non-Goals

**Goals:**
- 提供 CLI + Python API 雙介面的微博媒體下載工具
- 實作企業級限流控制（sliding window + exponential backoff）
- 支援可恢復下載（checkpoint freeze/thaw）
- 支援增量更新（fast-update / latest-stamps）
- 模組化架構，API 變動時只需修改隔離層

**Non-Goals:**
- v1 不實作 `--post-filter` Python 表達式篩選
- 不支援微博桌面版 API（`weibo.com/ajax`）
- 不實作自動 CAPTCHA 解碼（僅人工互動）
- 不支援評論/轉發鏈爬取
- 不實作 async HTTP（v1 採同步模型）

## Decisions

### D1: HTTP Stack — requests sync

**選擇**: `requests` + `ThreadPoolExecutor`（media 並發下載）

**替代方案**:
- `httpx`/`aiohttp` 全 async：media 吞吐更好，但 API 限流 30 req/10min 下 async 優勢有限，且 checkpoint/exception 串接複雜度高
- Hybrid（API sync + media async）：兩種並發模型混用，維護成本高

**理由**: 與 instaloader 同步模型一致，除錯簡單，resume/exception 流程直觀。API 吞吐受限流約束，async 收益不大。

### D2: 模組架構

```
weiboloader/
├── __init__.py          # Public API exports
├── __main__.py          # CLI entry + target parsing + exit code
├── weiboloader.py       # WeiboLoader orchestrator (download coordination)
├── context.py           # WeiboLoaderContext (HTTP session, auth, CAPTCHA)
├── adapter.py           # API field isolation (raw JSON → internal models)
├── ratecontrol.py       # RateController (sliding window, subclassable)
├── structures.py        # Dataclasses: User, SuperTopic, Post, MediaItem, CursorState
├── nodeiterator.py      # Paginated iterator + freeze/thaw
├── exceptions.py        # Exception hierarchy → exit code mapping
└── _captcha.py          # Playwright CAPTCHA handler (optional)
```

**與 proposal 差異**:
- 新增 `adapter.py`：隔離 `m.weibo.cn` 原始 JSON 欄位與內部資料模型，API 變動時只改 adapter
- 新增 `ratecontrol.py`：R3 要求可 subclass，獨立模組可測試性高，支援策略切換

### D3: API 隔離層 — adapter.py

**選擇**: 獨立 `adapter.py` 模組，提供 `parse_user_info(raw)`, `parse_post(raw_card)`, `parse_supertopic(raw)` 等函式

**替代方案**: 在 `structures.py` 的 `from_api_payload()` 處理

**理由**: 職責分離 — `structures.py` 只定義資料模型，`adapter.py` 負責 API 欄位映射。當微博從 `/getIndex` 遷移到 `/ajax` 介面時，只需替換 adapter，不影響下游邏輯。structures 保留 `raw: dict` 欄位以容忍欄位漂移。

### D4: RateController — 獨立模組 + 可插拔策略

**選擇**: `ratecontrol.py` 提供 `BaseRateController` 抽象基類 + `SlidingWindowRateController` 預設實作

**核心介面**:
- `wait_before_request(bucket: str)` — proactive throttle
- `handle_response(bucket: str, status_code: int)` — reactive backoff
- 兩個獨立 bucket: `"api"` (30 req/600s) 和 `"media"` (獨立窗口)

**Backoff 參數**: `base_delay=30s`, `max_delay=600s`, `jitter_range=0.0~0.5*delay`

### D5: 時間戳 — CST (+0800) aware datetime

**選擇**: 內部統一使用 `datetime` with `timezone(timedelta(hours=8))`

**理由**: 微博 API 回傳的時間本身就是 CST，保留原始時區對中國用戶更直覺。latest-stamps 和檔名模板中的日期直接反映北京時間。

**解析策略**: adapter.py 統一處理微博的多種日期格式（`%a %b %d %H:%M:%S %z %Y`, `X分钟前`, `昨天`, `MM-DD`, `YYYY-MM-DD`），全部轉為 aware datetime。

### D6: Session 持久化 — 混合路徑

**選擇**: `--sessionfile <path>` 優先；未指定時預設 `~/.config/weiboloader/session.dat`

**序列化格式**: `pickle` (與 instaloader 一致，包含完整 `requests.Session` 狀態)

### D7: browser_cookie3 — Optional Extra

**選擇**: `pip install weiboloader[browser]` 安裝

**理由**: 該套件在 headless/Docker 環境不穩定（依賴本地瀏覽器 DB 路徑和 OS keyring）。降為可選依賴後，核心功能不受影響。

### D8: CAPTCHA — 可配置模式

**選擇**: `--captcha-mode auto|browser|manual|skip`，預設 `auto`（Playwright 可用則用，否則 pause-and-wait）

**Playwright 為 optional extra**: `pip install weiboloader[captcha]`

### D9: Resume — 每 target 一檔 + atomic write + lock

**Checkpoint 格式**: JSON，包含 `{version, cursor, seen_mids, options_hash, timestamp}`

**寫入策略**: write to `.tmp` → `fsync` → `rename`（atomic）

**併發保護**: target 級 `.lock` 檔案，拿不到鎖則 fail-fast

### D10: 下載完成判定 — exists && size > 0

**選擇**: v1 僅檢查檔案存在且大小 > 0

**部分下載保護**: 寫入 `.part` 暫存檔，完成後 rename

### D11: 錯誤容忍 — batch continue

**選擇**: 單 target 失敗記錄錯誤後繼續處理下一個 target

**KeyboardInterrupt**: 先 flush checkpoint/stamps，再 exit code 5

### D12: 依賴清單

| 套件 | 用途 | 必要性 |
|------|------|--------|
| `requests` | HTTP client | 必要 |
| `browser_cookie3` | 瀏覽器 cookie 提取 | 可選 `[browser]` |
| `playwright` | CAPTCHA 互動 | 可選 `[captcha]` |

## Risks / Trade-offs

**[R-HIGH] m.weibo.cn API 欄位不穩定** → adapter.py 隔離層 + defensive parsing + structures.raw fallback + APISchemaError

**[R-HIGH] 風控策略升級（新型 challenge/加密參數）** → 雙窗口限流 + jitter + CAPTCHA flow + 可調策略參數 + 可 subclass RateController

**[R-HIGH] Cookie 過期/提取失敗** → 明確 auth provider 優先順序 + SUB 驗證 + sessionfile 快速回退 + 友善錯誤訊息

**[R-MED] Resume 狀態與檔案不一致** → checkpoint version + options hash + size>0 判定 + .part 暫存

**[R-MED] CAPTCHA 依賴 GUI 環境** → fallback pause-and-wait + skip mode + timeout + 文件化

**[R-LOW] 檔名模板碰撞** → collision suffix (_1/_2) 或附加 media hash

## Open Questions

- 微博是否已開始強制 `st` / `_spr` 加密參數？需實測確認 m.weibo.cn API 目前的存取狀態。
- `pickle` 序列化 session 是否有跨 Python 版本相容性問題？是否改用 JSON + cookie jar 手動序列化更穩健？
