# 逐頁品質與頁碼對照（page map）系統性修正 — 設計文件

- 日期：2026-10-01
- 狀態：待審核（v1：spike 完成、方案定案；併入 T-001）
- 範圍：A 頁界由結構產生、B 頁碼完整度與對齊列入品質、C 真實樣本關卡、D 既有資料修復；另併入 [T-001](../../tickets/T-001-head-first-android-cover-garbled.md)（文字層損壞頁）——兩者統一為「**逐頁品質**」模型。
- 主規格：`docs/superpowers/specs/2026-09-24-ai-friendly-doc-design.md`（以下稱「主 spec」）。本文件修訂主 spec §3（頁界資訊來源）、§4（品質檢查）、§5（sidecar）、§6（Library／Document）、§10（測試），修訂點見第 14 節。
- 實作計畫：`docs/superpowers/plans/2026-10-01-per-page-quality-and-page-map.md`
- Spike 腳本與原始輸出：`.superpowers/spike/`（gitignored，不進版控；數字皆由這些腳本實測）

## 1. 背景

### 1.1 頁碼標記不完整

Document 檢視的左右同步完全依賴 `<!-- page: N -->`。真實書籍《A System of Orthopaedic Medicine, 3rd Edition》（1192 頁，document `f52860b8…`，MinerU）只產生 **30 個標記**（每 40 頁段一個），品質卻是 `ok 1.00`。

- 成因：`mineru_runner.insert_page_markers()` 用「文字搜尋」把 MinerU 的區塊對回 `markdown.md`。commit 93ad338 修了頁碼／頁首區塊造成的跳躍，但 **spike 在同一本書的兩段真實樣本上，修正後的函式仍然回傳 `None`（0 個標記）**：第 10 頁目錄（`index` 區塊）在 `structured_content.json` 與 markdown 的呈現不同，6 字元短針（`"althou"`、`"Periph"`）一路配到檔尾（cursor 31,895 → 83,297 → 125,373 → 244,576 → 292,550／312,345），80 頁只對到 12 頁，低於一半門檻 → `None` → `segment.merge_segments()` 在每段開頭補一個段首標記 → 30 個標記。**文字搜尋本質上不可靠，任何修補都只是換一種失敗方式。**
- 品質檢查完全不看頁碼，所以 `ok`。

現有輸出盤點（`out*/`，只計 PDF）：

| 文件 | 引擎 | 頁 | 標記 | 覆蓋率 |
|---|---|---|---|---|
| A System of Orthopaedic Medicine, 3rd Edition | mineru | 1192 | 30 | 0.025 |
| text（`text.pdf` 強制 MarkItDown） | markitdown | 3 | 0 | 0 |
| blank、text_for_overwrite_test-946f5abe（1 頁） | mineru | 1 | 0 | 0 |
| 其餘 Docling／MinerU PDF（含 long315、Agentic-Design-Patterns-CN 390 頁、Neo4j 266 頁、Head First Android 532 頁） | — | — | 全頁 | 1.000 |

（`page.png`、`sample.pptx` 等非 PDF 沒有頁的概念，不列入。）

### 1.2 文字層損壞頁（T-001）

《Head First Android Development》（532 頁，Docling，品質 `ok` 0.971）有 **55 頁**亂碼：PDF 的 Comic Sans 等字型以 Type0／Identity-H 嵌入且沒有 ToUnicode，docling-parse 把字形 ID 當字元碼輸出（固定位移、空白變 `U+FFFD`）。全書平均的 `garbage_ratio` 0.0048 把封面的 0.12 稀釋掉。Docling 只開 `FULL_PAGE` OCR 救不了（docling-parse 用同一份錯誤 decode 畫頁面圖給 OCR）。詳見 ticket。

### 1.3 共同根因

1. **品質只有全書平均，沒有「頁」的概念**：缺頁碼、壞頁都被平均掉。
2. **頁的內容與頁界都是「推回來」的**：頁界靠文字搜尋、頁文字靠 PDF 文字層，兩者都可能壞，但沒有任何一處驗證。

因此本修正把品質改成**逐頁**：每頁有自己的檢查結果，文件等級由頁推導；頁界改為「由引擎的結構化輸出逐頁產生」，再用 PDF 文字層抽查對齊。

## 2. 目標與非目標

目標：

1. 所有 PDF 輸出**每一頁（含空白頁）都有** `<!-- page: N -->`，由構造保證，不靠搜尋。
2. 頁碼完整度與對齊是品質的一部分：缺頁碼／錯位 → `low` → 換備援；不再「ok 但不能同步」。
3. 文字層損壞頁被逐頁偵測，並以 OCR 逐頁修復後接回原位；修不好的頁會被標出，文件不再靜默 `ok`。
4. 真實文件關卡：真實樣本（不進版控）在驗收模式下缺檔就**失敗**；左右同步準確度有自動化 e2e。
5. 既有資料：啟動／重新掃描時自動評估，Library 顯示「頁碼不完整」與「N 頁有問題」徽章，一鍵重新轉換。

非目標：MinerU／Docling 版面辨識本身的錯誤（例如漏掉圖中文字）；非 PDF 的頁碼；向量化。

## 3. Spike 結果

環境：MinerU 4.0.7（docvortex 0.4.25，tier `basic`，`ocr_mode="auto"`，與 runner 相同）、Docling 2.130 + RapidOCR、MarkItDown 0.1.8、PyMuPDF（主程式）。樣本：

| 樣本（`tests/fixtures/manual/`，gitignored） | 內容 |
|---|---|
| `ortho_p1-80.pdf` | 骨科書第 1–80 頁：封面、版權頁、**空白頁 3**、目錄、章首、running header + 頁碼 |
| `ortho_p300-340.pdf` | 骨科書第 300–340 頁：章節內文、表格、行間公式、圖 + 圖說、running header／footer／頁碼／側欄 |
| `ortho_blanks.pdf` | 第 41、42 頁 + 2 空白頁 + 第 43 頁 + 1 空白頁（6 頁，3 頁空白） |
| `hfad_cover_p1.pdf` | Head First Android 封面（T-001） |
| `hfad_garbled_mix.pdf` | Head First Android 第 1、16、17、25、31、61、108 頁（亂碼）＋第 20、45 頁（有壞字型但未亂碼的對照頁） |

### 3.1 MinerU 4.0.7 結構化輸出的 schema

| 檔案 | 結構 | 頁資訊 |
|---|---|---|
| `middle_json.json` | `{schema:"2.0", metadata:{producer:{version:"4.0.7"}, document:{page_count, page_count_kind:"physical"}}, pages:[{page_idx, blocks:[…]}]}` | **每一頁都有一筆**（空白頁 `blocks` 為空或只有 furniture），`page_idx` 連續 0..n-1（3 個樣本皆驗證） |
| `structured_content.json` | `pages[].blocks[]`，`content` 已攤平成字串 | 同上 |
| content list V1（`render_content_list`，save() 不寫，需自行 render） | 扁平陣列 | **每個 item 都有 `page_idx`**（1422 個 item，缺 0） |
| content list V2（`render_content_list_v2`） | `list[list[item]]`，每頁一個陣列 | 以陣列位置表示頁 |

區塊型別（p1-80／p300-340 合計）：`text`（含 `continues_prev`）、`paragraph_title`／`doc_title`（含 `level`）、`ref_text`、`index`（目錄）、`image`／`chart`（`*_body.image_path` + `*_caption` + `*_footnote`）、`table`（`table_caption` + `table_body`：HTML 字串 + `image_path` + `table_footnote`）、`equation`（LaTeX + `image_path`）、以及 **furniture：`header`／`footer`／`page_number`／`aside_text`，有明確型別**（docvortex `PAGE_AUXILIARY_BLOCK_TYPES`）。表格（HTML，簡單者 renderer 轉 GFM）、公式（`$$…$$`）、圖片（`images/…jpg`）、標題層級、列表（ref_text 合併為 list）、圖說／表說全部保留在結構中。

**關鍵發現：MinerU 的官方 Markdown renderer 本身就是逐頁的。** `docvortex.render._internal.common.planner.build_render_plan(middle_json, mode)` 回傳「每頁一個 `PlannedBlock` 陣列」，`render_markdown()` 只是把每頁 `_render_page(...)` 的結果以 `"\n\n"` 串起（略過空頁）。DEFAULT 模式會把 `continues_prev` 的段落／列表併到起始頁、把續表併到前表；`RenderMode.FULL`（`ParseResult.markdown(add_markers=True)`）是分頁檢視：保留 furniture、不跨頁合併、頁間以 `---` 分隔。

### 3.2 五種頁界產生方式的比較

比較基準為 MinerU 自己的 `markdown.md`。「差異」為 NFKC + 空白切 token、以字元長度加權的 SequenceMatcher 比對；「對齊」為第 7 節演算法（v3，全頁抽樣）。

| 方案 | p1-80（80 頁） | p300-340（41 頁） | blanks（6 頁） |
|---|---|---|---|
| 現行文字搜尋（93ad338） | **0/80 標記**（回 `None`） | **0/41 標記** | 6/6（小文件剛好成功） |
| **P：MinerU 官方 renderer 逐頁渲染**（DEFAULT plan，逐頁 `_render_page`） | 與 `markdown.md` **逐位元組相同**；標題 258／表格 7／行內公式 1／圖 100 全同；**80/80 標記**；對齊 74/75 = 98.7% | 逐位元組相同；標題 122／表格 3／行間公式 1／圖 69 全同；**41/41**；對齊 40/41 = 97.6% | 相同；6/6 |
| FULL plan（不跨頁合併，DEFAULT 過濾 furniture） | 字元總數相同，0.78% token 位置不同（跨頁段落拆開）；80/80；對齊 100% | 1.00% 位置不同；41/41；對齊 100% | 1.96% |
| 公開 API 單頁渲染（`render_markdown(單頁 MiddleJson)`） | 同 FULL：0.78% | 1.002% | 1.96% |
| 自寫 content list V1 renderer | recall 97.86%／precision 98.31% | 97.08%／96.37%，**行間公式 1 → 0** | 93.32% |

- P 與 `markdown.md` 逐位元組相同的驗證：6 個樣本全數通過（p1-80、p300-340、blanks、`scanned_cht.pdf`、`hfad_garbled_mix.pdf`、合成的 broken-ToUnicode PDF）。由已存檔的 `middle_json.json` 重新渲染亦與 `markdown.md` 相同（可離線重渲染）。
- 成本：80 頁逐頁渲染 0.08 s（與整份渲染相同）。
- P 的對齊失敗頁（p1-80 第 80 頁、p300-340 第 23 頁）都是**跨頁段落被併到起始頁**，屬設計行為（與 Docling「跨頁元素以首頁為準」一致），不是錯位。

**決策規則套用**：「結構式 markdown 與 `markdown.md` 差異 ≤ 1% 且不失表格／公式／圖／標題」——公開 API 單頁渲染在 p300-340（1.002%）與 blanks（1.96%）超標、自寫 V1 renderer 失公式，不採用；MinerU **有自己的逐頁 renderer**（P），差異 0%、100% 覆蓋 → 採用 P。P 等同「以 MinerU 自己的 render plan 決定頁界來切 `markdown.md`」，也就是任務描述中的 hybrid，只是頁界來自 renderer 的頁結構而不是區塊順序推算，**完全不需要文字搜尋**。

### 3.3 對齊抽查（第 7 節）驗證

| 文件 | 抽 30 頁 | 全頁 | 標記整體 +1 位移（陰性對照） |
|---|---|---|---|
| ortho p1-80（方案 P） | 28/29 = 0.966 | 74/75 = 0.987 | 1/75 = 0.013 |
| ortho p300-340（方案 P） | 29/30 = 0.967 | 40/41 = 0.976 | 1/41 = 0.024 |
| Head First Android（現有 Docling 輸出，排除壞字型頁） | 23/23 = 1.000 | 140/143 = 0.979 | 2/143 = 0.014 |
| Agentic-Design-Patterns-CN（現有 Docling，中文） | 28/29 = 0.966 | 372/374 = 0.995 | 2/374 = 0.005 |
| Neo4j Graph Algorithms（現有 Docling） | 30/30 = 1.000 | 251/254 = 0.988 | — |
| 骨科書現有輸出（30 標記） | **0/29 = 0.000** | — | — |

耗時：532 頁 0.5 s；1192 頁／470 MB 7.4 s（PyMuPDF 抽全書文字層佔 7.3 s）。

演算法演進（都在 spike 實測過，記錄以免重蹈）：v1「每頁 3 個 6 字探針、多數落在本頁區段」在 Head First 只有 0.48（大量程式碼清單跨頁重複、圖中文字被引擎丟掉）；v2「找不到的探針不計」仍 0.84（探針在別頁重複出現被當錯位）；**v3「探針必須在整份 PDF 文字層中唯一」**解決，正常文件 ≥ 0.966、陰性對照 ≤ 0.024，門檻 0.90 兩側都有充足餘裕。

### 3.4 MarkItDown 與 PDF 頁界

- MarkItDown 0.1.8 的 PDF converter 內部是逐頁的（pdfplumber 逐頁判斷表單版面），但輸出時：無表單頁 → 改用 pdfminer 整份 `extract_text()`（含 `\f` 分頁字元，`big.pdf` 45 頁有 44 個）；有表單頁 → 各頁 `"\n\n"` 串接並**丟掉空頁**（`ortho_p300-340`、`text.pdf` 都走這條，沒有任何頁資訊）。
- 原型：runner 用 MarkItDown 自己的函式（`_extract_form_content_from_words`、pdfminer `\f` 切頁、`_merge_partial_numbering_lines`）逐頁產生，並以「逐頁結果依 MarkItDown 的串接規則接回 + MarkItDown 的輸出正規化（每行 rstrip、3 個以上換行併為 2 個）== `MarkItDown().convert()`」自我驗證：**7/7 份 PDF 逐字相同**（ortho p300-340、ortho_blanks、text、twocol、big、blank、span_margin）；41 頁 4.2 s（原本 convert 4.9 s）。
- 建議：**保留** MarkItDown 為 PDF 最後備援，改成逐頁擷取（第 6.3 節）。理由：它是 Docling、MinerU 都失敗（或 GPU 環境壞掉）時唯一能出字的路徑；逐頁擷取幾乎零成本、可自我驗證；驗證失敗時沒有頁界 → `page_map_incomplete` → `low`，不會再「ok 但不能同步」。

### 3.5 T-001：偵測訊號與修復引擎

**壞字型 probe**（PyMuPDF `get_fonts()`：Type0 或 Identity 編碼且字型字典沒有 `/ToUnicode`，逐頁全掃）：

| 文件 | 耗時 | 壞字型 | 使用頁 |
|---|---|---|---|
| Head First Android（532 頁） | 0.1 s | 8 支（ComicSansMS、Arial-ItalicMT、ArialMT、MarkerFelt-Thin、SwisterSwister、SkippySharp、Wingdings2、Webdings） | 339 |
| 骨科書（1192 頁）、Agentic-CN（390）、Neo4j（266） | 2.4 s／0.4 s／0.2 s | 0 | 0 |

**逐頁文字訊號**（以 `<!-- page: N -->` 切頁，對照 ticket 人工確認的 55 頁）：

| 訊號 | 不經壞字型閘門 | 經壞字型閘門（頁 ∈ probe） |
|---|---|---|
| `\S\uFFFD\S` ≥ 1（glyph-3 空白特徵） | Head First 55 頁命中、其他文件 1109 頁 0 命中 | **55/55，誤報 0** |
| 逐頁 `garbage_ratio` > 0.05（頁內 ≥ 50 字） | 18 頁（全為真陽性），其他文件 0 | 18/55 |
| 位移 29 解碼後命中英文常用詞 ≥ 3 | 其他文件最多 33 次（**不可不經閘門使用**） | 48/55，誤報 0 |
| 「頁有文字層但輸出幾乎為空」（考慮過，**不採用**） | Head First 57 頁（多為整頁圖解，引擎把圖中文字當圖片，屬正常）| — |

結論：「頁使用壞字型」∩「`\S\uFFFD\S` ≥ 1 或逐頁 garbage > 0.05 或位移 29 解碼命中 ≥ 3」= 55/55、誤報 0（`\S\uFFFD\S` 一項即 55/55；另兩項是給「空白不會變成 U+FFFD」的字型用的備援訊號，閘門內在 Head First 其餘 284 頁壞字型頁上皆 0 誤報）；單看全書平均或單看字型都不行。

**修復引擎**（`hfad_garbled_mix.pdf` 9 頁；「片語」= 第 1、25、31、61 頁已知正確句子是否出現）：

| 設定 | 片語命中 | 修復後訊號 | 耗時（含模型載入） |
|---|---|---|---|
| Docling 預設（正式設定） | 0/4 | 7/7 亂碼頁命中 `\S\uFFFD\S` | 48.0 s |
| Docling 只開 `FULL_PAGE` | **0/4**（`\uFFFD` 消失但仍是 `RX0GQ…` 亂碼） | 位移解碼僅 0–1，**訊號抓不到** | 72.9 s |
| **B：Docling `PyPdfiumDocumentBackend` + `FULL_PAGE` + RapidOCR** | **4/4** | 9/9 乾淨 | 74.4 s |
| **A：MinerU `ocr_mode="auto"`** | **4/4** | 9/9 乾淨 | 13.9 s |

另以 PyMuPDF 內建 `cjk` 字型合成「Identity-H、刪除 `/ToUnicode`」的 2 頁 PDF（可產生、可進版控）：Docling 預設輸出 `8PVMEO\uFFFDU JU CF ESFBNZ…`（位移 31，重現）；B、A 皆還原 `Wouldn't it be dreamy…`。注意：合成檔上「只開 FULL_PAGE」也能修好（docling-parse 對此字型畫圖正確），**只有真實封面能重現「FULL_PAGE 救不了」**，所以真實樣本關卡必須包含 `hfad_cover_p1.pdf`。

## 4. 決策

1. **MinerU 頁界**：runner 由 `middle_json.json` 以 MinerU 官方 render plan 逐頁渲染（方案 P），每頁前加 `<!-- page: N -->`（空白頁也加）。`insert_page_markers()` 與所有文字搜尋邏輯**刪除**。
   - P 用到 docvortex 的內部函式（`_internal.common.planner.build_render_plan`、`_internal.markdown.renderer._render_page` / `_collect_markdown_anchor_targets`），以 `envs/mineru/uv.lock` 鎖定版本。runner 每次都做**自我驗證**：逐頁結果（略過空頁、以 `"\n\n"` 串接）必須與 `markdown.md` 逐位元組相同；import 失敗或不相同 → 改用**公開 API 單頁渲染**（`render_markdown(middle_json 只含該頁)`，覆蓋率同樣 100%，內容與 `markdown.md` 差 ≤ 2% 位置），`result.json` 記錄 `page_map_method`，主程式寫進 sidecar 與 log。兩條路都 100% 覆蓋，沒有「無頁界」的退路。
2. **Docling 頁界**：維持逐頁 `export_to_markdown(page_no=n)`（已 100%）。
3. **MarkItDown**：保留為 PDF 最後備援，改逐頁擷取（第 6.3 節）。
4. **文字層損壞頁修復**：主要用 **B（Docling pypdfium backend + FULL_PAGE）**，B 失敗或修復後仍被判壞時用 **A（MinerU `ocr_mode="ocr"`）**；主引擎為 MinerU 的文件順序相反（A 先）。理由：
   - 正確性兩者相同（4/4、9/9）。
   - 會遇到這個問題的幾乎都是 Docling 文件（MinerU `auto` 會自行偵測壞文字層改走 OCR）：B 與文件其餘頁同引擎、同 Markdown 風格（標題層級、圖片處理一致），符合主 spec §4「同一份文件用同一個 engine」的精神；A 會把 MinerU 的 `#`／圖片風格混進 Docling 文件。
   - B 不需要裝第二個 GPU 引擎；A 是安全網。
   - **禁止只開 FULL_PAGE**（實測救不了，且修復後的亂碼不含 `\uFFFD`，訊號偵測不到，會變成「看似已修復」）。
5. **品質改為逐頁**，文件等級由頁推導，新增等級 `warn`（第 5 節）。

## 5. 逐頁品質模型

### 5.1 頁記錄與理由代碼

每頁可被標記一個或多個**頁層級理由**：

| 代碼 | 判定 | 來源 |
|---|---|---|
| `page_map_missing` | 該頁沒有 `<!-- page: N -->` | 第 6 節 |
| `broken_text_layer` | 頁 ∈ `probe.broken_font_pages` 且（頁內 `\S\uFFFD\S` ≥ 1、或頁內 `garbage_ratio` > 0.05、或位移 29 解碼命中常用詞 ≥ 3） | 第 8.1 節 |
| `garbage` | 頁內非空白字元 ≥ 50 且頁內 `garbage_ratio` > 0.05（不需壞字型；同時符合 `broken_text_layer` 時只記後者） | 既有規則改為逐頁 |

文件層級理由（既有三個 + 新增三個）：

| 代碼 | 判定 |
|---|---|
| `chars_per_page`、`garbage_ratio`、`missing_table` | 不變（主 spec §4） |
| `page_map_incomplete` | PDF 且 `page_map.coverage` < 0.95 |
| `page_map_misaligned` | PDF 且對齊抽查 `decidable` ≥ 5 且 `ratio` < 0.90 |
| `pages_flagged` | 修復**前**被標記 `broken_text_layer`／`garbage` 的頁數 > 非空白頁的 20%（壞到應整份換引擎，而不是逐頁補） |

`page_misaligned` 不做頁層級理由：抽查只是樣本，且 DEFAULT plan 下跨頁段落本就會讓 1–3% 頁不對齊（第 3.3 節）。

### 5.2 文件等級

1. 有任何文件層級理由 → `low`（觸發備援；全部引擎都 `low` 時保留分數最高者，同主 spec）。
2. 否則，若仍有**未修復**的被標記頁（`page_map_missing`、`broken_text_layer`、`garbage`）→ `warn`。
3. 否則 → `ok`。已成功修復的頁記在 `quality.pages`（`repaired_by`），不影響等級。

`warn` 的語意：內容可用，但有已知問題頁；**不觸發備援**、task 狀態為 `done`、快取命中（與 `ok` 相同，不受 `--retry-low` 影響）、Library 顯示徽章並提供一鍵重新轉換。`documents.status` 增加 `warn`；task 狀態集合不變（`warn` 的 task 為 `done`）。

分數：`score = base × page_map_factor × (1 − 未修復被標記頁 ÷ 有效頁數)`，`base` 為主 spec 既有公式，`page_map_factor = coverage`（非 PDF 為 1）。四捨五入到 3 位。

### 5.3 段內快速檢查

每段（40 頁）完成後：既有三項 + `page_map` 覆蓋率（期望 = 段頁數）+ 段 PDF 的對齊抽查（抽 10 頁、`decidable` ≥ 5 才判）。**`pages_flagged` 不在段內判**（Head First 第 1 段 1–40 頁有 9 頁壞 = 22.5%，全書只有 10.3%；段內判會誤讓整份換引擎）。

### 5.4 sidecar（`<stem>.json`）新增欄位

```json
{
  "quality": {
    "score": 0.97, "level": "ok", "reasons": [],
    "metrics": {"chars": 262338, "chars_per_page": 499.69, "garbage_ratio": 0.0011, "effective_pages": 525, "has_table": true},
    "page_check": 1,
    "page_map": {
      "expected": 532, "found": 532, "coverage": 1.0, "missing": [],
      "method": "docling_per_page",
      "alignment": {"sampled": 30, "decidable": 23, "aligned": 23, "ratio": 1.0, "misplaced": [], "excluded_pages": 339}
    },
    "pages": [
      {"page": 1, "reasons": ["broken_text_layer"], "metrics": {"fffd_between": 24, "garbage": 0.12, "shift_hits": 12},
       "repaired_by": "docling:pypdfium_full_page_ocr"},
      {"page": 61, "reasons": ["broken_text_layer"], "metrics": {"fffd_between": 1, "garbage": 0.002, "shift_hits": 0},
       "repaired_by": "docling:pypdfium_full_page_ocr"}
    ],
    "pages_flagged": 55, "pages_unrepaired": 0
  },
  "probe": {"…": "…", "broken_fonts": ["ComicSansMS", "…"], "broken_font_pages_count": 339}
}
```

- `quality.page_check`：逐頁評估演算法版本（本版 = 1）。缺少或小於現行版本 → 既有資料需重新評估（第 11 節）。
- `page_map` 只在 PDF 出現；非 PDF 為 `null`（pptx 的 slide 標記不評估）。
- `page_map.method`：`mineru_render_plan` | `mineru_single_page` | `docling_per_page` | `markitdown_per_page` | `none` | `legacy`（既有資料重新評估時）。
- `quality.pages` 只列被標記（含已修復）的頁，不列全部頁；`missing` 最多列 200 個頁碼，多的以 `missing_truncated: true` 表示。
- `probe.broken_font_pages` 完整頁碼清單（可能上千筆）只在轉換與重新評估期間使用，sidecar 只放 `broken_font_pages_count`；重新評估既有文件時由來源 PDF 重新掃描（0.1–2.4 s）。
- 主 spec §5 的 sidecar 欄位全部保留；新增欄位皆為「多出來的 key」，舊讀者不受影響。

### 5.5 probe 新增

`ProbeResult.broken_fonts: list[str]`、`ProbeResult.broken_font_pages: list[int]`（1 起算，**逐頁全掃**，不是 20 頁取樣）。判定：`get_fonts()` 的字型類型為 `Type0` 或編碼含 `Identity`，且該字型 xref 沒有 `/ToUnicode`。字型 xref 結果快取，不重複查。

## 6. 頁界產生（A）

### 6.1 MinerU runner

```
parse(src, tier, ocr_mode)  →  result.save(writer)         # markdown.md / middle_json.json / structured_content.json / images/
pr = ParseResult.from_json(middle_json.json)                 # 已外置圖片路徑的版本，與 markdown.md 一致
pages = render_pages(pr.middle_json)                         # 方案 P；失敗 → 公開 API 單頁渲染
assert "\n\n".join(p for p in pages if p) == markdown.md     # 不同 → 改走單頁渲染，method 記為 mineru_single_page
out.md = "".join(f"<!-- page: {i+1} -->\n\n{p}\n\n" for i, p in enumerate(pages))   # 空頁也有標記
```

- `kind == "image"` 時不加標記（不變）。
- `result.json` 新增 `page_map_method`（host 端 `RawResult.page_map_method`）。
- `ocr_mode` 改由 `engine_opts.ocr_mode` 決定（預設 `auto`；修復時 `ocr`）。
- 已知行為：DEFAULT plan 把跨頁段落／續表併到起始頁，下一頁的標記出現在該段之後（約 1–3% 頁，第 3.3 節），與 Docling 一致；若之後該頁被逐頁修復，段落尾巴可能重複一次（第 13 節）。

### 6.2 Docling runner

頁界不變。新增 `engine_opts.backend: "docling_parse" | "pypdfium"`（預設 `docling_parse`）；`pypdfium` 時以 `PdfFormatOption(pipeline_options=po, backend=PyPdfiumDocumentBackend)` 建 converter，converter 快取鍵加入 backend。只在逐頁修復時使用（一律搭配 `full_page_ocr: true`）。

### 6.3 MarkItDown runner（PDF）

```
pages = []
for page in pdfplumber pages:  form = _extract_form_content_from_words(page)  → 有：form 頁；無：page.extract_text()
無任何 form 頁 → pdfminer extract_text(整份) 依 "\f" 切頁（去掉最後一個空段；段數 ≠ 頁數 → 視為失敗）
自我驗證：依 MarkItDown 規則接回（form 路徑 "\n\n" 串接非空頁；pdfminer 路徑原文）→ _merge_partial_numbering_lines
          → MarkItDown 輸出正規化（每行 rstrip、\n{3,} → \n\n）== MarkItDown().convert(path).markdown
通過 → 每頁前加 <!-- page: N -->，has_page_markers=true，page_map_method="markitdown_per_page"
不通過 → 用 convert() 原文，has_page_markers=false，method="none"（→ page_map_incomplete → low）
```

### 6.4 normalize／merge

- `normalize(raw, page_offset, seg_idx, page_numbers=None)`：新增 `page_numbers`（修復用）：第 j 個標記對應原文件第 `page_numbers[j-1]` 頁，圖片依該頁命名 `p<page>_<n>`。
- `merge_segments()` 對沒有標記的段仍補段首標記（保留部分同步能力），但這不再能掩蓋問題：`page_map` 會算出實際覆蓋率。

## 7. 對齊抽查演算法（B）

在主程式（PyMuPDF）執行，輸入：最終（或段）Markdown、對應 PDF、要排除的頁（`broken_font_pages`）。

```
squash(s)   = NFKC → 去掉軟連字號 U+00AD 與「-\n」斷字 → 小寫 → 只留 str.isalnum() 的字元（中英文皆適用，無需斷詞）
sections    = 以 <!-- page: N --> 切 markdown，各段 squash 後串成一個字串 T，並記錄每段起點（bisect 由位置查頁）
layer[i]    = squash(PDF 第 i 頁文字層)；whole = "\0".join(layer)
候選頁      = 不在排除清單、且 len(layer[i]) ≥ 120 的頁
抽樣        = 候選頁中等距取 min(30, 候選數) 頁（段內快速檢查取 10）；決定性，不用亂數種子以外的狀態
每頁探針    = 以 Random(seed=頁碼) 在 layer[i] 的 20%–80% 區間取最多 9 個起點、每個長 24 字元的子字串；
              只保留「在 whole 中恰好出現一次」者（排除跨頁重複的程式碼、running text），取前 3 個
判定        = 每個探針在 T 中找出現位置（最多 5 處）→ 對應頁；
              探針都找不到 → 該頁「不可判定」（引擎沒輸出該文字，例如圖中文字；不是錯位）
              否則該頁「可判定」，找到的探針中過半落在本頁區段 → aligned，否則 misplaced
結果        = {sampled, decidable, aligned, ratio = aligned / decidable, misplaced[≤10], excluded_pages}
文件判定    = decidable ≥ 5 且 ratio < 0.90 → page_map_misaligned；decidable < 5（掃描檔、無文字層）→ 不判
```

- 20%–80% 區間避開頁首頁尾（running header、頁碼）與跨頁段落的頭尾。
- 掃描 PDF 沒有文字層 → 不可判定 → 只看覆蓋率（由構造保證）。
- 文字層本身壞掉的頁（壞字型頁）一律排除，否則 OCR 修好的頁反而會被判錯位。
- 成本：全書文字層抽取一次（1192 頁 7.3 s），探針搜尋可忽略。
- 2026-10-05：同一家族的新成員 `pagemap.infer_pages()`——第三方 Markdown 沒有頁碼標記時，用相同的唯一 24 字探針（20%–80%、在全書文字層唯一）定位每頁、最長遞增子序列去掉錯序頁、推出頁界；只給 `aidoc verify` 使用，見 `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` 第 2.2 節。

## 8. 文字層損壞頁：偵測與逐頁修復

### 8.1 偵測

對最終 Markdown 每個頁區段計算：`fffd_between = count(\S\uFFFD\S)`、`garbage = garbage_ratio(區段)`（頁內，≥ 50 字才計）、`shift_hits`（區段中長 1–12 的可列印 ASCII token，每個字元碼 +29 後屬於常用英文詞表〔the、and、you、that、it、is、to、of、in、for、on、with、this、be、are、your、can、if、what、there、but、was、as、at、by、from、or、an、not、have、will、all、so、do、just、like、how、app〕的個數；29 = TrueType 標準 Mac 字形順序的位移〔glyph 3 = 空白〕，T-001 的亂碼即此位移。spike 也試過 k = 1..64 全搜尋：正常頁偶然命中 3–4 次〔對照頁 p45 在 k=5 命中 3〕，加上「多於未位移命中 2 倍」限制後只剩 7/55，故不採用；固定 29 在閘門內 48/55、誤報 0。只在閘門內使用，不經閘門時其他文件最多命中 33 次）。判定規則見第 5.1 節。

### 8.2 修復流程（pipeline checking 階段、合併之後）

1. 被標記頁（`broken_text_layer`／`garbage`）數量 > 非空白頁 20% → 不逐頁修，記 `pages_flagged` → `low` → 整份換下一個引擎。
2. 否則以 PyMuPDF 把被標記頁抽成 `data/work/<task>/repair/pages.pdf`（依頁碼順序），記住頁碼清單。
3. 依修復引擎順序（主引擎 Docling／MarkItDown：B → A；主引擎 MinerU：A → B；未安裝者跳過）開一個 runner session 轉換 `pages.pdf`：
   - B：`engine_opts = {..., "backend": "pypdfium", "full_page_ocr": true}`
   - A：`engine_opts = {"tier": <設定>, "ocr_mode": "ocr"}`
   - 逾時：每頁 60 s、最少 120 s（同主 spec §8.8）。
4. `normalize(raw, 0, seg_idx=900, page_numbers=頁碼清單)`，得到各頁新區段與新圖片（`p<page>_<n>`）。
5. 對每個新區段重跑第 8.1 節訊號（此時壞字型閘門仍成立）：乾淨 → 接受；仍被標記 → 留給下一個修復引擎；所有引擎都失敗 → 保留**原區段**，該頁記為未修復（`repaired_by` 不設）。
6. 接回：`splice_pages(markdown, {page: 新區段})` 只替換 `<!-- page: N -->` 到下一個標記之間的內容；移除原本屬於該頁的圖片（`assets/p<N>_*`），加入新圖片。
7. 重新計算 `quality`（包含 `page_map`、對齊抽查、字數），寫 sidecar：`quality.pages[*].repaired_by = "<engine>:<method>"`。
8. 修復 runner 的錯誤（逾時、OOM、engine error）只影響修復結果，不會讓 task 失敗：該頁維持未修復、log 一行、文件等級依第 5.2 節（多半為 `warn`）。取消會中斷修復並照常取消 task。

### 8.3 中斷與續轉

修復在所有段完成、合併之後執行，修復結果不入 segments 表。中斷後重跑時，段落依主 spec §8.3 續轉（已完成段沿用），修復階段整個重做（只有被標記頁，成本小）。

### 8.4 限制

- 位移 29 解碼只對拉丁文字、且只對標準 Mac 字形順序的字型有意義；中文文件靠 `\uFFFD` 與 `garbage` 訊號（且有壞字型閘門）。
- 修復後的驗證只能抓「仍有亂碼特徵」的頁；「OCR 讀到錯誤影像而輸出乾淨亂碼」（Docling 只開 FULL_PAGE 的情形）抓不到 → 所以修復引擎只允許 A、B 兩種已實測的設定。

## 9. 資料庫、API、UI（D）

### 9.1 資料庫

不新增資料表、不升 schema 版本：

- `documents.status` 值域增加 `warn`（`ok` | `warn` | `low` | `orphaned`）。
- `documents.quality_json` 內含第 5.4 節的 `page_check`、`page_map`、`pages`、`pages_flagged`、`pages_unrepaired`。
- `Store` 新增 `update_document_quality(doc_id, quality: dict, status: str)`、`list_documents(..., flag=None)`。

### 9.2 API（前綴 `/api`，皆帶 `workspace`）

| 方法 + 路徑 | 請求 | 回應 |
|---|---|---|
| `GET /documents` | 新增 `?flag=page_map_incomplete\|page_quality\|unassessed`；`?status=` 接受 `warn` | Document 物件新增 `flags: string[]`（伺服器端依 quality 推導，UI 不重算規則）與 `page_summary: {expected, found, coverage, flagged, unrepaired} \| null` |
| `POST /documents/rescan` | — | `{orphaned, assessed, page_map_incomplete, page_quality}`（標記 orphaned 之外，重新評估 `page_check` 缺少或過舊的文件；`?all=1` 全部重評） |
| `POST /documents/reconvert` | `{ids: [doc_id, ...]}`（1–500 筆） | `201 {job}`：一個 job、每份文件一個 task，`force` 且由路由自動選引擎（`flags.force` + `auto_engine`），輸出寫回原輸出目錄；錯誤：`404 not_found {id}`、`410 source_missing {id}`（原始檔與工作副本皆不在）、`409 already_converting {id, task_id}`；任一錯誤時不建立任何東西 |

`flags` 定義：

| flag | 條件 |
|---|---|
| `page_map_incomplete` | `quality.reasons` 含 `page_map_incomplete` 或 `page_map_misaligned` |
| `page_quality` | `quality.pages_unrepaired` > 0 或 `quality.reasons` 含 `pages_flagged` |
| `unassessed` | PDF 且 `quality.page_check` 缺少或小於現行版本 |

reconvert 的來源：`source_path` 是存在的絕對路徑且 sha256 相符 → 用它；否則用工作副本（`work_copy_path/src.*`，sha256 相符）。task 的 `source_path` 保持文件原本的值（顯示名稱與輸出目錄不變），工作副本記在 `work_path` 並以 `flags.reconvert = true` 讓 pipeline 從 `work_path` 取檔（現行只有上傳檔會這樣）。

### 9.3 Library

- 卡片／表格：`page_map_incomplete` → 紅色徽章 「頁碼不完整」；`page_quality` → 琥珀色徽章 「N 頁有問題」（N = `page_summary.unrepaired`）；`warn` 狀態徽章為琥珀色。
- 每列 「重新轉換」 按鈕（有任一 flag 時顯示），呼叫 `POST /documents/reconvert`，成功後跳到該 job。
- 頂部橫幅（有 flag 的文件 ≥ 1）：「有 N 份文件頁碼不完整或有問題頁」＋「全部重新轉換」＋「只看這些」（`?flag=`）。
- 篩選：狀態加入 `warn`；新增 flag 篩選（URL 參數 `flag`）。

### 9.4 Document 檢視

- 標題列：品質徽章旁顯示同 Library 的徽章；「跳至頁」數字輸入（1..pages，Enter 後兩側同時捲到該頁，供長文件與 e2e 使用）。
- `page_map_incomplete`：右側上方橫幅 「頁碼不完整（找到 F／E 頁），左右同步可能失準。」＋「重新轉換」。
- 被標記頁：右側該頁的頁錨點旁顯示提示——已修復：「第 N 頁：文字層損壞，已用 OCR 修復」（資訊色）；未修復：「第 N 頁：文字層損壞，內容可能是亂碼」／「第 N 頁：亂碼比例過高」（警示色）。左側 PDF 該頁外框加警示色細邊。
- JSON 分頁照常顯示整份 sidecar（含 `page_map`、`pages`）。

## 10. 真實樣本關卡（C）

### 10.1 樣本清單

`tests/fixtures/manual_samples.json`（**進版控**）列出每個真實樣本：名稱、來源文件 sha256、頁碼清單、用途、產生方式。產生腳本 `tests/fixtures/make_manual.py`：依 sha256 從資料庫 `documents`（工作副本）或 `--source <sha8>=<path>` 找來源，抽頁寫進 `tests/fixtures/manual/`（**不進版控**）。

| 名稱 | 來源 | 頁 | 用途 |
|---|---|---|---|
| `ortho_p1-80.pdf` | 骨科書 `ed486cbb…` | 1–80 | MinerU 頁界、空白頁、目錄頁、同步 e2e |
| `ortho_p300-340.pdf` | 同上 | 300–340 | running header／頁碼／表格／公式 |
| `ortho_blanks.pdf` | 同上 | 41, 42, 空白, 空白, 43, 空白 | 空白頁標記 |
| `hfad_cover_p1.pdf` | Head First Android（sha256 見清單） | 1 | T-001：FULL_PAGE 救不了的真實封面 |
| `hfad_garbled_mix.pdf` | 同上 | 1, 16, 17, 25, 31, 61, 108, 20, 45 | 偵測（7 壞 + 2 對照）與修復 |

另有**進版控的合成 fixture**（`make_fixtures.py` 產生，無版權問題）：`paged_furniture.pdf`（8 頁：running header＋頁碼、目錄頁含點線導引、1 空白頁、跨頁段落、圖頁）與 `broken_tounicode.pdf`（2 頁：PyMuPDF 內建 `cjk` 字型 Identity-H 並刪除 `/ToUnicode` 的句子 + 正常 Helvetica 段落；第 2 頁只在頁尾用壞字型，作為對照）。

### 10.2 驗收模式

- `AIDOC_REQUIRE_MANUAL=1`：需要真實樣本的測試在樣本缺檔時 **fail**（訊息指出 `make_manual.py` 指令），需要引擎環境的 slow 測試在 `envs/<engine>/.ready` 缺少時也 **fail**；未設定時兩者都 skip。
- 測試一律透過 `tests/manual.py` 的 `manual_sample(name)`、`require_engine(name)` 取得，不得自行 `skipif`。既有 `test_manual_fixtures.py` 改用此 helper（並保留「資料夾內所有檔都轉成 ok／warn」的行為）。
- 驗收指令：`AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow tests/integration -q`。

### 10.3 慢測試斷言（真實引擎）

| 測試 | 斷言 |
|---|---|
| MinerU 頁界（ortho p1-80、p300-340、blanks） | 標記數 = 頁數、遞增、空白頁有標記；去掉標記後 == MinerU `markdown.md`；`page_map.method == "mineru_render_plan"`；對齊 ratio ≥ 0.90；品質 `ok` |
| MinerU 頁界（合成 `paged_furniture.pdf`） | 同上；目錄頁、running header 不影響 |
| T-001 偵測（`hfad_garbled_mix.pdf`，強制 Docling、關閉修復） | 被標記頁恰為原第 1、16、17、25、31、61、108 頁（mix 內第 1–7 頁），對照頁未被標記 |
| T-001 修復（`hfad_cover_p1.pdf`、`hfad_garbled_mix.pdf`，Docling 主引擎） | 修復後封面含 `Wouldn't it be dreamy`；mix 內第 4、5、6 頁含 `with all these different devices`、`there's some major`、`java code`（忽略大小寫、`’`→`'`）；`pages_unrepaired == 0`；等級 `ok`；`repaired_by` 以 `docling:` 開頭 |
| 合成 `broken_tounicode.pdf`（自動路由） | 第 1 頁被標記、第 2 頁（只有頁尾用壞字型）未被標記；1/2 頁 = 50% > 20% → Docling 嘗試記 `pages_flagged` → 整份改 MinerU，輸出含 `Wouldn't it be dreamy`（逐頁修復路徑另由假引擎測試以「1 壞頁 + 9 正常頁」的 10 頁變體驗證） |
| MarkItDown 逐頁（`text.pdf`、`ortho_p300-340.pdf`、`ortho_blanks.pdf`，強制） | 標記數 = 頁數、`method == "markitdown_per_page"` |

### 10.4 左右同步準確度 e2e

- `web/e2e/sync.spec.ts`（一般 e2e，假引擎）：轉換 `big.pdf`（45 頁，假引擎每頁輸出含該頁文字層摘錄的區段），開 Document 檢視，以固定種子抽 20 個頁碼：用「跳至頁」→ 斷言左側最上方可見頁 = N、右側最上方頁錨點 = N；再以捲動左側到第 N 頁 → 右側跟到 N；再捲右側 → 左側跟到 N。**20/20 全對**才通過。
- `web/e2e/sync-real.spec.ts`（驗收模式）：對一個以真實引擎轉好的 `ortho_p1-80.pdf` 文件（`AIDOC_E2E_REAL_BASE_URL` + `AIDOC_E2E_REAL_DOC_ID`；`AIDOC_REQUIRE_MANUAL=1` 時缺少即 fail）做同樣 20 頁跳轉：位置 **20/20**；另以 pdfjs-dist 在 Node 讀第 N 頁文字層，取第 7 節的唯一探針，斷言右側第 N 頁錨點之後、下一錨點之前的文字含探針——**≥ 18/20**（容許跨頁段落）。

## 11. 既有資料修復（D）

1. **自動評估**：server 啟動恢復（主 spec §8.4）之後、以及 `POST /documents/rescan` 時，對 `status ∈ {ok, low, warn}`、輸出存在、`page_check` 缺少或過舊的 PDF 文件執行 `assess_existing()`：讀 Markdown、算 `page_map`（期望 = `documents.pages`）、對齊抽查與逐頁訊號（來源 PDF：原始檔或工作副本；兩者皆不在 → 只算覆蓋率與不需閘門的 `garbage`，`page_map.alignment = null`），把結果寫回 `documents.quality_json` 與 `status`（`page_map.method = "legacy"`）。**不改輸出檔與 sidecar**（輸出只由轉換產生）。啟動時在背景執行緒做，不擋 server 啟動；每份文件一筆 log。
2. **一鍵重新轉換**：Library 的「重新轉換」／「全部重新轉換」→ `POST /documents/reconvert`。
3. **預期結果（依 spike 數字）**：

| 文件 | 評估後 | 處置 |
|---|---|---|
| 骨科書（1192 頁） | `low`：`page_map_incomplete`（0.025）、`page_map_misaligned`（0/29） | 重新轉換（來源為工作副本，保留 7 天——**需在到期前執行**） |
| Head First Android（532 頁） | `warn`：55 頁 `broken_text_layer` 未修復（10.3% < 20%） | 重新轉換 → 逐頁修復 → `ok` |
| `text.pdf`（MarkItDown） | `low`：`page_map_incomplete`（0/3） | 重新轉換（Docling 路由） |
| 1 頁 MinerU 空白文件 | 已是 `low` | 可不處理 |
| 其他 PDF | `ok`（覆蓋 1.0、對齊 ≥ 0.96） | 無 |

## 12. 驗收標準

| # | 項目 | 標準 |
|---|---|---|
| 1 | 頁碼覆蓋 | 所有新轉換的 PDF（三個引擎、任何樣本）`page_map.coverage == 1.0`，空白頁有標記；ortho p1-80／p300-340／blanks、`paged_furniture.pdf` 實測 |
| 2 | MinerU 內容不變 | 去掉標記後與 MinerU `markdown.md` 逐位元組相同（`method == mineru_render_plan` 時）；標題／表格／公式／圖數量相同 |
| 3 | 對齊 | 真實樣本 ratio ≥ 0.95（spike：0.966–1.000）；+1 位移陰性對照 ≤ 0.10（spike ≤ 0.024） |
| 4 | 品質攔截 | 無標記或錯位的 PDF 輸出一定是 `low` 並觸發備援（單元＋可靠性測試涵蓋：假引擎 `no_pages`、`misaligned` 情境） |
| 5 | T-001 偵測 | `hfad_garbled_mix.pdf` 7/7 命中、2/2 對照不命中；Head First 全書重新評估 55/55 命中、誤報 0；其他 4 份長文件 0 命中 |
| 6 | T-001 修復 | 封面含 `Wouldn't it be dreamy`；Head First 全書重新轉換後 `pages_unrepaired == 0`、等級 `ok` |
| 7 | 同步 e2e | 假引擎 20/20；真實樣本位置 20/20、內容 ≥ 18/20 |
| 8 | 真實樣本關卡 | `AIDOC_REQUIRE_MANUAL=1` 且刪掉任一樣本 → 對應測試 fail（不是 skip） |
| 9 | 既有資料 | rescan 後骨科書與 `text.pdf` 帶 `page_map_incomplete`、Head First 帶 `page_quality`；三者重新轉換後無 flag；骨科書全書 1192/1192 標記、對齊 ≥ 0.95 |
| 10 | 成本 | 品質評估（含對齊抽查）≤ 10 s／1000 頁；MinerU 逐頁渲染額外成本 ≤ 1 s／40 頁段 |
| 11 | 回歸 | `uv run pytest`、`pnpm --dir web test`、`pnpm --dir web e2e` 全綠 |

## 13. 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| docvortex 內部函式在 MinerU 升級後改名／改行為 | 方案 P 失效 | `uv.lock` 鎖版；runner 自我驗證（逐位元組相同）失敗即改走公開 API 單頁渲染，仍 100% 覆蓋，`method` 與 log 會顯示；升級 MinerU 的 PR 必須跑 §10.3 慢測試 |
| MarkItDown 內部函式（`_extract_form_content_from_words` 等）改變 | 逐頁擷取失敗 | 自我驗證失敗 → 無標記 → `low`（顯性失敗，不會靜默 ok） |
| DEFAULT plan 跨頁段落使 1–3% 頁的標記落在段落之後 | 同步在少數頁偏移數行 | 已量化；對齊門檻 0.90 有餘裕；若被逐頁修復的頁接在跨頁段落後，段落尾巴可能重複一次（只發生在 MinerU 文件的修復頁） |
| 壞字型閘門漏掉「有 ToUnicode 但對映錯誤」的字型 | 該類亂碼只靠逐頁 `garbage` 抓（18/55 的敏感度） | 列為已知限制；之後可評估不經閘門、門檻更高的位移解碼版本，需另外量測誤報 |
| 修復頁風格與主引擎不同（A） | 少數頁 Markdown 風格不一 | B 為主；A 僅在 B 失敗時用 |
| 逐頁修復增加耗時 | Head First 55 頁約多 1–2 分鐘 | 只修被標記頁；> 20% 直接整份換引擎 |
| 工作副本 7 天後清除 | 上傳的文件（骨科書）無法重新轉換 | rescan 結果出來即提示；Library 對 `410 source_missing` 顯示「需要重新上傳原始檔」 |
| 真實樣本不進版控 | CI 無法跑真實關卡 | 驗收在 Windows 開發機以 `AIDOC_REQUIRE_MANUAL=1` 執行並記錄於 PR；合成 fixture 在一般 CI 涵蓋主要邏輯 |
| 新增 `warn` 等級 | 舊前端／CLI 只認 `ok`／`low` | API 欄位為新增；CLI `--json` 與 manifest 照實輸出 `warn`；skill 說明 `warn` 的意義 |

## 14. 對主 spec 與 plan index 的修訂

- 主 spec §3「各 engine 的頁界資訊來源」表：MinerU 列改為「由 `middle_json.json` 以 MinerU 官方 render plan 逐頁渲染，逐頁加標記（第 6.1 節）」；MarkItDown 列改為「PDF 逐頁擷取（第 6.3 節）」。「對不上時該段視為無頁界」刪除。
- 主 spec §4 品質檢查：加入第 5 節的逐頁模型與 `warn` 等級；全部工具失敗時保留最高分仍成立。
- 主 spec §5 sidecar：加入第 5.4 節欄位；`probe` 加 `broken_fonts`、`broken_font_pages_count`。
- 主 spec §6：Library／Document 依第 9.3、9.4 節；API 依第 9.2 節。
- 主 spec §10：加入第 10 節真實樣本關卡與同步 e2e。
- plan index：`models.QualityResult.level` 加 `"warn"`、`DocStatus.warn`、`ProbeResult.broken_fonts`／`broken_font_pages`、`RawResult.page_map_method`、runner `result.json` 的 `page_map_method`、`engine_opts` 的 `backend`／`ocr_mode`、§8 API 新增列、Store 新增方法、§2 新增 A19（`warn` 等級語意）。詳細文字見實作計畫的「Index amendments」，由計畫 Task 0 寫入 index。

## 15. 驗收紀錄（2026-10-01，Windows 開發機，RTX 5070 Ti）

實作偏差與門檻調整的證據見實作計畫末節「Implementation notes / deviations」。重點：逐頁修復在「沒有備援引擎」時（強制引擎或最後一個引擎）一律執行；`has_table_lines` 改為需要格線；段內快速對齊檢查改為整段逐頁、門檻 0.50（只攔重大錯位），整份文件仍為 30 頁抽查、0.90；`page_map_misaligned` 另需至少 2 頁錯位。

### 既有資料修復（live library，`POST /api/documents/reconvert`）

| 文件 | 修復前（rescan `?all=1`） | 修復後 | 頁碼標記 | 對齊（30 頁抽查） | 對齊（全頁） | 耗時 |
|---|---|---|---|---|---|---|
| A System of Orthopaedic Medicine, 3rd Edition（1192 頁） | `low`：`page_map_incomplete` + `page_map_misaligned`，30/1192 標記，未修復 1162 | `ok` 1.000，MinerU（`mineru_render_plan`），flags 無 | 1192/1192 | 29/29 = 1.000 | 1122/1157 = 0.970 | 739 s |
| Head First Android Development（532 頁） | `warn`：55 頁 `broken_text_layer` 未修復 | `ok` 0.996，Docling；`pages_flagged` 55、`pages_unrepaired` 0（50 頁 `docling:pypdfium_full_page_ocr`、5 頁 `mineru:ocr`）；封面含「Wouldn't it be dreamy」 | 532/532 | 20/20 = 1.000（排除 339 頁壞字型頁） | 130/134 = 0.970 | 3766 s |
| 3 份 1 頁空白文件（blank.pdf 系列） | `low`：`page_map_incomplete` 0/1 | `low`（`chars_per_page`，本來就是空白），1/1 標記，flags 無 | 1/1 | — | — | — |
| `text.pdf`（MarkItDown，上傳檔） | `low`：`page_map_incomplete` 0/3 | **未修復**：`410 source_missing`（工作副本已過期、無本機原始檔），需重新上傳 | — | — | — | — |

rescan 後 `page_map_incomplete` 5 份、`page_quality` 6 份（含上表全部）；修復後 library 只剩 `text.pdf` 帶 flag（原因：來源已不存在）。其他 PDF 重新評估皆 `ok`（Neo4j 266/266、Agentic-CN 390/390、long315 315/315）。

### 測試

| 項目 | 結果 |
|---|---|
| `uv run pytest -m "not slow" -q` | 437 passed |
| `AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow -q` | 53 passed，0 skipped（13 min） |
| `pnpm --dir web test` | 106 passed（28 files） |
| `pnpm --dir web build` | ok |
| `pnpm --dir web e2e` | 6 passed（含 `sync.spec.ts`：20 次跳頁 + 10 次左捲 + 10 次右捲，40/40） |
| `pnpm --dir web e2e:real`（`ortho_p1-80.pdf`，MinerU，live server） | 位置 20/20、內容 19/19 |
| 真實樣本關卡（`test_page_quality_real.py`） | ortho p1-80 29/29、p300-340 30/30、blanks 3/3、paged_furniture 7/7 對齊；封面與 mix 由 `docling:pypdfium_full_page_ocr` 修復、`pages_unrepaired` 0；改名一個樣本 → 測試 **fail**（非 skip） |
| T-001 偵測（既有 Head First 輸出） | 55/55 命中、誤報 0；其他輸出 0 命中 |
| 成本 | 1192 頁全頁對齊抽查 9.8 s（文字層抽取為主）；30 頁抽查約 5 s |
