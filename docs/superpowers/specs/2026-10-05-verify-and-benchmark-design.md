# `aidoc verify` 與公開 benchmark — 設計文件

- 日期：2026-10-05
- 狀態：已實作（2026-10-12）；benchmark 因 `verify` 校準閘門未通過而未發表（§10）
- 依據：`docs/superpowers/research/2026-10-02-similar-projects.md` §8.2 建議 1（`aidoc verify`，學 pdfmux）與 §8.3「加一張實測表」；§7 差距表「品質稽核」「Benchmark／可信度」兩列。
- 相關：主 spec `2026-09-24-ai-friendly-doc-design.md`（§4 品質檢查、§5 sidecar、§10 測試）、逐頁品質 `2026-10-01-per-page-quality-and-page-map.md`（§5 逐頁模型、§7 對齊抽查、§8 偵測訊號；以下稱「逐頁 spec」）、MCP `2026-10-01-mcp-server-design.md`（§5 tools 共同規則）、契約 `plans/2026-09-24-index.md`、VLM 引擎 `2026-10-05-vlm-ocr-engine-design.md`（benchmark 的一個受測設定）。
- 實作計畫：`docs/superpowers/plans/2026-10-05-verify-and-benchmark.md`

> **一句話：** 把我們已經在用的逐頁品質模型拆成一個**可以對任何 Markdown 輸出打分的獨立工具**（CLI／API／MCP），再用它加上有正解的公開語料，做一份**別人可以重跑、我們敢把輸的項目也列出來**的 benchmark。

## 1. 背景與目標

### 1.1 背景

- 逐頁 spec 之後，Doc4AI Studio 內部已有一套能抓到「漏頁、頁碼錯位、文字層損壞、亂碼、漏表格」的逐頁品質模型（`quality.assess()` + `pagemap.spot_check()` + `probe.broken_fonts()`），並在兩本真書上驗證過（1192 頁骨科書 1192/1192 標記、Head First Android 55/55 壞頁命中、0 誤報）。但它只能評**我們自己轉出來**的文件。
- 調查（研究文件 §7）指出兩個差距：(a) pdfmux 有 `verify` 能稽核**任何**引擎的輸出，我們沒有；(b) anymd／opendataloader／doc7／pdfmux 都有公開 benchmark，我們 0 ★、沒有數字，外界無從判斷「quality fallback」到底值多少。
- 很多團隊的 RAG 庫已經用 MinerU／Docling／MarkItDown／Open WebUI 轉好了，不會為了我們重轉；但他們會想知道「哪幾頁被靜默丟掉了」。這正是 `verify` 的入口。

### 1.2 目標

1. **`aidoc verify`**：給任何 Markdown（有或沒有頁碼標記）＋原始 PDF，產出逐頁報告（JSON／人讀摘要／可選 HTML），指出漏頁、錯位、壞文字層、亂碼、漏表格、頁碼完整度；CLI exit code 可直接進 CI。同一個函式供 API 與 MCP tool 使用。
2. **可重跑的 benchmark**：語料可合法再散佈（合成＋公有領域／CC）、有正解、有腳本一鍵跑完「我們的自動路由 vs 單一引擎」、結果表格式固定，寫進 README 與 `docs/benchmarks/`。涵蓋繁中掃描、壞字型 PDF（Head First 案例的合成重現）、1000+ 頁書、表格、公式。
3. **誠實**：明列不能宣稱的事（§5）。

### 1.3 非目標

- 不做新的「文件解析模型 benchmark」（OmniDocBench、olmOCR-Bench 已經做得很好）；我們量的是**編排層**的價值：自動路由＋品質 fallback＋逐頁修復，相對於直接用單一引擎。
- `verify` 不判斷 OCR 文字是否「正確」——沒有正解時做不到；它判斷的是**結構性損失**（頁掉了、頁錯了、文字層壞了、表格不見了）。
- 不比較付費雲端解析服務（Reducto、LlamaParse…）。
- 不做 reading order 指標（v1）。

## 2. `aidoc verify`

### 2.1 輸入

```
aidoc verify <markdown.md> --pdf <source.pdf> [選項]
aidoc verify <out/<stem>/>                 # 我們自己的輸出目錄：Markdown + sidecar + 來源（原始檔或工作副本）
```

| 選項 | 預設 | 說明 |
|---|---|---|
| `--pdf PATH` | （目錄形式時從 sidecar／資料庫找） | 來源 PDF。沒有 PDF 時只能做「無 PDF 檢查」（§2.3 表的最後一欄） |
| `--format auto\|aidoc\|mineru\|docling\|markitdown\|pdfmux\|none` | `auto` | 頁碼標記的方言（§2.2）。`none` = 忽略任何標記、一律用對齊推頁 |
| `--page-marker-regex REGEX` | — | 自訂標記，需含一個群組抓頁碼（1 起算） |
| `--pages N` | 由 PDF 讀 | 沒有 PDF 時可手動給總頁數（只為覆蓋率） |
| `--json PATH` / `--html PATH` | — | 寫報告。`--json -` 寫到 stdout |
| `--fail-on warn\|low` | `low` | exit code 規則（§2.5） |
| `--sample N` | `30` | 對齊抽查頁數；`0` = 全頁 |
| `--thumbnails` | 關 | HTML 報告為被標記頁附縮圖（需 PDF） |
| `--lang cht\|en` | `cht` | 只影響 HTML／摘要的文字 |

- Markdown 編碼一律 UTF-8（BOM 可）；CRLF 正規化成 LF 後再處理，報告記 `input.newline`。
- `verify` **不改任何輸入檔**，也不寫資料庫。目錄形式會讀 sidecar（`<stem>.json`）取得 `pages`、`engine`、`quality`，並把舊 `quality` 一併放進報告的 `previous` 供對照。

### 2.2 頁碼標記方言與「沒有標記時怎麼對齊」

| `--format` | 標記 | 來源 |
|---|---|---|
| `aidoc` | `<!-- page: N -->`（每頁一個，由構造保證） | 我們的輸出（逐頁 spec §6） |
| `mineru` | MinerU `markdown.md` **沒有**頁標記；`ParseResult.markdown(add_markers=True)`（FULL 模式）頁間以 `---` 分隔但不帶頁碼 → 以「第 k 個 `---` 區段 = 第 k 頁」推頁（**只有在區段數 == 頁數時採用**，否則視同無標記） | 逐頁 spec §3.1 |
| `docling` | `export_to_markdown(page_break_placeholder=X)` 的固定字串（預設 `<!-- page break -->`，可由 `--page-marker-regex` 覆寫）；已知連續空白頁會漏（docling issue #2623）→ 區段數 ≤ 頁數時以對齊修正 | 主 spec §3 |
| `markitdown` | PDF 無標記；pptx `<!-- Slide number: N -->`（v1 只處理 PDF，pptx 標記僅記錄不評） | 主 spec §3 |
| `pdfmux` | `<!-- page N -->` 風格（實作時以其 README 為準，放進 `markers.py` 的方言表；研究文件 A2） | 研究文件 |
| `auto` | 依序嘗試上列正規表達式，第一個在文件中出現 ≥ 2 次者；都沒有 → `none` | — |

**沒有標記時的頁界推斷（`pagemap.infer_pages()`）**：這是 `verify` 新增的唯一演算法，其餘全部重用逐頁 spec 的實作。

```
layers[i] = squash(PDF 第 i 頁文字層)；whole = "\0".join(layers)
T         = squash(整份 Markdown)，並保留 squash 前→後的位置對照表（list of (orig_pos, squashed_pos)）
每頁探針  = 逐頁 spec §7 的方法：Random(seed=頁碼)、20%–80% 區間、長 24、在 whole 中唯一；每頁最多 3 個
定位      = 探針在 T 中恰好出現一次 → 該頁有一個「錨點」（探針位置）；取每頁錨點的中位數位置
頁序檢查  = 錨點位置必須隨頁碼單調遞增；違反者丟掉（最長遞增子序列），丟掉的頁記為 undecidable
邊界      = 相鄰兩頁錨點中點 → 回推到 squash 前的原文位置，再退到最近的段落邊界（空白行）
結果      = inferred_page_map：{page: (start, end)}，method = "inferred"，confidence = 有錨點頁數 ÷ 可判定頁數
```

- 掃描 PDF 沒有文字層、Markdown 又沒有標記 → **無法推頁**：報告 `page_map = null`、`verifiable = false`，exit code 3（§2.5）。這是誠實的結果，不是失敗。
- 推斷出來的頁界只用來**定位問題頁**（壞文字層、亂碼、空頁），`coverage` 對推斷頁界無意義，改報 `anchored_pages / decidable_pages`。有標記時的 `coverage` 定義不變。
- 探針唯一性要求與 §7 相同，所以程式碼清單、running header 這類跨頁重複文字不會造成錯錨。

### 2.3 檢查項目

全部重用 `quality.py`／`pagemap.py`／`probe.py` 的現有函式；新增的三個頁層級**建議（advisory）**訊號不影響等級，只列出：

| 檢查 | 等級影響 | 來源函式 | 需要 PDF | 無標記時 |
|---|---|---|---|---|
| `page_map_incomplete`（覆蓋率 < 0.95）、`page_map_invalid`（標記非遞增／越界） | 文件 → `low` | `pagemap.page_map()`、`invalid_markers()` | 否（需頁數） | 不評（改報 anchored） |
| `page_map_misaligned`（§7 抽查） | 文件 → `low` | `pagemap.spot_check()` | 是 | 不評（推頁本身就是對齊） |
| `broken_text_layer`（壞字型閘門 + `�`／garbage／位移 29） | 頁 → `warn` | `probe.broken_fonts()` + `quality.flag_pages()` | 是（字型表） | 可（用推斷頁界） |
| `garbage`（頁內 garbage_ratio > 0.05） | 頁 → `warn` | `quality.flag_pages()` | 否 | 可 |
| `chars_per_page`、`garbage_ratio`、`missing_table`（整份） | 文件 → `low` | `quality._assess_base()` | `missing_table` 需 PDF | 可 |
| **新** `page_dropped`：PDF 該頁文字層 ≥ 120 字（squash 後）但該頁區段 < 20 字 | 頁 → `warn` | `verify.py` | 是 | 可（推斷頁界下為「該頁沒有任何錨點且相鄰頁錨點相接」） |
| **新（advisory）** `table_missing_on_page`：該頁 `_ruled_grid` 為真但區段沒有 GFM／HTML 表格 | 不影響 | `probe._analyse_page()` 逐頁版 | 是 | 可 |
| **新（advisory）** `formula_missing_on_page`：該頁 math 字型／符號密度達門檻但區段沒有 `$` | 不影響 | 同上 | 是 | 可 |
| **新（advisory）** `images_missing`：`<!-- image missing -->` 計數（Docling 部分失敗） | 不影響 | 既有 metrics | 否 | 可 |

- 等級規則與逐頁 spec §5.2 完全相同（文件理由 → `low`；否則有未修復頁 → `warn`；否則 `ok`），分數公式相同。第三方輸出沒有 `repaired_by`，所以所有被標記頁都算未修復。
- `page_dropped` 是 pdfmux 「找出被靜默丟掉的頁」的對應物；門檻（120／20）沿用逐頁 spec §7 的候選頁門檻，實作時以 benchmark 語料的「刻意刪頁」案例校準。
- 無 PDF 時能做的只有：標記覆蓋率（需 `--pages`）、標記完整性、整份亂碼、逐頁亂碼。報告 `verifiable = "partial"`。

### 2.4 報告格式

**JSON（`verify_version = 1`）**

```json
{
  "verify_version": 1, "aidoc_version": "0.1.0", "generated_at": "2026-10-05T10:00:00+08:00",
  "input": {"markdown": "raw/mineru/book.md", "sha256": "…", "bytes": 1203456, "newline": "lf",
            "format_detected": "none", "markers_found": 0},
  "pdf": {"path": "book.pdf", "sha256": "…", "pages": 1192, "text_layer_pages": 1180,
          "broken_fonts": ["ComicSansMS"], "broken_font_pages_count": 339, "blank_pages": [3, 44]},
  "verifiable": true,
  "page_map": {"method": "inferred", "expected": 1192, "found": null, "coverage": null,
               "anchored": 1101, "decidable": 1150, "confidence": 0.957, "missing": [], "invalid_markers": 0,
               "alignment": null},
  "summary": {"level": "warn", "score": 0.91, "reasons": [],
              "pages_flagged": 57, "pages_unrepaired": 57, "advisories": 12},
  "metrics": {"chars": 2601234, "chars_per_page": 2182.6, "garbage_ratio": 0.0009, "has_table": true,
              "images_missing": 0},
  "pages": [
    {"page": 1, "reasons": ["broken_text_layer"], "metrics": {"fffd_between": 24, "garbage": 0.12, "shift_hits": 12}},
    {"page": 212, "reasons": ["page_dropped"], "metrics": {"layer_chars": 1890, "section_chars": 0}},
    {"page": 305, "advisories": ["table_missing_on_page"]}
  ],
  "previous": {"engine": "mineru", "quality": {"level": "ok", "score": 1.0}},
  "timing_s": {"probe": 2.4, "layers": 7.3, "infer": 0.9, "assess": 0.6, "total": 11.2}
}
```

- `pages` 只列有理由或建議的頁（同 sidecar 慣例）；`missing` 最多 200 筆＋`missing_truncated`。
- 欄位名與 sidecar `quality` 對齊（`level`／`score`／`reasons`／`pages`／`pages_flagged`／`pages_unrepaired`／`page_map`），所以 Library 既有的 `doc_flags()` 可以直接吃 `summary + page_map`。
- `--format` 偵測到的方言寫在 `input.format_detected`；使用者強制指定時加 `format_forced: true`。

**人讀摘要（stdout，預設）**

```
book.md ← book.pdf (1192 pages, mineru output, no page markers → inferred, confidence 0.96)
level: warn   score: 0.91
  pages flagged: 57   (broken_text_layer 55, page_dropped 2)
  advisories:    12   (table_missing_on_page 9, formula_missing_on_page 3)
  worst pages:   1, 16, 17, 25, 31, 61, 108, 212, 640 …
run with --json report.json for the full list; --html report.html for a page table
```

**HTML 報告（`--html`）**：單一檔案、無外部資源（不載 CDN，離線可開、可附在 PR）。內容：標頭（檔名、頁數、等級、分數）、理由與建議計數、**逐頁表格**（頁碼、理由、關鍵指標、`--thumbnails` 時附 PyMuPDF 渲染的 160 px 寬縮圖 data URI；只對被標記頁產生，最多 200 頁）、推頁信心、產生時間與版本。深淺色各一套 CSS 變數。

### 2.5 Exit code（CI 用）

| code | 意義 |
|---|---|
| `0` | 等級達標（預設 `--fail-on low`：`ok`／`warn` 都是 0；`--fail-on warn`：只有 `ok` 是 0） |
| `1` | 等級未達標 |
| `3` | 無法驗證（掃描檔無文字層且無標記；或無 PDF 又無 `--pages`）。`--allow-unverifiable` 時改回 0 |
| `4` | 用法／輸入錯誤（檔案不存在、PDF 損毀或加密、regex 無群組） |

JSON／HTML 在 0、1、3 都會寫出；4 不寫。

### 2.6 API 與 MCP

| 介面 | 形式 | 說明 |
|---|---|---|
| `POST /api/verify` | multipart：`markdown`（檔案）＋ `pdf`（檔案，可省）＋ 表單欄位 `format`、`sample`、`pages` | 回 §2.4 JSON；大小限制同 `upload_max_bytes`；在 request 內同步執行（1192 頁 ≈ 11 s，可接受；> 60 s 的輸入請用 CLI） |
| `POST /api/verify` | JSON：`{"doc_id": "…"}` | 稽核 Library 既有文件（Markdown + 原始檔或工作副本）；等同 `reassess` 但**回傳完整報告且不寫資料庫** |
| `POST /api/verify` | JSON：`{"markdown_path": "…", "pdf_path": "…"}` | 本機路徑：只接受 loopback 或 server token（與 `POST /jobs` 的本機路徑同規則） |
| MCP tool `verify_document` | `{doc_id}` | scope `doc4ai:read`、`read_only`；回 `VerifyOut`（summary、page_map、最多 50 筆 pages、hint），受 `response_token_budget` 裁切 |
| MCP tool `verify_markdown` | `{doc_id, markdown_base64}` 或 `{pdf_base64, markdown_base64}` | scope `doc4ai:convert`（它會消耗 CPU 並接受上傳）；上傳上限 `mcp.max_upload_mb`；用途：agent 拿別的工具轉出來的 Markdown 來對照 Library 裡同一份 PDF |

共用函式：`aidoc.verify.verify_markdown(md: str, pdf: Path | None, *, pages: int | None, fmt: str, marker_regex: str | None, sample: int) -> VerifyReport`。純函式、不碰 store、不寫檔；CLI／API／MCP 只是三層薄殼。`VerifyReport.to_json()`／`.summary_text()`／`.to_html()`。

### 2.7 限制（寫進 `--help` 與 README）

- 沒有正解時 `verify` 看不出 OCR 認錯字、VLM 幻覺、漏掉圖中文字。它抓的是結構性損失。
- 掃描 PDF ＋ 無標記 → 無法驗證（exit 3）。
- `page_dropped` 對「整頁是圖、引擎合理地只輸出圖說」會誤報；因此是頁層級 `warn`、不是 `low`，且 HTML 報告會附縮圖讓人一眼判斷。
- 表格／公式 advisory 用的是 probe 的格線／數學字型啟發式（主 spec §4 已註明誤判率），所以只做建議。
- 推頁需要文字層與 Markdown 共享足夠多的唯一 24 字串；表格為主、字很少的頁會 undecidable。

## 3. 公開 benchmark

### 3.1 設計原則

1. **量編排層，不量模型。** 受測設定是「Doc4AI 自動路由（含 fallback 與逐頁修復）」對上「單一引擎、我們出廠的預設設定」。我們不調各引擎的最佳參數，也不宣稱任何引擎本身的好壞（§5）。
2. **三層語料，誠實標示。** Tier A：合成、進版控、有逐字正解；Tier B：公有領域／開放授權的真實文件，由腳本依 sha256 下載、進版控的只有清單；Tier C：私有真書（骨科書、Head First），結果**分開列、標明不可重現**。
3. **正解分級。** L1 逐字（合成）、L2 來源文字（Gutenberg 文本渲染成 PDF，文字即正解）、L3 結構（只有頁數、表格數、公式數、刻意製造的缺陷清單）。每個數字旁都寫它用的是哪一級。
4. **可重跑。** 一個指令、固定種子、釘死版本（引擎 `uv.lock`、模型 sha256、語料 sha256）、輸出 JSON 與 Markdown 表；跑一次就能看出與上次的差異。
5. **`verify` 自我驗證。** 用 L1／L2 正解檢驗 `aidoc verify` 的分數是否與真實損失相關（§3.4 最後一列）；相關性不夠就先修 `verify`，不發表。

### 3.2 語料設計

#### Tier A — 合成（`bench/corpus/make_synthetic.py`；進版控的是**腳本與正解**，PDF 由腳本在幾分鐘內重生；小檔也可直接進版控，上限 20 MB 總量）

| 名稱 | 內容 | 針對 | 正解 |
|---|---|---|---|
| `syn-text-zh` ×5 | 30 頁 A4，Gutenberg 繁中古典小說段落（紅樓夢 #24264、三國演義 #23950、西遊記 #23962、水滸傳 #23863；PG 檔頭去除後為公有領域文字）排版，Noto Sans CJK TC（SIL OFL，腳本下載），含標題階層、running header、頁碼、1 空白頁、跨頁段落 | 數位繁中、頁界、對齊 | L1 逐頁文字 |
| `syn-text-en` ×3 | 同上，英文 Gutenberg（公有領域） | 數位英文 | L1 |
| `syn-scan-zh` ×5 | `syn-text-zh` 渲染 200 dpi → Augraphy／Pillow 退化（雜訊、±1.5° 歪斜、JPEG q=55、墨水暈、折痕各一種）→ 影像 PDF（無文字層） | **繁中掃描**（填補 OmniDocBench v1.5 只有 13 頁 `traditional_chinese` 的缺口） | L1 |
| `syn-scan-mixed` ×3 | 中英混排（技術段落 + 英文術語、程式碼） | 混排掃描 | L1 |
| `syn-broken-tounicode` ×4 | 以 PyMuPDF 嵌入 Type0／Identity-H 字型後 `doc.xref_set_key(font_xref, "ToUnicode", "null")` 刪除 CMap（既有 `make_fixtures.make_broken_tounicode_pdf()` 的做法；PyMuPDF ≥ 1.19.4 對無斜線的 key 會整個移除）；變體：(a) 全書 10% 頁用壞字型（Head First 10.3% 的重現）、(b) 30% 頁（> 20% → 應整份換引擎）、(c) 只有頁尾用壞字型（對照，不應被標）、(d) 壞字型是 CJK | 壞文字層偵測 + 修復 | L1 + 缺陷清單（哪些頁壞） |
| `syn-tables` ×4 | 每頁 1–3 個格線表格（3–8 欄、含合併標題列、含中文），`_draw_table` 升級版；跨頁表格 1 個 | 表格 | L1 + 表格 cell 矩陣（JSON） |
| `syn-formula` ×2 | 行內與行間公式（LaTeX 來源 → matplotlib mathtext 渲染貼進頁面） | 公式 | L1 + LaTeX 字串 |
| `syn-book-1200` ×1 | 1,200 頁：Gutenberg 繁中四部合輯 + 目錄 + 章首 + 20 個空白頁 + 跨段表格 2 個，約 60 MB（**不進版控**，腳本重生 ≈ 2 分鐘） | 1000+ 頁、分段、續跑、跨段表格 | L1（頁文字）+ 結構 |
| `syn-twocol` ×2 | 雙欄英文 + 圖說 | 雙欄（只量對齊，不量順序） | L1 |
| `syn-dropped-pages` ×3 | 從 `syn-text-zh` 的正確 Markdown 刻意刪 5 頁、把標記整體 +1、把兩頁對調 → **`verify` 的負樣本**（不是轉換受測樣本） | `verify` 校準 | 缺陷清單 |

#### Tier B — 下載（`bench/corpus/fetch.py` + `MANIFEST.json`：來源 URL、授權與授權 URL、抓取日、sha256、頁數、用途；缺檔或 sha 不符即該樣本 skip 並在報告標 `missing`）

| 來源 | 授權（查證 2026-10-05） | 用途 | 數量 |
|---|---|---|---|
| 行政院公報（data.gov.tw dataset 5959，PDF） | 政府資料開放授權條款－第 1 版（第 4.2 條明示與 CC BY 4.0 相容、可再散佈、需標示來源；排除第三方著作與個資） | 真實繁中數位 PDF：表格多 | 6 份、各 ≤ 40 頁 |
| 立法院公報（ppg.ly.gov.tw；授權頁 data.ly.gov.tw/principle.action） | 立法院開放資料授權（OGDL-1 同款） | 真實繁中、雙欄、表格 | 4 份 |
| arXiv 論文，**只取 OAI-PMH `arXiv` metadata `<license>` 為 CC BY 4.0／CC BY-SA 4.0／CC0** 者 | 各該 CC 授權 | 公式、雙欄、英文 | 6 份（含 3 份數學） |
| NIST 出版品（nist.gov/open/license） | 美國政府著作，美國境內不受著作權保護（17 U.S.C. §105；他國權利保留，報告註明） | 表格、數據報告 | 4 份 |
| Internet Archive 公有領域繁中掃描書（逐項確認 rights 欄位為 public domain 才收） | 公有領域 | **真實繁中掃描**（Tier A 合成掃描的對照） | 2 份、各取 40 頁 |
| OmniDocBench v1.6 的 `traditional_chinese` 頁（13 頁）與 olmOCR-Bench 子集 | OmniDocBench：研究用、非商業、**無再散佈授權**；olmOCR-Bench：ODC-BY-1.0 但含授權不一的 arXiv 檔 | 與外界 benchmark 的接點（只做 sanity，不進我們的總表） | 由腳本從 HF 下載到本機、絕不進版控 |

- 不收：ctext.org 掃描（禁止自動下載、無再散佈授權）、CBETA（CC BY-NC-SA；NC 與下游商業使用不合）、arXiv 預設 non-exclusive 授權、任何含個資的政府文件頁（抓取時人工看一次、有個資的頁以 PyMuPDF 刪除並記錄）。
- 字型：合成語料一律用 Noto Sans CJK TC／Noto Serif CJK TC（SIL OFL 1.1，可嵌入與再散佈）；**不用 `msjh.ttc`**（現有 fixtures 用它，但那些 fixtures 不以 benchmark 語料身分散佈；benchmark 腳本找不到 Noto 就下載，不退回系統字型）。
- 目前沒有任何公開的繁中 PDF→Markdown benchmark（查證：esun TC-STR、lianghsun/tw-OCR、ZihCiLin 合成集都是文字行／場景文字層級）；Tier A 繁中合成 + Tier B OGDL 公報是真正的新貢獻，README 可以這樣寫，但只能這樣寫。

#### Tier C — 私有（`tests/fixtures/manual/` 既有清單 + 兩本全書）

骨科書（1192 頁）、Head First Android（532 頁）、`hfad_cover_p1`、`hfad_garbled_mix`。結果放在報告的獨立區塊，標題明寫「不可重現：樣本受著作權保護，未隨專案散佈」。

### 3.3 正解格式

`bench/corpus/<name>/gt.json`：

```json
{"name": "syn-scan-zh-03", "tier": "A", "gt_level": "L1", "pages": 30, "lang": "cht", "kind": "scan",
 "license": "Public domain text (Project Gutenberg #24264, PG header stripped) + OFL fonts; generated by bench/corpus/make_synthetic.py",
 "page_text": {"1": "……", "2": "……"},
 "blank_pages": [17],
 "tables": {"12": [{"cells": [["欄一", "欄二"], ["甲", "乙"]], "n_cols": 2, "spans_pages": false}]},
 "formulas": {"5": ["\\sum_{i=1}^{n} x_i"]},
 "defects": {"broken_text_layer_pages": [3, 9, 21], "dropped_pages": [], "marker_shift": 0},
 "generator": {"script": "make_synthetic.py", "version": 1, "seed": 24264003, "fonts": ["NotoSansCJKtc-Regular.otf"], "augraphy": "1.x"}}
```

### 3.4 指標

| 指標 | 定義 | 適用正解 | 實作 |
|---|---|---|---|
| **頁文字 NED** | 每頁 `1 − Levenshtein.normalized_distance(squash(gt), squash(pred))`，再取平均；`squash` 同逐頁 spec §7（NFKC、去斷字、小寫、只留字母數字）；缺頁的 pred 視為空字串（NED = 0） | L1／L2 | `rapidfuzz`（MIT） |
| **CER（繁中）** | 字元錯誤率 = 編輯距離 ÷ 正解字元數，只對 `lang=cht` 樣本另列 | L1／L2 | 同上 |
| **頁覆蓋率** | `page_map.coverage`（有標記）；無標記的第三方輸出用 `verify` 推頁的 `anchored / decidable` | 全部 | `pagemap` |
| **對齊率** | `spot_check` 全頁（`--sample 0`） | 有文字層者 | `pagemap` |
| **表格 TEDS** | 結構 + 內容 TEDS 與 TEDS-S（PubTabNet 定義）；pred 的 GFM／HTML 轉成 HTML 後比；該頁沒有表格 → 0 | L1 表格 | `table_recognition_metric`（授權待確認；不行就以 `apted` 自實作，進 `bench/metrics.py`） |
| **公式** | 正解 LaTeX 與 pred `$…$` 內容的 NED（去空白、正規化 `\left` `\right`）；**不用 CDM**（需 Node + texlive + ImageMagick；可選、Docker 才跑） | L1 公式 | `rapidfuzz` |
| **壞頁修復率** | `defects.broken_text_layer_pages` 中 pred 頁 NED ≥ 0.90 的比例 | 缺陷清單 | — |
| **漏頁率** | 正解非空白頁中 pred 區段 < 20 字的比例 | L1／L2 | — |
| **時間／頁、峰值顯存** | 牆鐘（含 runner 啟動與模型載入，另列不含）；`nvidia-smi --query-gpu=memory.used` 每 0.5 s 抽樣 | 全部 | `bench/run.py` |
| **費用（遠端 VLM）** | sidecar `vlm.usage` tokens × 價目表（價目與日期寫在報告） | VLM 設定 | — |
| **verify 相關性** | 每份文件 `verify` 的 `score` 與 GT 平均 NED 的 Spearman ρ；`verify` 標記頁 vs GT 缺陷頁的 precision／recall | L1 | 自實作 rank correlation（不裝 scipy） |

- 所有指標都在**正規化後**比較，不比 Markdown 語法；標題符號、表格管線、圖片連結、HTML 註解一律由 `quality.strip_markup()` 去掉。
- 不加總成單一「總分」。每個類別一欄，讀者自己看。

**校準閘門重新定義（2026-10-20，使用者核准；在任何重跑之前寫入）**——取代 §7 #9 的舊定義：

1. 只在 `verify` 能判斷的列與頁上計算：有文字層的列（排除 `verifiable` 不為 `true` 的列與掃描檔），頁則排除沒有文字層的頁（PDF 文字層 < 20 個 squash 字元，`verify.TEXT_LAYER_MIN`）。
2. ρ（verify 分數 vs GT 平均 NED，Spearman）≥ 0.6 不變。
3. 頁級 recall ≥ 0.9 **且** precision ≥ 0.9；被標頁 = 每一個頁層級理由（`page_dropped`、`page_misaligned`、`page_oversized`、`broken_text_layer`、`garbage`、缺標記、失敗頁），**不含**提示性質的 `page_partial`；壞頁 = GT 頁 NED < 0.5 或 GT 缺陷頁（未修好者），與舊定義相同。
4. 掃描檔另列報告，不參與閘門。
5. 在沒有用來調整的資料上驗證：全新種子（從未產生過）的 Tier A，加上 Tier B。Tier B 只有 L3 正解（沒有逐頁文字），無法算 ρ 與頁級 P／R，只列旗標數供參考。
6. 只跑一次、照實回報；看到結果後不再調整任何東西。`syn-dropped-pages` 的四個負樣本照舊報告並仍須通過。

### 3.5 Runner（`bench/run.py`，不屬於 `aidoc` 套件、不進 wheel）

```
uv run python bench/run.py --corpus A,B[,C] --configs auto,auto-norepair,docling,mineru,markitdown[,vlm] --out bench/results/<date>-<host>/
```

- **受測設定**：`auto`（`aidoc convert`，自動路由 + 修復）、`auto-norepair`（自動路由、關修復：拆解「路由」與「修復」各貢獻多少）、`docling`／`mineru`／`markitdown`／`vlm`（`--engine X`，無 fallback、**修復關閉**：`AIDOC_BENCH_NO_REPAIR=1`，否則單引擎設定會借到其他引擎的修復，比較失去意義；報告明寫）。`vlm` 只在 `backend != "none"` 且 `.ready` 時出現，否則該欄為 `—`（**依賴 VLM spec**）。
- 每個設定一個獨立輸出根目錄 `bench/results/<run>/out-<config>/`，用 `--force`；資料庫用獨立的 `AIDOC_DATA=bench/results/<run>/data-<config>`，不污染使用者的 Library（`AIDOC_DATA` 只改 `data/`；模型目錄以 `AIDOC_MODELS` 指回原處，不重下載）。
- 順序：先暖機（每個引擎轉一份 3 頁樣本、不計時）→ 依語料順序逐份轉換 → 轉完一份立刻算指標並 append 到 `results.jsonl`（中斷可續：已有結果的 (config, sample) 跳過）→ 全部完成後 `report.py`。
- 計時只量 `aidoc convert` 的牆鐘；GPU 抽樣在背景執行緒；n=1（§5 明寫），`--repeat N` 可選。
- 環境紀錄 `env.json`：OS、GPU、驅動、CUDA、各 env 的 `uv.lock` sha256、MinerU／Docling／MarkItDown／llama.cpp／GGUF 版本、`aidoc` commit、`aidoc.toml` 的引擎相關鍵、`prompt_version`（VLM）、日期。
- 失敗（`failed`／逾時）記 `status` 並以 NED 0 計入（不是略過：略過會讓分數虛高）。
- 第三方原生輸出（`raw-mineru`、`raw-docling`、`raw-markitdown`：直接呼叫各引擎 API、不經我們的 runner 與 normalize）作為**選用設定**，用來證明 `verify` 能吃第三方格式；v1 只在 `--configs raw-*` 時跑。

GPU 機器（RTX 5070 Ti）的時間預估：Tier A（不含 1200 頁書）≈ 150 頁 × 5 設定 ≈ 30 分鐘；`syn-book-1200` 每設定 15–40 分鐘（MinerU 最慢），只跑 `auto` 與 `mineru`；Tier B ≈ 300 頁 × 5 ≈ 1.5 小時；Tier C 兩本全書只跑 `auto`（已有紀錄：739 s、3766 s）。

### 3.6 結果表格式與發表位置

- `bench/results/<run>/summary.md`（`report.py` 產生）→ 複製到 `docs/benchmarks/<date>.md`（進版控，附 `env.json`、`results.jsonl`）；README 加一節「實測」放**最新一份的精簡表**與連結，表下固定附 §5 的「不宣稱」段。
- 表格（每個 Tier 一張；數字 = 該類別所有樣本的平均，括號內為最差樣本）：

| 類別（樣本數，正解等級） | 指標 | auto | auto-norepair | docling | mineru | markitdown | vlm |
|---|---|---|---|---|---|---|---|
| 繁中數位 ×5（L1） | NED ↑ / 覆蓋 / 對齊 | 0.98 (0.96) / 1.00 / 0.99 | … | … | … | … | — |
| 繁中掃描 ×5（L1） | NED / CER ↓ | | | | | | |
| 壞字型 10% ×2（L1+缺陷） | NED / 修復率 ↑ / 漏頁率 ↓ | | | | | | |
| 壞字型 30% ×1 | 最終引擎 / NED | | | | | | |
| 表格 ×4（L1） | TEDS ↑ / TEDS-S | | | | | | |
| 公式 ×2（L1） | 公式 NED | | | | | | |
| 1200 頁 ×1（L1） | NED / 覆蓋 / 時間 / 峰值顯存 | | | | | | |
| 時間／頁（全部） | 秒 ↓ | | | | | | |

- 第二張表：**`verify` 校準**——`syn-dropped-pages` 三個缺陷各一列：`verify` 是否判 `low`／標出哪些頁、exit code；以及 Spearman ρ 與頁級 P/R。
- 第三張表（Tier C，標「不可重現」）：兩本真書的 `auto` 結果（覆蓋、對齊、修復頁數、時間）。
- 每張表上方一行環境摘要（GPU、驅動、引擎版本、日期、commit），下方 §5 固定文字。

## 4. 相依與順序

- `verify`（§2）只依賴既有模組，可立即實作。
- benchmark 的 `vlm` 欄依賴 `2026-10-05-vlm-ocr-engine-design.md` 完成到「`--engine vlm` 可用」；runner 以 `.ready` 存在與否決定是否出現該欄，所以兩份可以並行開發、VLM 晚到也不擋發表（表格該欄為 `—` 並註明）。
- Tier B 的下載清單在實作時逐份人工確認授權頁面（抓取腳本把授權 URL 與抓取日寫進 MANIFEST；沒有授權欄的來源不收）。

## 5. 不宣稱的事（固定文字，隨表格一起出現）

1. **不是 OmniDocBench／olmOCR-Bench 的分數，不可與其他專案發表的數字比較**：語料、正規化、指標定義都不同。
2. **合成掃描不等於真實掃描**：Augraphy 退化是我們選的；真實掃描的對照只有 Tier B 兩本公有領域書與不可重現的 Tier C。
3. **單引擎欄位是「我們出廠設定下的該引擎」**，不是該引擎的最佳表現；MinerU 用 `basic` tier（主 spec §13.1）、Docling 用 RapidOCR、MarkItDown 無 OCR；各引擎在其他設定下可能更好。
4. **這量的是編排層**：`auto` 比單引擎好的部分來自「換引擎 + 逐頁修復」，不代表我們的 OCR 更強——我們沒有自己的 OCR。
5. **時間 n=1**，含模型載入；不同機器不可比；只看相對順序。
6. **`verify` 分數不是品質的絕對量**：它與正解 NED 的相關性寫在校準表裡；相關性只在合成語料上量過。
7. **VLM 欄（若有）的幻覺未被 NED 以外的指標量化**；遠端 VLM 的費用是以當日價目換算的估計。
8. **Tier C 不可重現**，列出只為說明兩本真書的實際結果，與公開數字分開看。
9. 任何「第一」「最強」「SOTA」字眼都不出現在 README 與文件中。

## 6. 測試

- **單元（`tests/unit/test_verify*.py`、`test_markers.py`、`test_infer_pages.py`）**：方言偵測（五種 + 自訂 regex + `none`）；推頁在 `text.pdf`／`paged_furniture.pdf` 上以「去掉標記的我們自己的輸出」為輸入，推出的頁界與原標記位置誤差 ≤ 1 段落、`confidence ≥ 0.9`；負樣本（標記 +1、刪 2 頁、對調）各自產生預期的 reasons 與 exit code；`page_dropped` 在「整頁圖＋圖說」的合成頁不誤報為 `low`（只 `warn`）；JSON schema 鍵齊全且與 sidecar `quality` 鍵相容（`doc_flags()` 能吃）；HTML 無外部 URL、含每個被標記頁的列；CLI exit code 四種；`--pages` 無 PDF 路徑；CRLF 輸入。
- **第三方格式（`tests/unit/test_verify_formats.py`）**：以 `tests/fixtures/mineru_recorded/furniture8/markdown.md`（真實 MinerU 原生輸出，無標記）對 `paged_furniture.pdf` → 推頁成功、8 頁中 ≥ 6 頁 anchored、無 `low`；Docling 原生 `export_to_markdown(page_break_placeholder=…)` 與 MarkItDown 原生輸出由慢測試錄製到 `tests/fixtures/docling_recorded/`、`markitdown_recorded/`（小，進版控）後同樣斷言。
- **API（`tests/api/test_verify.py`）**：三種 body 形狀；本機路徑的 loopback／token 規則；大小限制 413；`doc_id` 不存在 404；回應與 CLI JSON 相同。
- **MCP（`tests/mcp/test_tool_verify.py`）**：scope 檢查、budget 裁切（1192 頁報告 ≤ 8,000 tokens）、`verify_markdown` 的 base64 上限。
- **benchmark 管線（不需 GPU）**：`make_synthetic.py --small` 產生每類 1 份、各 3 頁的迷你語料並比對 `gt.json` 的 sha（決定性）；`fetch.py --check` 只驗 MANIFEST 格式與授權欄位必填；`metrics.py` 以手算小例（NED、TEDS 3×3 表、公式正規化）為正解；`run.py --configs auto --corpus A-small --fake`（`AIDOC_FAKE_ENGINES=1`）走完整個流程產出 `results.jsonl`、`summary.md`、`env.json`；`report.py` 對固定 `results.jsonl` 產出與 `tests/fixtures/bench_expected_summary.md` 逐字相同的表。
- **慢測試（`@slow`）**：真實三引擎對 Tier A 迷你語料各轉一份、`verify` 判 `ok`／`warn`；錄製第三方原生輸出 fixtures。
- **驗收模式**：`AIDOC_REQUIRE_MANUAL=1` 時 Tier C 樣本缺檔 → fail（沿用 `tests/manual.py`）。

## 7. 驗收標準

| # | 項目 | 標準 |
|---|---|---|
| 1 | `verify` 吃我們自己的輸出 | 對 Library 中兩本真書的現有輸出，`verify` 的 `summary` 與 sidecar `quality` 的 `level`／`reasons`／`pages_unrepaired` 一致（允許 `score` 差 ≤ 0.01） |
| 2 | `verify` 吃第三方輸出 | MinerU 原生 `markdown.md`（無標記）對 `paged_furniture.pdf` 推頁 ≥ 6/8；對骨科書 1192 頁 MinerU 原生輸出推頁 `confidence ≥ 0.9`、耗時 ≤ 30 s |
| 3 | 缺陷偵測 | `syn-dropped-pages` 三種缺陷：刪頁 → 全部被 `page_dropped` 標出且 exit 1（`--fail-on warn`）；標記 +1 → `page_map_misaligned` + exit 1；對調 → `page_map_invalid` 或 `misaligned` + exit 1；無缺陷的對照 → exit 0 |
| 4 | 無法驗證 | 掃描 PDF + 無標記 → exit 3、JSON `verifiable: false`，不是 traceback |
| 5 | 報告 | JSON 通過 schema 測試；HTML 離線可開、無外部請求；人讀摘要 ≤ 12 行 |
| 6 | API／MCP | 三種 body 與兩個 tool 的測試全綠；MCP 回應 ≤ budget |
| 7 | 語料 | `make_synthetic.py` 在乾淨機器（只有 Python + uv + 網路）跑完 Tier A ≤ 10 分鐘，sha 與版控的 `gt.json` 一致；`fetch.py` 的 MANIFEST 每筆有授權 URL 與 sha256；repo 內不含任何非 OFL 字型、非公有領域／OGDL／CC BY 文件 |
| 8 | 跑一次 | GPU 機上 `run.py --corpus A,B --configs auto,auto-norepair,docling,mineru,markitdown` 一次跑完、產出 `docs/benchmarks/<date>.md`；README 實測節更新；§5 文字出現在表下 |
| 9 | `verify` 校準 | Tier A 上 Spearman ρ ≥ 0.6、頁級缺陷 recall ≥ 0.9（precision 另列不設門檻）；不達標則 `verify` 不發表、先修 |
| 10 | 回歸 | `uv run pytest -q`、`pnpm --dir web test` 全綠；`uv run pytest -m slow -q` 在 GPU 機全綠 |

## 8. 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| 推頁對字少／表格頁 undecidable 比例高 | 第三方輸出的頁級報告有洞 | 報告明列 `decidable`／`anchored`；undecidable 頁不算錯、不算對；HTML 顯示為灰色 |
| `page_dropped` 誤報整頁圖 | 第三方使用者對 `warn` 失去信心 | 只到 `warn`、附縮圖；校準表量 precision 並公開 |
| Tier B 來源下架或改授權 | 語料不完整 | MANIFEST 記 sha256 與抓取日；缺檔即 skip 並標 `missing`；報告寫明當次實際樣本數 |
| 政府文件含個資 | 法律 | 抓取時人工檢視、刪頁、記錄；只收公報類（本就公開） |
| Augraphy 版本變動使合成掃描不可重現 | 分數漂移 | 釘版本；`gt.json` 記 generator 版本；影像 sha 不要求一致（只要求文字正解一致），報告列 generator 版本 |
| TEDS 套件授權不明 | 不能進 repo | 先查；不行就自實作（APTED 演算法公開） |
| benchmark 被誤讀成「我們比 MinerU 強」 | 信譽 | §5 固定文字強制出現；`auto` 的優勢來源拆成 `auto-norepair` 對照 |
| `verify` 校準不達標 | 發表延後 | 這是刻意的閘門（§7 #9） |
| 1200 頁合成書每個設定 15–40 分鐘 | 迭代慢 | 只對 `auto`／`mineru` 跑；`--corpus A-small` 排除它 |

## 9. 對主 spec、index 與 README 的修訂

- 主 spec §3 架構：加 `verify.py`、`verify_report.py`、`markers.py`；§5 輸出：`aidoc verify` 的報告檔；§6 API 表加 `POST /verify`；§7 skill：加「轉完可以 `aidoc verify out/<stem>/` 看逐頁報告」一行；§10 測試加本 spec §6。
- 逐頁 spec §7：`infer_pages()` 作為同一演算法家族的新成員，數字（24 字、唯一性、20%–80%）共用。
- MCP spec §5.2 tool 一覽加 `verify_document`（read）、`verify_markdown`（convert）。
- index：§1 layout 加 `src/aidoc/{verify,verify_report,markers}.py`、`bench/`（`corpus/`、`run.py`、`metrics.py`、`report.py`、`results/`）、`docs/benchmarks/`、`tests/fixtures/{docling_recorded,markitdown_recorded}/`；§3 加 `VerifyReport`；§8 API 加 `POST /verify` 三形狀與錯誤碼；§2 新增 A24（`verify` 純函式、不碰 store、不寫檔）、A25（benchmark 不屬於套件、不進 wheel、GPU 部分不在 CI 跑）。
- README：「Tests」節後加「Verify」與「Benchmark」兩節；License 節加 benchmark 語料授權說明（OFL 字型、Gutenberg、OGDL、CC BY、美國政府著作）。

## 10. 驗收紀錄（2026-10-12／13，RTX 5070 Ti、驅動 591.86；docling 2.130.0、mineru 4.0.7、markitdown 0.1.8）

實作偏差逐條見計畫 `docs/superpowers/plans/2026-10-05-verify-and-benchmark.md` 的「Implementation notes / deviations」。

| # | 結果 | 數字 |
|---|---|---|
| 1 | 通過（重用的檢查完全一致），另有新發現 | 兩本真書既有輸出：`verify` 的 `reasons` 皆為空、沒有 `broken_text_layer`／`garbage`／頁碼理由，與 sidecar 一致；但 `page_dropped` 標出骨科書 13 頁、Head First 51 頁（sidecar 都是 `ok`）。逐頁看過：MinerU 漏了整頁表格與章首頁，Docling 把整頁文字輸出成圖片。這是轉換流程本身漏掉的靜默缺頁，已把 `page_dropped` 收進轉換的逐頁品質模型並逐頁修復（`PAGE_CHECK_VERSION` 2，Library 重新評級一次）。修正後重轉：骨科書 13 頁中修好 10 頁、3 頁仍標 `warn`（781 s）；Head First 改由 MinerU 轉（Docling 被標的頁超過 20 %），15 頁中修好 13 頁（3268 s） |
| 2 | 通過 | 骨科書 1192 頁 MinerU 輸出去掉標記（MinerU 原生 `markdown.md` 即逐頁 render 的串接）：`confidence` 0.976（1155／1184），CLI 牆鐘 21.5 s。真正的 MinerU 原生輸出（`raw-mineru`）：ortho_p300-340 `confidence` 1.00、ortho_p1-80 0.99 |
| 3 | 通過 | 完整語料 `syn-dropped-pages`：control ok／exit 0；dropped（5／29 頁）warn、5 頁全被 `page_dropped` 標出、`--fail-on warn` exit 1；shifted low（`page_map_invalid`＋`misaligned`）exit 1；swapped low（`page_map_invalid`）exit 1。小樣本刪 1／3 頁 → 超過 20 % → low（與任何被標頁相同的文件規則） |
| 4 | 通過 | 掃描 PDF＋無標記 → exit 3、`verifiable: false`；有文字層但 Markdown 完全對不上時，摘要改說「對不上文字層」而不是「掃描檔」 |
| 5 | 通過 | JSON 鍵與 sidecar 相容（`doc_flags()` 直接吃）；HTML 無 `<script>`、無外部 URL；人讀摘要 ≤ 12 行 |
| 6 | 通過 | `POST /api/verify` 三種 body、`verify_document`／`verify_markdown` 測試全綠；1192 頁級報告裁切到預算內 |
| 7 | 通過（兩處偏差） | Tier A 全量重生 100 s；小樣本 GT 逐位元組一致；MANIFEST 9 筆皆有授權 URL 與 sha256；repo 無字型檔。偏差：小樣本 2.37 MB（目標 2 MB）、Tier B 沒有網路公有領域繁中掃描書 |
| 8 | 已跑、**未發表** | 見下方；README 沒有放結果表 |
| 9 | **未通過** | Spearman ρ 0.48、頁級 recall 0.16（precision 1.00），門檻 0.6／0.9 → 依 §3.1 原則 5 不發表 |
| 10 | 通過 | 見計畫偏差節的回歸紀錄 |

### 10.1 為什麼校準沒過（這正是閘門要擋的）

基準跑（修正前，A＋B × 5 個設定，142 列 Tier A L1）：ρ = 0.48、recall = 0.16、precision = 1.00（`verify` 標出的頁全是真的壞頁，但大多數壞頁沒標出）。漏掉的壞頁（共 582 頁）來源：

- 掃描樣本 346 頁：MarkItDown 對掃描檔輸出空白（文件層已判 `low`，但頁層沒有逐頁標記），以及 OCR 認錯字的頁（NED < 0.5）——掃描檔沒有文字層，`verify` 依設計看不到 OCR 內容錯誤（§2.7）。
- MinerU 的 1200 頁書 143 頁：跨頁長段落被整段歸到其中一頁，半頁內容落在鄰頁（NED < 0.5，但探針抽查仍判對齊）。
- 其餘：Docling 對英文合成書的頁歸屬錯位（文件層 `page_map_misaligned` → low，但頁層只標出空頁）。

診斷變體（只供參考，閘門仍以計畫定義為準）：把 `low` 文件的每一頁都算成已標出 → recall 0.55；只用 spec §3.4 的 GT 缺陷頁 → recall 0.57；去掉掃描樣本 → ρ 0.50、recall 0.32。沒有一種達標，所以不是定義問題。**更正（2026-10-13）**：先前寫的「修正後 ρ 0.48 → 0.67」比較的是不同的列（142 列的五個設定 vs 40 列的 `auto`＋小樣本 `vlm`），0.67 主要來自 11 列 `vlm`（其 ρ 0.82）；只看 `auto`，修正前 ρ 0.11、修正後 ρ −0.10，兩者都表示 `auto` 的 verify 分數與 NED 幾乎無關，不是改善。

後續（未做）：頁層的「整份 low → 每頁標記」呈現、掃描檔的 OCR 信心訊號、跨頁段落的頁歸屬檢查。

### 10.2 基準跑的重點數字（不發表，僅供內部判斷；`bench/results/2026-10-12-ulove-arrangement/`，不進版控）

Tier A 平均 NED（括號內為最差樣本）：

| 類別 | auto | docling | mineru | markitdown |
|---|---|---|---|---|
| 繁中數位 ×5 | 1.00 | 1.00 | 0.83 (0.75) | 0.98 |
| 英文數位 ×3 | 0.94 (0.90) | 0.51 (0.44) | 0.94 | 0.98 |
| 繁中掃描 ×5 | 0.68 (0.39) | 0.96 (0.93) | 0.68 (0.39) | 0.00 |
| 壞字型 10 % ×2 | 0.98 | 0.65 | 0.86 | 0.98 |
| 壞字型只在頁尾 ×1 | 0.49 → 修正後 0.94 | 0.49 | 0.94 | 0.98 |
| 表格 ×4（TEDS） | 0.74 | 0.74 | 0.89 | 0.26 |
| 公式 ×2（公式 NED） | 0.00 | 0.00 | 0.84 | 0.00 |
| 1200 頁書 | 1.00（80.8 s） | — | 0.77（282.6 s） | — |

這張表指出的路由問題（後續）：繁中掃描 `auto` 走 MinerU（0.68）而 Docling＋RapidOCR 是 0.96；公式文件 `auto` 走 Docling（公式 NED 0）而 MinerU 是 0.84；表格 MinerU 較好。雙欄合成樣本的繪製方框被 probe 當成表格格線（`missing_table` 誤判，三個引擎都試過）。

> 真實樣本評估（2026-10-22，`docs/superpowers/evals/2026-10-22-real-sample-eval-record.md`）：登錄的那一次 run 因共用的英文對照組檢查不過而**無效**，(1a) 繁中掃描路由與 (1b) 公式路由都是 `insufficient`，路由不改；上面兩個合成訊號仍是「真實樣本未確認」。

修正後（`2026-10-12-ulove-arrangement-pagedrop/`）：`auto` 與基準相比只有三處改變，全是 `page_dropped` 修復：壞字型頁尾樣本 0.49 → 0.94（改走 MinerU）、arXiv RL-ARC 修好 5 頁、兩份 NIST 各修好 1 頁；其他樣本 NED 不變。

VLM 欄（本機 Ollama `gemma4:12b`，`max_output_tokens` 4096，只跑小樣本）：幾乎每頁都 `truncated`，NED 多為 0、每頁約 66 s——這個模型在這個輸出上限下不可用，數字不代表 VLM 引擎本身。

### 10.3 驗證者回合（2026-10-13）

修正（每項都有先失敗的測試，見計畫偏差節「Verifier round」）：

1. 逐頁修復的接受條件改為「新段落涵蓋該頁 prose 文字層 ≥ 0.5」（字元二元組多重集合）；只有標題與圖片的「修復」不再算數。
2. 每頁用唯一探針找出它的文字在哪一段：文字整頁跑到別頁底下 → `page_misaligned`（不做 OCR 修復，不會重複插入）；收容別頁文字、或遠大於自己文字層的段落 → `page_oversized`。
3. 沒有原始檔也沒有工作副本時，重評不蓋新版本章（Library 顯示「未評估」）；兩本書的原始檔經 sha 驗證後寫回 `source_path`。
4. 重評沿用每頁歷史（`repaired_by`、引擎回報的失敗頁、頁界方法），資料庫被前次重評洗掉時從 sidecar 讀回；`PAGE_CHECK_VERSION` 3。Live 資料庫先備份為 `data/aidoc.db.bak-20261013-023339`、在副本上試跑後才套用：Head First 的 55 筆修復紀錄從 sidecar 找回；骨科書沒有逐頁歷史可找（原轉換本來就 0 筆）；11 份沒有 PDF 的文件維持舊版本、顯示「未評估」。
5. `page_misaligned` 成為頁層級理由，verify 預設檢查每一頁；benchmark 加入「標記不動、兩頁內容對調」與「一頁截到 15 %」兩個負樣本（列在閘門旁，不改閘門）。
6. `page_partial`：段落涵蓋 prose 文字層 < 0.5。prose 文字層去掉頁首頁尾、頁碼與 20 字以下的短行（圖中標籤、表格儲存格）——這也修好了「圖頁只有頁首加圖說卻被判 dropped」。
小項：自訂標記 regex 限長 200、群組必須一定參與比對、拒絕巢狀無界量詞（422／exit 4）。

**重跑 benchmark（`bench/results/2026-10-13-ulove-arrangement/`，A＋B × 5 設定，閘門未改）**：ρ 0.47、頁級 recall 0.045、precision 1.00 → 仍未通過。recall 比前次（0.16）低，原因是閘門定義只算 `page_dropped`／`broken_text_layer`／`garbage`／缺標記／失敗頁：原本被當成 dropped 的錯位頁現在是 `page_misaligned`，不在閘門的計算裡。僅供參考：
- 計入所有頁層級理由（含 `page_partial`／`page_misaligned`／`page_oversized`）：全部 142 列 recall 0.32、precision 0.87；
- 只看有文字層的樣本（102 列，`verify` 能檢查的範圍）：ρ 0.51，計入所有理由時 recall 0.68、precision 0.87；
- `verify` 對有標記的掃描檔也回報 `verifiable: true`，所以「verifiable 列」與全部列相同（142 列，ρ 0.47、recall 0.045）；
- 只看 `auto`：ρ −0.03（verify 分數幾乎都是 1.0，`auto` 的錯誤主要是掃描檔的 OCR 錯字，看不到）。
Tier A 各類 NED 與前次相同（例如繁中掃描 `auto` 0.68、Docling 0.96）；壞字型只在頁尾樣本 `auto` 0.94。

**兩本書以 force 在 Library 重轉**（逐頁抽查過）：

| 書 | 引擎 | page_dropped | page_partial | page_misaligned | page_oversized | broken_text_layer | 已修復 | 等級 | 秒 |
|---|---|---|---|---|---|---|---|---|---|
| 骨科書（1192 頁） | MinerU | 12（修 4） | 6（修 0） | 1 | 4 | 0 | 4 | warn | 830 |
| Head First（532 頁） | Docling → MinerU（Docling 被標頁 > 20 %） | 2（修 0） | 22（修 3） | 0 | 1 | 12（修 12） | 15 | warn | 3443 |

抽查：骨科 1052（整頁表格）修復後是完整的 HTML 表格；282（流程圖頁，標籤都是長行）OCR 只拿回少量文字，涵蓋率不到 0.5 → 不算修好、留 `warn`（前次這種頁會被誤記為已修）；1014 的文字確實在 1013 底下（`page_misaligned`，1013 為 `page_oversized`）。Head First 120 是程式碼頁，PDF 文字層含被手寫註記蓋住的舊版程式碼，MinerU 輸出畫面上的版本 → `page_partial`（文字層與畫面不一致，屬於訊號的已知限制）；74 由 Docling 全頁 OCR 修好。

**更正（2026-10-20）**：第 4 項「Head First 的 55 筆修復紀錄從 sidecar 找回」只對 10-13 那次重評成立；之後的 MinerU 重轉取代了那份轉換，文件實際只有 15 頁已修復（上表「已修復」欄）。第 5 項之後的「有標記的掃描檔也回報 `verifiable: true`」已改為 `"partial"`（見 10.4）。

### 10.4 驗證者第二回合（2026-10-20）

修正（計畫偏差節「Verifier round 2」列出每項的測試）：

1. **有標註的真實頁面集**：`tests/fixtures/page_labels.json`，293 頁（兩本書加五份原始引擎樣本），只存文件身分（sha256）、頁碼、預期理由與判定，不含任何文件文字；`tests/integration/test_page_labels_real.py`（slow，`AIDOC_REQUIRE_MANUAL=1` 下缺樣本就失敗）要求每個理由 precision 與 recall ≥ 0.9。流程：先標頁、再改檢查；每個 test 分割只量一次，量過就變成 dev，再抽新的 test。標註更正都記在該頁 `note`（其中五頁 Head First 是我的評判工具把 `<XML>` 程式碼當 HTML 標籤刪掉造成的誤標——與發現 B 同一個 bug）。
2. **A**：`page_dropped` 改看去掉頁首頁尾的完整文字層（body，≥ 300 字）或 prose 層（≥ 120 字），段落空白或只有標題／圖說（涵蓋 < 0.2 且 < 150 字）即 dropped；`page_partial` 仍看 prose 層。涵蓋率改為字元 5-gram 多重集合（二元組對任何英文都高估）。
3. **B**：只去掉引擎會輸出的 HTML 標籤、實體與連結網址；用「該頁獨有」的 5-gram 判斷文字是否在別段——合併表格頁成為 `page_misaligned`（不再 OCR 第二份），收容頁 `page_oversized`；重複句子、沒有唯一探針的頁也找得到。收容判定只看這個證據：一個或兩個探針（跨頁延續的段落）與段落大小都不再決定 oversized（第二個 test 分割裡這兩種造成 11 個誤報）。壞字型頁改用該頁健康字型的文字檢查（Head First 532 頁中 339 頁用了壞的手寫註記字型，本文卻抽得出來）。
4. **C**：自訂頁碼標記改成固定樣板（`[[p {page}]]` 或 `\[\[p (\d+)\]\]` 這類：字面文字、跳脫標點、`\s` 系列、一個頁碼群組），編譯成線性時間的樣式；五個繞過寫法立即 422。
5. **D**：重評時，歷史上的修復若段落仍不過檢查 → `repair_rejected`、算未修復；頁首頁尾是否算 furniture 改以整份文件判斷（只有頁首頁尾或只有短圖說的頁不再被判 dropped；verifier 的 edge3.pdf 現在只標出真的錯位 5／7）；連結網址不計入段落大小（GEO 報告 p14 不再誤報）；有標記的掃描檔回報 `verifiable: "partial"`；修復必須涵蓋該頁檢查所用的那一層（dropped 用 body 層——重轉時骨科 p779 曾在修復階段被接受、最終檢查又否決）。
6. **閘門輸入更正**：`bench/run.py` 的 `FLAG_REASONS` 漏了 `page_partial`／`page_misaligned`／`page_oversized`，改為涵蓋全部頁層級理由；閘門定義與門檻不變。

**標註頁上的 precision／recall**（每格 P / R，括號內為正例數）：

| 理由 | 舊檢查（98c58f0），全部標註 | 新檢查，最後一個 test 分割（凍結後才抽） | 新檢查，全部標註 |
|---|---|---|---|
| page_dropped | 0.94 / 0.63 (27) | 1.00 / 1.00 (3) | 1.00 / 1.00 (27) |
| page_partial | 0.52 / 0.73 (15) | 0.80 / 1.00 (4) | 0.88 / 1.00 (15) |
| page_misaligned | 1.00 / 0.67 (3) | —（0 正例，0 誤報） | 1.00 / 1.00 (3) |
| page_oversized | 0.40 / 0.29 (7) | 1.00 / 0.50 (2) | 1.00 / 0.86 (7) |

閘門（≥ 0.9）**沒有全過**：page_partial precision 0.88（兩頁程式碼頁的旁註算 prose，屬邊界）、page_oversized recall 0.86（hfmix3 第 28 頁收容第 29 頁 0.297 的獨有文字，門檻 0.30）——如實回報，沒有在看過的頁上調門檻。page_misaligned 與 page_oversized 的正例很少（3／7），數字的信賴區間很寬。前兩個 test 分割量到的數字見計畫偏差節。

**重跑 benchmark（`bench/results/2026-10-20-ulove-arrangement/`，A＋B × 5 設定，閘門未改）**：ρ 0.38、頁級 precision 0.74、recall 0.37（142 列）→ 仍未通過（前次 ρ 0.47 / P 1.00 / R 0.045；R 上升主要來自 `FLAG_REASONS` 更正與新的 dropped 規則，P 下降是 86 個被標頁的 NED ≥ 0.5，其中 MinerU 46、Docling 12、MarkItDown 12、auto 8）。僅供參考：只看有文字層的 102 列 ρ 0.42、P 0.74、R 0.77；掃描檔改為 `verifiable: "partial"` 後「verifiable 列」與有文字層的列相同。Tier A 各類 NED 與前次相同。

**兩本書以 force 在 Library 重轉**：

| 書 | 引擎 | page_dropped | page_partial | page_misaligned | page_oversized | broken_text_layer | 已修復 | 被否決的修復 | 秒 |
|---|---|---|---|---|---|---|---|---|---|
| 骨科書（1192 頁） 前次 | MinerU | 12（修 4） | 6（修 0） | 1 | 4 | 0 | 4 | — | 830 |
| 骨科書 本次 | MinerU | 17（修 0） | 1（修 1） | 5 | 3 | 0 | 1 | 0 | 879 |
| Head First（532 頁） 前次 | Docling → MinerU | 2（修 0） | 22（修 3） | 0 | 1 | 12（修 12） | 15 | — | 3443 |
| Head First 本次 | Docling（187／525 頁被標）→ MinerU | 2（修 0） | 18（修 2） | 0 | 0 | 12（修 12） | 14 | 0 | 3596 |

骨科 1052–1054（合併到 1051 的表格）現在是 `page_misaligned`、各自段落空白、文字只在 1051 一份（前次被 OCR 再插入一份）；1129 收容 1130 整張表 → oversized／misaligned。骨科 17 頁 dropped 都是整頁流程圖（「Interpretation of the clinical examination」系列）與整頁表格，MinerU／Docling OCR 都拿不回一半以上的文字 → 留 `warn`，不假裝修好。

### 10.6 驗收紀錄：標註閘門（2026-10-21）

`heldout_v2` 由獨立驗證者盲標（183 頁，種子 20261021，提交 5f99bba，標註者為驗證者）。正例數：dropped 1、partial 4、misaligned 1、oversized 1，全部 < 15 → `test_page_labels_real.py` 的四個理由都是 **strict xfail**（「blind P/R not measurable: n_pos=… < 15」），**不是通過**；缺樣本仍然失敗。新增凍結門檻 `FIGURE_AREA_MAX` = 0.8（OCR 文字層的掃描頁每一行都落在整頁影像上，prose 層因此為空、`page_partial` 永遠不會觸發；蓋住 ≥ 80 % 頁面的圖片不算圖區），之後只重跑一次標註評分、沒有調整別的門檻。

### 10.5 驗證者第三回合（2026-10-20／21）

修正（計畫偏差節「Verifier round 3」列出測試與凍結門檻）：`page_partial` 改為提示（不提高等級、不修復、不換引擎）；頁面文字政策（使用者確認：程式碼、表格、本文算；圖片中的旁註不算——嵌入圖片上的非等寬行移出 prose 層）；錯誤 Unicode 對照的文字層以語言模型偵測後標為不可信（合成壞字型樣本的假 `page_partial` 34 → 1）；壞字型改為逐頁、依壞字元比例 ≥ 2 %，符號字型不算（Head First 339 → 124 頁）；自訂標記相鄰空白合併為一段（加 fuzz 測試）；標註閘門只看獨立驗證者盲標的 `heldout_v2`（Wilson 95 % 下界），在標註完成前於驗收模式下失敗。

**校準閘門（§3.4 的 2026-10-20 新定義），全新種子 Tier A（`fresh-20261020:`）＋ Tier B，A＋B × 5 設定，只跑一次：未通過。**

| | n | ρ（≥ 0.6） | 頁 precision（≥ 0.9） | 頁 recall（≥ 0.9） |
|---|---|---|---|---|
| 有文字層的列（閘門） | 102 | **0.63** ✓ | **0.83** ✗（tp 234／fp 49） | **0.72** ✗（fn 93） |
| 掃描檔（另列，不參與） | 40 | 0.61 | —（沒有可判斷頁） | — |
| 舊定義（參考） | 142 | 0.55 | 0.83 | 0.35 |

各設定（有文字層的列）：Docling ρ 0.84、P 0.90、R 0.93；MinerU 0.53／0.79／0.68；auto 0.47／0.50／0.14；auto-norepair 0.55／0.80／0.40；MarkItDown 0.38／1.00／0.00（12 頁壞頁全沒標）。Tier B 只有 L3 正解，標記頁數：auto 5、auto-norepair 5、Docling 3、MinerU 22、MarkItDown 0。`syn-dropped-pages` 負樣本通過（切頁負樣本以提示呈現）。依決定不在看到結果後調整；benchmark 頁與 README 表格**不發表**。Tier A 的 NED 與舊種子相近（例：英文數位 auto 0.96、Docling 0.45）。

**兩本書以 force 在 Library 重轉（本回合程式碼）**：

| 書 | 引擎 | page_dropped | page_partial（提示） | page_misaligned | page_oversized | broken_text_layer | 已修復 | 秒 |
|---|---|---|---|---|---|---|---|---|
| 骨科書 | MinerU | 17（修 0） | 1 | 5 | 3 | 0 | 0 | 1568 |
| Head First | Docling（127／525 頁被標）→ MinerU | 2（修 0） | 18 | 0 | 0 | 12（修 12） | 12 | 3266 |

Head First 這一列是加上 `font_suspect_pages`（見下）之後再重轉一次的結果。`page_partial` 不再送修，所以 Head First 的「已修復」從 14 降為 12（前次修好的 2 頁 partial 現在只是提示）；骨科前次 1 頁 partial 修復同理。

重轉後的慢速測試抓到一個回歸：依比例判定壞字型頁後，Head First 只有一個壞標題（佔頁面 0.5–1 %）的 T-001 頁不再被檢查 Markdown 亂碼。修正：`ProbeResult.font_suspect_pages` 列出每個用到壞（非符號）字型的頁，`flag_pages`（還需要 Markdown 真的有亂碼）看這張清單；壞字型清單本身（對齊、報告、涵蓋率檢查）仍依比例。

## 11. 已知限制（2026-10-20）

- **沒有 PDF 就不能重評**：原始檔與工作副本都不在的文件，重評時保留上次的結果與 `page_check` 版本（Library 顯示「未評估」）；例如佑立診所 GEO 報告（doc 20a31841）的 p14 舊 `page_oversized`（連結網址造成的誤報，新檢查已不會標）要重新上傳或重轉才會消失。
- **`page_partial` 是提示**：盲標 κ 只有 0.29（定義漏洞：只以圖片呈現的程式碼、旁註算不算頁面文字）。使用者 2026-10-20 決定：程式碼、表格、本文算；圖片裡的旁註與標籤不算。檢查只認得「落在嵌入圖片上的行」（等寬字體除外），向量繪製的圖中標籤與 20 字以下的短行（含表格儲存格）一律不列入比對；在獨立驗證者的盲標 held-out（`heldout_v2`）通過之前，`page_partial` 不提高等級、不觸發修復或換引擎。
- **只以圖片存在的程式碼**：PDF 沒有文字層可比，看不到它的遺失。
- **跨頁／跨欄延續的段落**：引擎常把下一頁（或雙欄頁的下一欄）延續的段落放在前一頁標記底下；這不算 `page_oversized`（否則某些書每頁都會被標），所以整欄被搬走的頁會被低報。
- **錯誤 Unicode 對照的文字層**（位移密碼字型）：以文件自身的字元二元語言模型偵測、標為不可信，只在段落空白時判 `page_dropped`。
- **校準閘門**：見 §3.4 的 2026-10-20 新定義與 §10.5 的結果；掃描檔的 OCR 錯字 `verify` 看不到（只另列報告）。
- **文字層與輸出裡一模一樣的亂碼抓不到**：引擎原樣抄出亂碼文字層時，涵蓋率檢查看到的是「相符」；文字系統一致性檢查（script-consistency）延後。
- **雜訊多的 OCR 文字層上，搬走的文字可能漏抓**（例：AASHTO 第 4 頁，盲標為 `page_oversized`，檢查沒標）：「該頁獨有的 n-gram」在 OCR 錯字下不穩。
- **延續段落的邊界是判斷題**：一段跨頁延續的文字要算「正常延續」還是「整塊搬走」，標註者之間也會不同。
- **盲標 P／R 尚未量到**：獨立驗證者盲標的 `heldout_v2`（183 頁，種子 20261021）每個理由只有 1–4 個正例，少於 15：標註閘門以 strict xfail 回報「blind P/R not measurable」，不是通過。僅供參考（同一次重跑，`FIGURE_AREA_MAX` 之後）：dropped 1/1、partial 提示 tp 4 fp 2、misaligned tp 1 fp 1、oversized tp 0 fp 1 fn 1。
- **校準閘門未通過**（ρ 0.63、P 0.83、R 0.72，§10.5）：benchmark 不發表。
- **已公布的閘門數字早於 4a2dd90**（font_suspect_pages 修正）：那次修正只影響壞字型頁的 Markdown 亂碼檢查，閘門沒有重跑。
