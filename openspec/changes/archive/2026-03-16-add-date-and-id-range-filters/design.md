## Context

`weiboloader` 目前只支援用 `--count`、`--fast-update` 與 coverage-based incremental 行為縮小抓取範圍，尚未提供可直接限制貼文集合的 boundary 旗標。CLI 入口 `weiboloader/__main__.py` 目前沒有 date/id boundary 解析；下載主迴圈 `weiboloader/weiboloader.py` 會依序套用 `count`、coverage skip、`fast-update`、metadata 寫入與媒體下載；progress compatibility 目前只由輸出相關選項控制。

現有資料流同時提供了本次變更可依賴的兩個基礎：
- `weiboloader/context.py` 的 `_parse_posts()` 已經會展平 `card_group` 並用 `mid` 去重，代表 User timeline 第一頁的特殊卡片包裝不需要在 boundary 層重做一次。
- `weiboloader/adapter.py` 的 `parse_post()` 會保留完整 `raw` payload，讓 boundary 邏輯可以直接檢查原始微博欄位（例如 pinned 標記）而不必先改動 `Post` 資料模型。

這次 change 要解決的不是新抓取來源，而是把「哪些貼文應進入處理流程」與「何時可以安全停止繼續翻頁」定義成可驗證、可測試、可序列化的規則，並且讓這些規則與既有 resume / coverage 語義一致。使用者已經明確決定以下產品約束：
- `--date-boundary START:END` 以貼文原始時區的日曆日做包含式比較；若貼文時間沒有時區資訊，則視為 CST(+08:00)。
- `--id-boundary START:END` 以 `Post.mid` 的非負十進位整數值做包含式比較。
- 同時指定 date/id boundary 時採 AND 交集。
- 只有 `UserTarget` 允許 boundary 作為 cutoff 條件；`SearchTarget`、`SuperTopicTarget` 僅做過濾；`MidTarget` 只決定單貼文是否被納入，不涉及 pagination cutoff。
- User timeline 的 pinned post 不得觸發 cutoff，但若其自身落在 boundary 內，仍可正常下載。
- boundary 改變時，既有 `resume` 與 `coverage` 一律視為不相容。

## Goals / Non-Goals

**Goals:**
- 新增 date/id boundary 的 canonical parsing、嚴格驗證與 CLI help 語義，讓等價輸入產生等價行為與等價 progress compatibility。
- 定義 boundary 在 User timeline 上的 cutoff 規則，並明確排除 pinned post 對 cutoff 的影響。
- 讓 boundary 過濾與 `--count`、`--fast-update`、metadata 輸出、媒體下載、resume、coverage 形成一致的 in-range 優先語義。
- 讓 boundary 變更能可靠地失效既有 resume/coverage，避免舊 progress 污染新選取集合。
- 補齊可機械驗證的 spec 與測試性質，特別是 canonicalization、subset 關係、side effect 隔離與 cutoff/pinned 互動。

**Non-Goals:**
- 不實作通用 `--post-filter` 語言，也不預留可執行表達式系統。
- 不新增外部依賴，也不改變既有 page-based 抓取來源。
- 不把 Search / SuperTopic 升級成 cutoff-safe source；這次只為 User timeline 定義 cutoff 保證。
- 不重做 progress 檔案格式或拆成多種 hash 欄位；沿用既有 unified progress record。
- 不把 pinned 狀態提升成新的 `Post` 欄位；本次只在 boundary 判斷流程中使用原始 raw payload。

## Decisions

### D1. 導入 canonical boundary 值物件，所有比較都先經過單一正規化流程

新增一組 boundary parsing helpers，將 CLI 輸入解析為單一的 canonical 邊界表示，再交給 loader 使用。這組 helpers 必須同時處理 date 與 id 兩種 range，並提供：
- `parse_date_boundary(raw: str | None) -> DateBoundary | None`
- `parse_id_boundary(raw: str | None) -> IdBoundary | None`
- `serialize_boundary(...) -> str | None` 供 progress compatibility 使用

具體規則：
- `:` 等價於未設定該 boundary，不能建立新的 progress namespace。
- date boundary 接受 `YYYYMMDD` 與 `YYYY-MM-DD`，canonical form 一律序列化為 `YYYY-MM-DD:YYYY-MM-DD` 的開端點可省略格式。
- id boundary 端點必須是非負十進位整數字串，canonical form 會去除前導零（保留單一 `0`）。
- 若 `START > END`，CLI 直接報初始化錯誤並回傳 exit code `2`。
- 單一端點缺省時視為無界，兩端皆存在時採包含式比較。

這個決策讓 `20250301:`、`2025-03-01:`、`00123:0456` 之類的等價輸入能共用同一份 progress compatibility，避免使用者僅因輸入字面不同而重掃。

**Alternatives considered:**
- **保留字面輸入參與 hash**：實作最簡單，但會讓等價查詢產生不同 progress namespace，違反這次 change 的可預測性目標。
- **自動交換 `START > END`**：對使用者較寬鬆，但會把錯誤輸入隱性修正成另一個查詢，降低 CLI 可驗證性。

### D2. `Post.created_at` 必須同時支援 boundary 的原始時區語義與既有 CST 正規化語義

使用者要求 date boundary 以貼文原始時區的日曆日比較，但目前 `parse_weibo_datetime()` 會把絕對時間字串直接轉成 CST，這會在實作前就丟失原始時區語義。因此這次設計明確區分兩種時間用途：
- **boundary 比較用途**：保留貼文原始時區的 aware datetime；若來源時間沒有時區資訊，則補成 CST。
- **既有 coverage / filename / grouped-run 用途**：仍透過 `_cst()` 轉成 CST 後使用。

為了達成這點：
- `parse_weibo_datetime()` 對帶有 `%z` 的絕對時間字串要保留原始 offset，不再立即 `astimezone(CST)`。
- `parse_weibo_datetime()` 對相對時間（例如 `xx分钟前`、`昨天 HH:MM`）與無時區日期字串仍建立 CST-aware datetime。
- `_cst()` 必須改為：aware datetime 轉 `astimezone(CST)`；naive datetime 補 `tzinfo=CST`。

這樣 boundary 可以依原始時區做日曆日比較，而既有依賴 CST 的 coverage sealing 與命名規則不需要另外發明第二套資料欄位。

**Alternatives considered:**
- **延續目前一律轉 CST 的做法**：會直接違反使用者剛確認的 boundary 語義。
- **在 `Post` 上新增第二個原始時間欄位**：可行，但對這次 change 來說是多餘資料模型擴張。

### D3. Boundary 判斷順序固定為「先選取集合，再決定 cutoff，再進入下載流程」

下載主迴圈中的 boundary 行為必須是機械化且 target-aware 的。對每篇貼文，處理順序固定如下：
1. 先判定是否為 pinned post。
2. 根據 active boundaries 計算該貼文是否 in-range。
3. 若 target 類型允許 cutoff，再根據 target-specific ordering 規則判定是 `continue`、`process` 還是 `break`。
4. 只有通過上述步驟且不是 coverage hit 的 in-range post，才會進入 metadata/media 處理流程。

具體 target 規則如下：
- **UserTarget**：
  - 假設忽略 pinned post 後，普通貼文在 `created_at` 與數值 `mid` 上都依遍歷順序單調遞減。
  - 若貼文只是不符合 upper bound（例如比 `END` 更新、或 `mid > END`），則 `continue` 繼續往後找更舊貼文。
  - 若非 pinned 貼文已低於任一 active lower bound（例如 `date < START` 或 `mid < START`），則可安全 `break` 結束 pagination。
  - pinned post 永遠不能觸發 `break`；它只參與 in-range 過濾。
- **SearchTarget / SuperTopicTarget**：永遠只做 filter，不做 cutoff；out-of-range 時一律 `continue`。
- **MidTarget**：只抓單一貼文，若 out-of-range 則 target 成功完成但 0 輸出。

這個決策把「boundary 是選取集合」與「boundary 何時可作停止條件」拆成兩個明確步驟，避免在 loader 內混成難以測試的 if/else 網狀邏輯。

**Alternatives considered:**
- **讓所有 target 都支援 cutoff**：最省判斷分支，但 Search/SuperTopic 沒有使用者已批准的排序保證，無法安全驗證。
- **完全不做 cutoff，只做 filter**：安全但失去使用者明確要求的 User timeline cutoff 行為。

### D4. Pinned post 以原始 payload 的 `mblogtype == 2` 判定，且只影響 cutoff，不改變 boundary 本身

User timeline 的 pinned 判定規則固定為：檢查 `post.raw.get("mblog", post.raw).get("mblogtype") == 2`。這與外部參考專案（特別是 `dataabc/weibo-crawler`）的一致性最高，且符合目前 `parse_post()` 會保留原始 `raw` payload 的現況。

Pinned 的行為規則固定為：
- 不得作為 `date-boundary` 或 `id-boundary` 的 cutoff 依據。
- 仍需套用一般 boundary AND 語義；若 pinned post 自身不在範圍內，則直接忽略。
- 若 pinned post 在範圍內，則可像一般貼文一樣參與 metadata / media 處理。
- pinned post 不因其 pinned 身分獲得特殊的 count、coverage 或 fast-update 權限。

這讓「忽略置頂對排序的干擾」與「置頂貼文本身仍可被精準回補」同時成立。

**Alternatives considered:**
- **完全排除 pinned post**：能避開排序問題，但違反使用者要求的「仍可下載」。
- **把 pinned 當普通貼文處理**：最簡單，但會讓第一頁置頂直接破壞 cutoff 正確性。

### D5. Boundary 與 coverage / `count` / `fast-update` 的交互統一採 in-range 優先語義

Boundary 生效後，只有「通過 boundary 且未被 coverage 排除」的貼文才算進入下載流程。這代表：
- `processed` / `--count` 只計算真正進入 post-processing 的 in-range 貼文。
- out-of-range 貼文不得寫 metadata、不得排程媒體下載、不得貢獻 `processed`。
- `--fast-update` 只檢查 in-range 貼文的目標媒體是否已存在；boundary 外貼文即使已存在檔案，也不能觸發早停。
- coverage 只對 in-range 貼文生效；out-of-range 貼文不會阻止 coverage sealing，也不會被視為同一組必須成功的成員。

為了符合這組語義，loader 內部的 `count` guard 必須從目前的「進入迴圈就先檢查」改成「boundary / coverage 都通過後、但 side effects 發生前」再判定是否達到上限。這能確保 count 的單位是「真正被處理的貼文」，而不是掃描到的原始頁面項目。

**Alternatives considered:**
- **把 out-of-range 貼文也算進 count**：可少改迴圈順序，但使用者得到的不是 boundary 內的前 N 篇，而是掃描過程中的第 N 個原始項目。
- **讓 boundary 外貼文也能觸發 fast-update**：有機會更快停，但會讓使用者明明指定了邊界卻被外部資料提前終止，語義不乾淨。

### D6. 沿用單一 `options_hash`，但其語義從「輸出相容性」擴充為「輸出 + 選取集合相容性」

現有 progress 檔案已經把 resume 與 coverage 都綁在同一個 `_options_hash` 上，因此這次不新增第二種 hash。相反地，直接擴充 `_hash_options()` 的 payload，把 canonical date/id boundary 納入其中，讓同一個 `options_hash` 代表「這次執行會產生同一組可觀測貼文集合與輸出副作用」。

payload 需新增：
- `date_boundary`: canonical string 或 `null`
- `id_boundary`: canonical string 或 `null`

保留不變的欄位：
- `dirname_pattern`
- `filename_pattern`
- `no_videos`
- `no_pictures`
- `metadata_json`
- `post_metadata_txt`

仍然不納入 hash 的 traversal-only options：
- `count`
- `fast_update`
- `no_resume`
- `no_coverage`

結果是：boundary 改變時，resume/coverage 一律失效；boundary 等價重寫時，resume/coverage 仍可重用；`count` 與 `fast-update` 仍保留原本的 progressive rerun 能力。

**Alternatives considered:**
- **新增 `selection_hash` / `output_hash` 雙軌設計**：語義更細，但對本 repo 的現有 progress schema 來說屬於過度設計。
- **只把 boundary 納入 resume，不納入 coverage**：會讓舊 coverage 在新 boundary 下錯誤跳過應處理貼文。

### D7. 規格與測試都以 property-first 方式描述 boundary 行為

這次 change 會同時修改 `cli-interface`、`filtering-incremental`、`resumable-download` 三個 capability，因此 spec 與 tests 都要用能跨模組驗證的性質來描述，而不是只靠單一 happy path。至少需要覆蓋以下 property：
- **Canonical equivalence**：等價 date/id boundary 輸入產生相同 canonical form、相同 hash、相同輸出集合。
- **Subset monotonicity**：較窄 boundary 的結果集合必定是較寬 boundary 的子集。
- **No side effects for out-of-range posts**：boundary 外貼文不得產生 metadata 或媒體下載副作用。
- **Count monotonicity**：`count=n` 的結果集合是 `count=m (m>n)` 的子集，且 processed_count 永遠不超過 `n`。
- **Pinned does not cut off traversal**：前面出現 out-of-range pinned post 不能阻止之後的 in-range normal post 被抓到。
- **Boundary invalidates progress reuse**：canonical boundary 改變時，既有 resume/coverage 必須被忽略；canonical 等價輸入時必須可重用。
- **Freeze/thaw after boundary skips**：中斷點前若已略過多個 out-of-range 貼文，resume 還是要從下一個未處理的 in-range frontier 繼續。

這些 property 會同時餵給 CLI parsing tests 與 loader behavior tests，確保這不是單純「多兩個旗標」，而是整個下載語義的一致性修正。

**Alternatives considered:**
- **只補單點 scenario tests**：短期可行，但容易漏掉 canonicalization 與 progress compatibility 之間的交叉條件。

## Risks / Trade-offs

- **User timeline 的 `mid` 單調假設依賴來源排序穩定** → 只對 `UserTarget` 啟用 cutoff，並讓 pinned 永遠不能觸發 cutoff；若未來觀察到新的排序異常，再以獨立 change 收縮保證範圍。
- **保留原始時區語義需要調整 `parse_weibo_datetime()` 與 `_cst()`** → 透過集中式 helper 變更而不是在下載主迴圈散落時區特判，降低回歸風險。
- **把 boundary 納入 `options_hash` 會讓既有 progress 在首次使用 boundary 時失效** → 這是必要的一次性重掃成本，換來 resume/coverage 不污染新選取集合的正確性。
- **`count` guard 後移會改動主迴圈流程** → 用 loader regression tests 鎖住 `count`、coverage 與 `fast-update` 的交互，避免新旗標破壞既有 incremental 行為。
- **Pinned 判定依賴原始 payload 欄位** → 由於 `parse_post()` 已完整保留 `raw`，本次可直接依賴 `mblogtype == 2`；若來源欄位未來變化，再由 adapter 層集中調整。

## Migration Plan

1. 先在 CLI 層加入 boundary parsing / canonicalization，並將 canonical 值傳入 `WeiboLoader`。
2. 調整 adapter / datetime helper，讓 `Post.created_at` 可以同時支援原始時區 boundary 與既有 CST 正規化流程。
3. 在 `WeiboLoader` 中插入 boundary-aware filtering / cutoff 流程，並重新排序 `count`、coverage、`fast-update` 的判斷位置。
4. 擴充 `_hash_options()`，把 canonical boundaries 納入 progress compatibility。
5. 更新 `cli-interface`、`filtering-incremental`、`resumable-download` 三份 specs 與對應測試，最後再產出實作 tasks。
6. 若後續要回滾，直接忽略帶有 boundary hash 的 progress 重用，不做混合語義相容。

## Open Questions

None. 這次 change 需要的產品與技術約束都已明確決定，包括 cutoff 適用 target、pinned 行為、date/id canonicalization、單貼文目標語義，以及 boundary 與 progress / count / fast-update 的交互規則。
