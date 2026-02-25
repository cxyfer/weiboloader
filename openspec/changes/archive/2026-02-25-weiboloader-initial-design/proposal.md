# Proposal: weiboloader — Weibo Media Downloader

## Context

用戶需要一個類似 [instaloader](https://github.com/instaloader/instaloader) 的微博媒體下載工具，支援從瀏覽器取得 cookies、具備完善的限流處理機制，並提供 CLI + Python API 雙介面。

現有參考實作 [weiboPicDownloader](https://github.com/cxyfer/weiboPicDownloader) 功能簡陋且頻繁觸發限流，需要從架構層面重新設計。

## Requirements

### R1: CLI 介面 — 仿 instaloader 指令格式

**場景**: 用戶透過終端機下載微博媒體

```bash
# 下載用戶貼文（支援 UID 或暱稱）
weiboloader <uid_or_nickname> [<uid_or_nickname> ...]

# 下載超話
weiboloader "#超話名稱"
weiboloader "#100808containerid"

# 下載單條微博（透過 mid 或 URL）
weiboloader -mid <mid>
weiboloader "https://m.weibo.cn/detail/<mid>"

# 搜尋關鍵字下載
weiboloader ":search keyword"
```

**約束**:
- Target 解析邏輯參照 instaloader 的 prefix-based pattern
- 支援多 target 批次處理，單一 target 失敗不影響其他
- Exit codes 參照 instaloader: 0=成功, 1=部分失敗, 2=初始化失敗, 3=認證失敗, 5=用戶中斷

### R2: 認證 — browser_cookie3 + 手動指定

**場景**: 用戶需要提供微博登入態 cookie

```bash
# 從瀏覽器自動提取
weiboloader --load-cookies chrome <target>
weiboloader --load-cookies firefox <target>

# 手動指定 cookie 字串或檔案
weiboloader --cookie "SUB=xxx; SUBP=yyy" <target>
weiboloader --cookie-file ./cookies.txt <target>

# Session 持久化
weiboloader --sessionfile ./session.dat <target>
```

**約束**:
- 使用 `browser_cookie3` 從 Chrome/Firefox/Edge 等提取 `.weibo.cn` 域的 cookies
- Cookie 必須包含 `SUB` 欄位才視為有效
- Session 可序列化到檔案，避免重複提取
### R3: 限流控制 — Sliding Window Rate Controller

**場景**: 避免觸發微博 API 限流 (HTTP 403/418)

**約束**:
- 參照 instaloader 的 `RateController` 實作 sliding window 演算法
- API 請求預設限制: 30 req / 10 min（微博比 Instagram 更嚴格）
- 媒體下載請求獨立計數，不與 API 請求共用窗口
- 主動節流 (proactive): 每次請求前計算等待時間
- 被動處理 (reactive): 收到 403/418 時指數退避 (exponential backoff)
- `RateController` 可被子類覆寫，允許用戶自訂限流策略
- 提供 `--request-interval` 參數讓用戶調整最小請求間隔

### R4: CAPTCHA 處理 — Playwright 互動式

**場景**: 限流觸發驗證碼時，開啟瀏覽器讓用戶手動完成驗證

**約束**:
- 偵測到驗證碼回應時 (HTTP 418 或特定 redirect pattern)，自動啟動 Playwright chromium
- 導航到驗證頁面，等待用戶手動完成 CAPTCHA
- 驗證完成後自動提取更新的 cookies 並繼續下載
- 設定超時 (預設 300 秒)，超時則 graceful abort 當前 target
- Playwright 為 optional dependency，未安裝時 fallback 為暫停等待模式

### R5: 下載內容控制

**場景**: 用戶選擇下載哪些類型的媒體

```bash
weiboloader <target>                    # 預設: 圖片 + 影片
weiboloader --no-videos <target>        # 僅圖片
weiboloader --no-pictures <target>      # 僅影片
weiboloader --metadata-json <target>    # 同時儲存貼文 JSON 元資料
weiboloader --post-metadata-txt "{date}: {text}" <target>  # 儲存文字摘要
```

**約束**:
- 圖片一律取最大尺寸 (`pic.large.url`)
- 影片優先順序: `stream_url_hd` > `mp4_720p_mp4` > `mp4_hd_url` > `stream_url`
- 支援 `--no-pictures`, `--no-videos` 開關
- 元資料 JSON 預設不儲存，需明確啟用
### R6: 檔案命名與目錄結構

**場景**: 下載的媒體需要有組織的儲存結構

```bash
weiboloader --dirname-pattern "{nickname}" <target>
weiboloader --filename-pattern "{date:%Y%m%d}_{mid}_{index:02}" <target>
```

**約束**:
- 預設目錄結構: `./{nickname}/` (用戶), `./topic/{topic_name}/` (超話)
- 預設檔名模板: `{date}_{name}`
- 支援變數: `{nickname}`, `{uid}`, `{mid}`, `{bid}`, `{date}`, `{date:FORMAT}`, `{index}`, `{index:PAD}`, `{text}`, `{type}`
- 檔名安全化: 移除 `\/:*?"<>|` 字元
- `{text}` 截斷至 50 字元

### R7: 篩選與增量更新

**場景**: 用戶只想下載特定時間範圍或增量更新

```bash
weiboloader --post-filter "date > '2025-01-01'" <target>
weiboloader --count 50 <target>
weiboloader --fast-update <target>
weiboloader --latest-stamps stamps.json <target>
```

**約束**:
- `--post-filter`: 支援 Python 表達式篩選 (參照 instaloader 的 filterstr_to_filterfunc)
- `--count N`: 限制下載數量
- `--fast-update`: 遇到已下載的貼文即停止
- `--latest-stamps`: 記錄每個 target 的最後下載時間戳，僅下載更新內容

### R8: 可恢復下載 (Resumable)

**場景**: 下載中斷後可從斷點繼續

**約束**:
- 參照 instaloader 的 `NodeIterator` freeze/thaw 機制
- 分頁遊標 (cursor) 序列化到 `.json` 檔案
- 已下載的檔案自動跳過 (檢查檔案存在且大小 > 0)
- `--no-resume` 可停用此功能

## Architecture

```
weiboloader/
├── __init__.py          # Public API exports
├── __main__.py          # CLI entry: `python -m weiboloader`
├── weiboloader.py       # WeiboLoader 協調器 (下載邏輯)
├── context.py           # WeiboLoaderContext (HTTP session, auth, rate control)
├── structures.py        # Post, User, SuperTopic 資料結構
├── nodeiterator.py      # 分頁迭代器 + 可恢復機制
├── exceptions.py        # 異常層級
└── _captcha.py          # Playwright CAPTCHA handler (optional)
```

### 層級關係

```
CLI (__main__.py)
 └─ WeiboLoader (weiboloader.py)        — 下載協調
     └─ WeiboLoaderContext (context.py)  — HTTP + Auth + Rate Control
         ├─ RateController               — Sliding window 限流
         ├─ requests.Session             — HTTP 連線池
         └─ CaptchaHandler (_captcha.py) — CAPTCHA 互動 (optional)
```

### Weibo API 端點 (m.weibo.cn)

| 用途 | 端點 | 參數 |
|------|------|------|
| 用戶資訊 | `/api/container/getIndex` | `type=uid&value={uid}` |
| 暱稱→UID | `/n/{nickname}` (302 redirect) | — |
| 用戶貼文 | `/api/container/getIndex` | `containerid=107603{uid}&page={n}` |
| 超話貼文 | `/api/container/getIndex` | `containerid={id}_-_feed&page={n}` |
| 超話搜尋 | `/api/container/getIndex` | `containerid=100103type%3D98%26q%3D{kw}` |
| 單條微博 | `/detail/{mid}` | — |
| 關鍵字搜尋 | `/api/container/getIndex` | `containerid=100103type%3D1%26q%3D{kw}` |

### 依賴

| 套件 | 用途 | 必要性 |
|------|------|--------|
| `requests` | HTTP client | 必要 |
| `browser_cookie3` | 瀏覽器 cookie 提取 | 必要 |
| `playwright` | CAPTCHA 互動式處理 | 可選 (optional extra) |

## Success Criteria

1. `weiboloader <uid>` 可成功下載用戶所有圖片+影片至 `./{nickname}/` 目錄
2. `weiboloader "#超話名"` 可成功下載超話貼文媒體
3. `weiboloader -mid <mid>` 可下載單條微博媒體
4. `weiboloader ":search keyword"` 可下載搜尋結果媒體
5. `--load-cookies chrome` 可從 Chrome 提取有效 cookie
6. 連續下載 200+ 貼文不觸發限流 (rate controller 正常運作)
7. 觸發 CAPTCHA 時自動開啟 Playwright 視窗，用戶完成驗證後繼續下載
8. 中斷後重新執行可從斷點恢復 (resume)
9. `--fast-update` 可正確偵測已下載內容並停止
10. 內部模組化設計，未來可擴展為 Python API
