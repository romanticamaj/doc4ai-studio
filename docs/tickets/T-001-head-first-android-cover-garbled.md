# T-001 Head First Android Development：封面轉譯出現亂碼

- 狀態：已併入 `docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md`（逐頁品質模型；實作計畫 `docs/superpowers/plans/2026-10-01-per-page-quality-and-page-map.md`）
- 建立：2026-10-01
- 回報者：站長
- 嚴重度：中（內容遺失但不影響其餘文字；品質檢查誤判為 `ok`，使用者不會被提醒）
- 影響範圍：不只封面。本書 532 頁中 **55 頁**有同類亂碼（約 5,700 字元，主要是 Head First 風格的手寫體旁白／對話框）；`out/` 其餘 17 份文件未發現同類問題。根因在 PDF 字型＋Docling 解析層，任何「Type0/Identity-H 字型且沒有 ToUnicode」的 PDF 走 Docling 都會中。

## 現象

文件 `Head First Android Development`（document id `7f4377ad34d94438b3f44fa64efa3060`，engine `docling`，532 頁，品質 `ok`，score 0.971，`garbage_ratio` 0.0048）在 Document 檢視中，封面頁的轉換結果出現亂碼。

- 輸出：`out/Head First Android Development/`
- 來源工作副本：`data/work/5b32c9a3b5d94c96bfb4d19e1feff2d0/src.pdf`

封面（`<!-- page: 1 -->`）實際輸出：

```
## Head
  First Android
  Development

:RXOGQ·W�LW�EH�GUHDP\�LI� WKHUH�ZDV�D�ERRN�RQ�$QGURLG� GHYHORSPHQW�WKDW�FRXOG�WXUQ�PH� LQWR�DQ�H[SHUW�ZKLOH�NHHSLQJ�PH� HQJDJHG�DQG�HQWHUWDLQHG"�%XW�LW·V� SUREDEO\�MXVW�D�IDQWDV\���
```

封面上的正確文字（對話框，Comic Sans 字型）：

> Wouldn’t it be dreamy if there was a book on Android development that could turn me into an expert while keeping me engaged and entertained? But it’s probably just a fantasy...

亂碼特徵：

- 碼點：`003A 0052 0058 004F 0047 0051 00B7 0057 FFFD 004C 0057 FFFD …`
- 不是 mojibake（不是 UTF-8 被當 cp1252 解碼）、不是 PUA、不是 OCR 雜訊。
- 是**固定位移 29 的「字形 ID 當成字元碼」**：每個字元 +29 即還原（`:`0x3A→`W`0x57、`R`→`o`、`X`→`u`…）。這是 TrueType 標準 Mac 字形順序（glyph 3 = space，glyph 36 = `A`）。空白是 glyph 3 → 控制字元 → 被轉成 `U+FFFD`；`’` 變成 `·`。
- 封面底部 `Beijing • Cambridge • Farnham • Köln • …`（Arial-ItalicMT）同樣壞掉，原始輸出中被 Docling 判成頁尾而丟掉，故未顯示。
- 同書其他頁也有，例如 p25 `:LWK�DOO�WKHVH�GLIIHUHQW� GHYLFHV�`（With all these different devices）、p31 `7KHUH·V�VRPH�PDMRU…`（There’s some major…）、p6/p21 `MXVW�RQH�SODWIRUP!`（just one platform!）；p16/p17 是另一支字型、位移不同（`0MZM¼[�\\PI\\�?-�LQL"`）。

## 調查紀錄

### 1. PDF 文字層本身就是壞的（PyMuPDF 檢查封面）

`page.get_fonts()` + 字型物件：

| 字型 | 類型 | Encoding | ToUnicode | 封面用途 |
|---|---|---|---|---|
| `TIMORR+AmericanTypewriter` | TrueType（simple） | — | **有** | 書名、作者 → 正常 |
| `TDTIQB+ComicSansMS` | Type0 / CIDFontType2 | Identity-H（CIDToGIDMap 串流） | **無** | 對話框 → 亂碼 |
| `EUHPTK+Arial-ItalicMT` | Type0 / CIDFontType2 | Identity-H | **無** | 城市列 → 亂碼 |

- 封面文字是**向量文字**（不是圖片），另有 2 張圖（人物照、O'REILLY logo）。
- PyMuPDF `get_text()` 抽出的也是同一串：`:RXOGQ�PW\x03LW\x03EH\x03GUHDP\\\x03LI…`（空白是 `\x03`），證明**文字層本身沒有可用的 Unicode 對映**，不是 Docling 獨有的錯。
- 渲染（PyMuPDF、pypdfium2 皆可）畫面完全正確——字形本身沒問題，只是「字形→Unicode」對映缺失。
- 全書掃描：沒有 ToUnicode 的字型共 8 支（`ComicSansMS`、`Arial-ItalicMT`、`ArialMT`、`MarkerFelt-Thin`、`SwisterSwister`、`SkippySharp`、`Wingdings2`、`Webdings`），出現在 **339 / 532 頁**（多數只用在頁尾、符號，Docling 輸出時被當 furniture 丟掉或字數極少）。

### 2. 各層責任

- **PDF 文字層**：根源（Identity-H 且無 ToUnicode）。
- **Docling 解析（docling-parse 7.21.0 / docling 2.130.0）**：照字元碼直接輸出，glyph 3 被轉為 `U+FFFD`。
- **我們的 normalize（`src/aidoc/normalize.py`）**：未參與，沒有任何字元替換。
- **品質檢查（`src/aidoc/quality.py`）**：沒攔下（見第 4 節）。

### 3. OCR 能不能救？（只跑封面 1 頁 PDF，真實引擎 venv）

重現：用 PyMuPDF 抽出第 1 頁存成單頁 PDF，以 `envs/docling/.venv`、`envs/mineru/.venv` 直接呼叫 `docling_runner.build_converter(...)` / `mineru.parser.parse(...)`，輸出到 scratchpad。

| 設定 | 對話框輸出 | 結果 |
|---|---|---|
| Docling 預設（文字層，與正式轉換相同） | `:RXOGQ·W�LW�EH�GUHDP\�LI…` | 亂碼（重現） |
| Docling `OcrMode.FULL_PAGE` + RapidOCR（cht 或 en） | `: RX0GQ·WφLWEHIGUHDP\iLI WKHUHIZDV…` | **仍是亂碼**（OCR 分數 0.957） |
| Docling `PyPdfiumDocumentBackend`，不 OCR | `:RXOGQ·WLWEHGUHDP\LI…` | 亂碼 |
| Docling `PyPdfiumDocumentBackend` + `FULL_PAGE` + RapidOCR | `Wouldn't it be dreamy if there was a book on Android development … just a fantasy...` | **正確** |
| MinerU 4.x `ocr_mode="auto"`（runner 預設） | `Wouldn't it be dreamy if there was a book …` | **正確** |
| MinerU `ocr_mode="ocr"` | 同上 | **正確** |

關鍵發現：**Docling 的 FULL_PAGE OCR 救不了**。docling-parse 7.x 的 threaded backend 不用 pdfium 出圖，而是「用同一份 decode 結果自己畫頁面圖」（見 `docling/backend/docling_parse_backend.py` 註解 *"The threaded parser renders the page image from this same decode"*）。缺 ToUnicode 的字會被它以錯誤字元＋替代字型畫出來，OCR 讀到的影像本身就是 `RX0GQ…`（已存圖比對：pdfium 渲染正確，docling-parse 渲染出亂碼）。所以要在 Docling 內用 OCR 修，必須同時把 backend 換成 `PyPdfiumDocumentBackend`。MinerU 預設 `auto` 模式就會偵測壞掉的文字層並改用 OCR。

### 4. 為什麼 `quality.py` 沒攔下

- 規則只算 `U+FFFD`／PUA／控制字元／Latin-1 mojibake，而這種亂碼**九成以上是正常的 ASCII 字母**（只是位移 29），只有空白變成的 `U+FFFD` 會被算到。
- 而且是**全書平均**：封面單頁 garbage 0.12（> 0.05 門檻），但被 532 頁、26 萬字稀釋成 0.0048。
- 逐頁算的話也只有封面會超標；其他 54 頁 `U+FFFD` 佔比僅 1–5%，現行門檻也抓不到。

### 5. 全部輸出掃描（`out/*/*.md`，逐頁）

| 文件 | 頁數 | 結果 |
|---|---|---|
| Head First Android Development | 532 | **55 頁**有 `U+FFFD` 串接的位移亂碼，合計約 5,736 字元：1, 5, 6, 16, 17, 21, 25, 30, 31, 42, 46, 61, 69, 107, 108, 117, 122, 136, 140, 144, 174, 179, 188, 192, 203, 210, 223, 228, 232, 234, 243, 263, 290, 292, 305, 316, 332, 333, 349, 364, 373, 393, 399, 406, 412, 447, 458, 460, 471, 473, 476, 489, 490, 499, 515 |
| A System of Orthopaedic Medicine, 3rd Edition | 30 段 | 無亂碼；僅 `U+E073`（符號字型的項目符號）×30，屬外觀小問題，另案 |
| 其餘 16 份（Agentic-Design-Patterns-CN、Neo4j、long315、scanned_* 等） | — | 無 |

掃描條件：逐頁 `garbage_ratio` > 0.05、`U+FFFD` ≥ 3、PUA ≥ 3、mojibake 樣式（`Ã`、`â€`、`Â`）≥ 3、ASCII 母音比例過低／+29 位移後母音比例回到正常。

## 根因

PDF 裡的 Comic Sans 等手寫體字型以 Type0／Identity-H 嵌入、**沒有 ToUnicode 對映**，文字層只有字形 ID；Docling（docling-parse）把字形 ID 直接當字元碼輸出（固定位移 29、空白變 `U+FFFD`），我們的 normalize 沒有改動它。品質檢查只看全書平均的 `U+FFFD`／PUA／控制字元比例，這類亂碼大多是正常 ASCII 字母，且被 532 頁稀釋，所以判成 `ok`；即使走 Docling 全頁 OCR 也救不了，因為 docling-parse 是用同一份錯誤 decode 畫頁面圖給 OCR 看。

## 建議修法

1. **逐頁亂碼偵測（必要）**：在 `quality.py` 加逐頁評估（以 `<!-- page: N -->` 切頁），除現有字元類別外，加「文字層壞掉」訊號：
   - `U+FFFD` 夾在非空白字元之間（`\S�\S`，glyph-3 空白特徵）；
   - Latin 文字母音比例過低（英文約 0.38，位移亂碼 < 0.2），可再驗證「整體位移 N 後母音比例回到正常」；
   - 逐頁 garbage_ratio 用頁內比例，不再被全書稀釋。
   - 取捨：母音啟發式只適用拉丁語系，需避開程式碼／表格頁（誤判會造成不必要的重跑）；建議以「≥ 2 個訊號」或最低字數門檻降低誤報。
2. **probe 加字型檢查（便宜、決定性）**：用 PyMuPDF 列出「Type0／Identity-H 且無 ToUnicode」字型與使用頁，寫進 probe。全書約 30 秒。單獨用會過寬（本書 339 頁有此類字型，但真正輸出亂碼的只有 55 頁，多數只在頁尾／符號字型），適合與第 1 點交集：「有壞字型 **且** 文字有亂碼」才判定。
3. **逐頁 OCR 回退（修正內容）**：對被判定的頁抽出成小 PDF 重跑，再把結果換回原頁區段：
   - 選項 A：MinerU（`ocr_mode="auto"` 已實測封面正確）——不用改引擎設定，但兩個引擎的 markdown 風格會混在同一份文件。
   - 選項 B：Docling 改用 `PyPdfiumDocumentBackend` + `OcrMode.FULL_PAGE`（已實測正確）——風格一致；**不能只開 FULL_PAGE**（預設 docling-parse backend 仍會餵亂碼影像給 OCR）。
   - 取捨：逐頁重跑增加耗時（本書 55 頁），需處理頁內圖片編號與 page marker；也可先只做「整份降級 low + reasons 標出頁碼」的保守版，讓使用者決定是否重轉。
4. **sidecar 加逐頁品質**：`quality.pages_flagged: [{page, reason, garbage}]`，Document 檢視可在該頁顯示警示；`level` 在有頁被標記時至少降為 `warn`／`low`，避免再出現「品質 ok 但有亂碼」。
5. 測試：以本書第 1 頁（Comic Sans 對話框）做成 fixture，斷言偵測命中且回退後含 `Wouldn't it be dreamy`。

## 相關

- 品質檢查的亂碼規則：`src/aidoc/quality.py`
- Docling runner：`src/aidoc/engines/runner/docling_runner.py`（目前 `FULL_PAGE` 只改 `ocr.mode`，未換 backend）
- docling-parse 自繪頁面圖：`envs/docling/.venv/Lib/site-packages/docling/backend/docling_parse_backend.py`
- 頁碼與逐頁品質系統性修正：`docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md`
