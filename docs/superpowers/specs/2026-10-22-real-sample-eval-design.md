# 真實樣本評估（繁中掃描路由、公式路由、本機 VLM 後端重測）— 設計文件

- 日期：2026-10-22
- 狀態：**預先登錄（pre-registered），尚未執行**（2026-10-22 凍結前修訂 A：§5.1 數學展開；B／B′：§7.0／§7.3.2 有效性分範圍；C：§6.6 正解重建；D：§8.4 一次性登錄與曝光紀錄）。本文件的指標、正規化、門檻與決策規則在任何真實樣本被轉換之前凍結；執行紀錄填在 §14。
- 依據：使用者 2026-10-20 核准的兩個待決事項（見 §1.1）；`docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` §3.4（指標）、§10.2（合成基準跑指出的路由問題）、§11（已知限制）；`docs/superpowers/specs/2026-10-05-vlm-ocr-engine-design.md` §3.4（spike 決策規則 1–4）、§5（路由）、§13 #6（本機後端驗收）、§15；`docs/superpowers/spikes/2026-10-vlm-ocr.md`（PaddleOCR-VL-1.6 的量測與「後續值得做」段）；`docs/superpowers/plans/2026-10-05-vlm-ocr-engine.md` Task 8a／8b／8c。
- 樣本：`tests/fixtures/manual/realeval/`（**gitignored，永不進版控**）的 `INDEX.md` 與各資料夾 `SOURCE.md`。
- 實作計畫：`docs/superpowers/plans/2026-10-22-real-sample-eval.md`
- 程式碼位置（計畫實作）：`bench/realeval/`（不屬於 `aidoc` 套件、不進 wheel）。本評估**不改** `src/aidoc/` 任何一行；它只產生決策，決策觸發的改動各自另開計畫。

> **一句話：** 合成 benchmark 說「繁中掃描該走 Docling、公式該走 MinerU、PaddleOCR-VL 其實很好」，但證據只有 5 個合成掃描、2 份合成公式與一個重複四次同一段落的合成頁。這份文件在看到任何數字之前，寫死用哪 12 份真實文件、怎麼正規化、怎麼算分、超過哪條線才改路由或才算本機 VLM 過關——跑一次、照實記、不夠就寫「證據不足，不改」。

## 1. 背景、目標、非目標

### 1.1 背景：兩個待決事項與現有證據的薄弱處

| # | 待決事項 | 合成基準的訊號（verify spec §10.2） | 證據的問題 |
|---|---|---|---|
| (1a) | 繁中掃描的自動路由要不要從 MinerU → Docling 改成 Docling → MinerU | `syn-scan-zh ×5`：`auto`（= MinerU）NED 0.68（最差 0.39），Docling＋RapidOCR 0.96（最差 0.93） | 5 份合成掃描，同一支腳本的退化方式（Augraphy），同一套字型（Noto Sans CJK TC）、全部橫排。真實掃描是直排、鉛字、油印、蓋章、泛黃、塗黑 |
| (1b) | 公式文件的自動路由要不要改 | `syn-formula ×2`：`auto`（= Docling）公式 NED 0，MinerU 0.84 | 2 份合成公式（matplotlib mathtext 貼圖）。**注意**：`router._rule` 現在就把 `probe.math_hint` 為真的 PDF 送 MinerU 優先（`pdf_math`）；合成樣本走到 Docling，表示它們根本沒被判成 `pdf_math`（mathtext 不帶 TeX 數學字型名）。所以這題其實有兩層：MinerU 的公式優勢在真實論文上成立嗎？偵測器在真實論文上會不會觸發？ |
| (2) | 本機 PaddleOCR-VL-1.6 後端（llama.cpp）要不要在真實掃描上重測 | spike 規則 1 只敗在 `scanned_cht.pdf`（同一段 47 字重複四次，模型只輸出三次 → recall 0.75）；真實頁 0.95–1.00、平均 1.15 s/頁、峰值顯存 +2.4 GB、封面句命中 | 規則 1 的三個合成掃描裡有一個是人造的重複段落；真實頁的對照正解是 MinerU 的 `ok` 輸出，不是人工正解 |

### 1.2 目標

1. 用 12 份有人工／來源正解的真實文件，**一次**回答 (1a)、(1b)、(2)。
2. 每個答案只有三種：**改**（並寫明改什麼）、**不改（現狀確認）**、**證據不足，不改**（並列出還需要哪些樣本，§11）。
3. 規則、門檻、樣本數下限在跑之前寫死（§7）；跑完只准填數字，不准調。

### 1.3 非目標

- 不是公開 benchmark、不發表數字表到 README 的「實測」節（verify spec §3.6 的閘門與這份評估無關）；不與任何外部 benchmark 比。
- 不調任何引擎參數、不改 prompt、不改 `probe._is_math`、不改 `router.py`——本評估結束時 `src/aidoc/` 與開始時逐位元組相同（§13 #1）。（2026-10-22 例外：凍結前的 loopback 不走 proxy 隱私修正，見 §8.4；零程式影響自凍結 commit 起算。）
- 不評估 `Table Recognition:` 兩段式模式、不評估 Qwen2.5-VL、不評估遠端 VLM（gemma／qwen 等）。
- 不重新量測 `scanned_cht.pdf` 的重複段落頁：它維持為**另行記錄的已知限制**（§7.3.4）。
- 不做逐頁人工校對全部 89 頁正解（只做 §6 的抽查）。

## 2. 樣本

資料夾 `tests/fixtures/manual/realeval/`（`.gitignore` 第 15 行 `tests/fixtures/manual/` 涵蓋；harness 啟動時以 `git check-ignore` 與 `git ls-files` 雙重確認，任何一個檔案被追蹤即拒絕執行）。每份文件的 URL、授權、正解方法與注意事項以該資料夾的 `SOURCE.md` 為準；下表只摘要決策會用到的欄位。sha256 為 `original.pdf`／`paper.pdf` 的值（harness 逐檔驗證，不符即該文件 `missing`、不計入）。

### 2.1 A 組：繁中影像掃描（決策 (1a) 與 (2) 的主體；6 份、89 個正解頁）

| 資料夾 | 內容 | PDF 頁 | 正解頁 | 正解等級 | 版面 | 注意 |
|---|---|---|---|---|---|---|
| `zh-kaohsiung-court-1973` | 1973 高雄地院函＋判決，打字／油印，章、雜訊；p1 為表單 | 9 | 9（1–9） | 3 | 直排 | GT 用 `{{Pre2}}` 保留印刷換行；發文日期章由抄錄者從檔案推定 |
| `zh-ncl-renjian-cihua` | 王國維《人間詞話》，國家圖書館掃描，泛黃、館章、墨暈 | 42 | 35（1,3,9–41） | 3 | 直排古籍 | p2,4,42 無文字（level 0）、p5–8 無 Page: 條目 → 這 7 頁**不計分**；`{{專}}` 攤平 |
| `zh-roc1912-gazette02` | 1912 臨時政府公報第 2 號，鉛字 | 18 | 17（1–17） | 3 | 直排、p4／p13 有表 | p18 無轉錄；保留舊字形（衞、爲、甯） |
| `zh-ssid-zhuyin-primer` | 小學生文庫《注音符號初步》，細字、行內注音 | 25 | 22（4–25） | 3 | 橫排、p12 有表 | **已知正解錯誤**：p12 `ㄌㄙ（冷）` 應為 `ㄌㄥ（冷）`（§6.1 勘誤）；抄錄者為大陸用戶，注音以 Unicode ㄅㄆㄇ 編碼 |
| `zh-supreme-court-1972` | 1972 最高法院刑判，打字、雜訊影印、塗黑 | 4 | 4（1–4） | 3 | 直排、**每 PDF 頁 = 兩頁跨頁**（6253×4428 pt） | GT 頁 N = 整個跨頁 N（右頁先、左頁後）；41 MB |
| `zh-taipei-court-1992` | 1992 臺北地院刑判，打字橫排，邊緣行號、塗黑、章 | 2 | 2（1–2） | 3 | 橫排現代 | 邊緣行號 01–31 **不在** GT；塗黑處 GT 留空白 |

共 89 個正解頁、約 60,000 字（GT 檔字元數：10,695／14,606／14,867／8,228／7,812／3,679）。全部 PDF 無文字層（PyMuPDF 抽字 0 字；`zh-ncl` 12 字，為館藏編號之類，`probe.text_ratio` 仍為 0）。

### 2.2 B 組：公式密集的 arXiv 論文（決策 (1b)；4 份、CC BY 4.0）

| 資料夾 | 標題（縮） | PDF 頁 | 正解公式（行間／全部） |
|---|---|---|---|
| `arxiv-2506.00451` | Connected bosonic n-point functions for the BKP hierarchy | 23 | 68／566 |
| `arxiv-2506.00108` | Analogue of Einstein–Gauss–Bonnet theory in 3+1 dimensions | 7 | 18／133 |
| `arxiv-2506.00106` | Gaussian-based distribution algebra … quantum foam | 49 | 191／993 |
| `arxiv-2506.00231` | Screens modeled by Schrödinger operators … C₀ contraction semigroups | 31 | 110／1345 |

正解 = arXiv HTML（LaTeXML）每個 `<math>` 的 `alttext`，依文件順序存成 `gt/formulas.json`（`display: block|inline`, `latex`）。**文件層級、不對應頁碼、機器產生、未人工校對**；沒有 e-print 原始碼（robots.txt 禁止自動抓取）。行內項目含大量單一符號（≤ 3 字元者：116／5／68／55 個），所以**只有 `block`（行間）公式用於決策**（§5.5）。

### 2.3 C 組：英文掃描對照（2 份、正解等級 4 = 已驗證）

| 資料夾 | 內容 | PDF 頁 | 正解頁 | 注意 |
|---|---|---|---|---|
| `en-aashto-usrn-1984` | 1984 AASHTO 路線編號委員會報告，打字、無框表格 | 5 | 5 | **PDF 帶 OCR 文字層**（8,505 字）；表頭在 `<noinclude>`、不在 GT |
| `en-christian-ker-lastwords` | 1702 蘇格蘭小冊，長 s（ſ） | 16 | 16 | **PDF 帶 OCR 文字層**（28,692 字）；GT 以 U+017F 編碼長 s |

對照組的用途：(i) 在等級 4 的正解上驗證 harness 與正規化政策沒有系統性偏差（§7.0 有效性檢查）；(ii) 給決策 (2) 補英文頁。**兩份都必須先渲染成無文字層的影像 PDF 再送引擎**（§4.3），否則文字層會把答案漏給引擎。

### 2.4 另外用到的既有樣本

- `tests/fixtures/manual/hfad_cover_p1.pdf`（私有、gitignored）：決策 (2) 的「封面句命中」準則（spike 規則 1 原文），目標句 `Wouldn't it be dreamy`。
- `tests/fixtures/scanned_cht.pdf`、`scanned_mixed.pdf`、`page.png`、`bench/corpus/small/`：**只供 harness 開發與乾跑**（§8），不是決策樣本。

## 3. 受測設定

| 設定 | 指令 | 用途 |
|---|---|---|
| `auto` | `aidoc convert <pdf> --force --json`（自動路由 + 逐頁修復） | 「使用者今天拿到的結果」；同時記錄 `tried`／`engine_used`／路由理由，確認現行路由實際走到誰 |
| `docling` | `--engine docling`，`AIDOC_BENCH_NO_REPAIR=1` | 單引擎、出廠設定（RapidOCR、`lang` 見下）、**修復關閉** |
| `mineru` | `--engine mineru`，`AIDOC_BENCH_NO_REPAIR=1` | 單引擎、出廠設定（`basic` tier、`ocr_mode auto`）、**修復關閉** |
| `vlm-local`（只對決策 (2)） | 不經 `aidoc`：harness 直接對 loopback 的 `llama-server` 送 Chat Completions（§5.6） | 與 spike 規則 1 相同的量法，可直接對照 |

- `markitdown` 不跑（無 OCR，對掃描輸出空白；對公式無意義）。`auto-norepair` 不跑（路由問題由單引擎欄回答，修復貢獻不是本評估的問題）。
- 語言：A 組 `--lang cht`（預設）；C 組 `--lang en`（`docling_ocr = rapidocr` 時 §13.2 例外不生效，路由與 cht 相同，記錄即可）。
- 每個設定獨立 `AIDOC_DATA=<out>/data-<config>`、輸出根 `<out>/out-<config>`、`AIDOC_CONFIG=<out>/aidoc-<config>.toml`（`[vlm] backend = "none"`），模型目錄 `AIDOC_MODELS` 指回原處——沿用 `bench/run.py` 的 `convert()`／`write_config_files()`／`env_for_config()`，不重寫。
- 逾時：`bench/run.py._timeout()`（`max(900, 30 s × 頁數)`）。逾時或失敗的 (設定, 文件) 以 **NED 0** 計入並列在報告（不略過；略過會讓分數虛高）。若失敗原因是 harness 本身（例如逾時值明顯不足、磁碟滿），該配對**可重跑一次**並記錄（§8.3 有效性重跑的唯一例外）。
- 環境紀錄 `env.json`：沿用 `bench/run.py.environment()` 欄位（OS、GPU、驅動、各 env `uv.lock` sha256、引擎版本、`aidoc` commit、`aidoc_dirty`），另加：本文件與 harness 的 commit、12 份樣本與兩份衍生影像 PDF 的 sha256、`gt_quality.json` 的 sha256（§6）、llama.cpp／GGUF 三個檔案的 sha256（§5.6）、`nvidia-smi` 基線顯存。

## 4. 樣本前處理（在任何引擎執行之前完成，全部可重現）

### 4.1 載入與驗證
`bench/realeval/MANIFEST.json`（進版控；只有檔名、sha256、頁數、正解頁清單、等級、語言、角色，**不含任何文件文字**）。harness：sha256 不符 → `missing`；資料夾被 git 追蹤 → 拒絕執行。

### 4.2 正解頁對應
A／C 組：`gt/page-NNN.txt` 的 NNN = PDF 頁碼（SOURCE.md 已目視抽查；§6.2 驗證者再查每份文件首尾頁）。轉換輸出以 `aidoc.pagemap.split_pages()` 依 `<!-- page: N -->` 切頁，第 N 段對 `page-NNN.txt`。**沒有正解的 PDF 頁不計分**（`zh-ncl` 的 7 頁、`zh-roc1912` 的 p18）；**有正解但輸出沒有該頁的，pred 視為空字串（NED 0）**。最高法院跨頁：PDF 頁 N 的輸出段對 GT 頁 N（整個跨頁），不切半。

### 4.3 對照組去文字層
`render_image_only(original.pdf, dpi=300) → en-*/derived/image_only.pdf`：每頁 `page.get_pixmap(dpi=300)` → PNG → 新 PDF 同尺寸頁 `insert_image`（與 `tests/fixtures/make_fixtures.make_scanned_pdf` 同法）。斷言衍生檔 PyMuPDF 抽字 **= 0 字**且 `probe_file().text_ratio == 0`，sha256 寫入 `env.json`。送引擎與送 VLM 的都是衍生檔。

### 4.4 正解勘誤（§6.1）
`gt_errata.json` 套用到**計分用的 GT 複本**；原始 `gt/` 不動。

## 5. 正規化與指標

### 5.1 文字正規化政策（A／C 組；兩側——GT 與 pred——完全相同的函式）

順序固定：

| 步 | 規則 | 說明 |
|---|---|---|
| 1 | pred 先**展開數學**（2026-10-22 修訂 A，見本節末），再過 `bench.metrics.plain()` | 去 HTML 標籤、Markdown 標記、圖片連結、HTML 註解（= `quality.strip_markup()`）；~~去 `$…$` 數學（另計）~~ → 數學只去定界符、保留內容 |
| 2 | 勘誤（只對 GT） | §6.1 |
| 3 | 注音聲調符號移除（兩側） | U+02C7 ˇ、U+02CA ˊ、U+02CB ˋ、U+02D9 ˙（GT 與引擎對聲調的編碼都不穩定，不計） |
| 4 | 撇號統一（兩側） | U+2019 ’、U+02BC ʼ → U+0027 '（封面句比對與英文對照用；之後反正被步 5 丟掉） |
| 5 | `aidoc.pagemap.squash()` | NFKC → 去軟連字號與行尾連字 → 小寫 → **只留 `isalnum()` 為真的字元（任何文字系統）** |

政策結果（已逐字元驗證過 NFKC 行為）：

- **空白**：全部消失。直排 GT 的換行與印刷欄位不對應（SOURCE.md 全部如此）→ 不影響分數。但**閱讀順序仍然計分**：引擎把右欄左欄順序弄反，Levenshtein 會重罰。兩個引擎同樣受罰；§5.4 的字袋 F1 用來分辨「認錯字」與「排錯序」，**只做診斷、不決策**。
- **長 s**：NFKC 把 U+017F ſ 映成 s；ﬁ（U+FB01）→ fi。引擎輸出 s 不算錯。
- **全形**：全形數字／拉丁字母 NFKC 成 ASCII（`１`→`1`）；相容表意文字歸一。
- **標點**：中英文標點、頓號、引號、省略號（含 GT 裡「重複冒號狀字形」）、GT 表格的 ` | ` 分隔、邊緣 `|`——全部不是 `isalnum` → **不計分**（與 verify spec §3.4 的 NED 定義一致）。
- **注音**：ㄅ–ㄩ（U+3105–U+312F）是 `Lo` → `isalnum()` 為真 → **保留並計分**（它們是初步課本的內容；OCR 認不出注音就是失分）。
- **簡繁**：**不做任何折疊**。GT 是 `臺`、pred 是 `台` → 一個替換錯誤；`這`／`这` 同理。（Qwen 系列會混出簡體，spike 已見；本政策就是要罰這個。）
- **舊字形／異體**：`衞`／`衛`、`爲`／`為`、`甯`／`寧`、`裏`／`裡` 等**不折疊**，算錯；§5.4 診斷估其佔比。
- **數字〇、漢字數字**：`〇`（U+3007，Nl）為 alnum → 保留。
- **不做任何「針對某份文件」的 pred 過濾**：臺北地院的邊緣行號、各文件的頁首頁尾／頁碼、館章文字、塗黑處的雜訊——引擎吐出來就是插入錯誤，兩個引擎同罰。這是預先登錄的取捨（不替任何引擎擦屁股）。

> **2026-10-22 修訂 A（凍結前，協調者決定；`bench/realeval/normalize.unwrap_math`）**：原步 1 用 `plain()` 把 pred 裡所有 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 整段刪除，而掃描組沒有另計數學——若某引擎（MinerU）習慣把數字、圈號或中文包進行內數學而另一個（Docling）不包，被包的字只對前者算漏字，Δ 會偏向 `change`。修訂後：A／C 組（含 VLM 回應）**只去數學定界符、保留內容**；內容若是 LaTeX 命令（如 `\frac`、`\mathrm`、`\text`、`\alpha`），**去命令名、保留引數的字元**（`\frac{1}{2}` → `12`；`\{`、`\%` 只去反斜線），之後照步 1–5。所有引擎同一規則。B 組（公式）不受影響：公式仍由 §5.5 從 `$…$` 抽出計分，護欄 `T` 的 pred 文字仍是 `plain(md)`。釘住的測試：`tests/unit/test_realeval_normalize.py::test_math_wrapped_digits_and_cjk_score_like_plain_text`（MinerU 式 `$1973$`、`$\text{臺北}$`、`$\frac{1}{2}$` 與 Docling 式純文字得分相同）。

### 5.2 頁級指標（A／C 組）

| 指標 | 定義 | 用途 |
|---|---|---|
| **NED**（主指標） | `bench.metrics.ned(gt, pred)` = `1 − Levenshtein.normalized_distance(squash(gt), squash(pred))`；pred 空而 GT 非空 → 0；兩者皆空 → 1（不會發生：空 GT 頁不計分） | 決策 (1a) |
| CER | `bench.metrics.cer()` = 編輯距離 ÷ `len(squash(gt))` | 報告 |
| 字袋 recall／precision／F1 | 字元多重集合：`common = Σ_c min(n_gt(c), n_pred(c))`；recall = common ÷ Σ n_gt、precision = common ÷ Σ n_pred | recall 是決策 (2) 的指標（= spike 的「character-multiset recall」）；F1 對 (1a) 只做診斷 |
| dropped | `squash(pred)` 非空白字元 < 20 而 GT 非空（`bench.metrics.DROPPED_SECTION_MAX`） | 報告 |

文件層級：**頁加權平均**（每頁權重 1；不是字元加權——與 verify spec §3.4 一致）。另列每份文件最差頁。

### 5.3 配對比較與不確定性（決策 (1a)）

對每個計分頁 p：`d_p = NED_docling(p) − NED_mineru(p)`。

- `Δ` = 所有計分頁 d_p 的平均（頁加權）。
- 95 % 信賴區間：**頁級配對 bootstrap**，重抽 `BOOTSTRAP_N = 10,000` 次、`random.Random(20261022)`、百分位法（2.5／97.5）。stdlib 即可，不裝 scipy。
- 文件層級：`Δ_doc` = 該文件兩引擎頁均 NED 之差；`wins_D` = `Δ_doc ≥ +0.02` 的文件數；`wins_M` = `Δ_doc ≤ −0.02` 的文件數；`big_loss_D` = `Δ_doc ≤ −0.05` 的文件數、`big_loss_M` 對稱。
- 頁在同一份文件內不獨立，所以 bootstrap 的 CI 偏窄；因此 §7.1 同時要求文件層級的勝場數（兩個條件都要過，任一不過即「證據不足」）。

### 5.4 診斷（報告，不決策）

- 字袋 F1 與 NED 的差：差距大 → 排序問題（直排欄序、表格 cell 序）。
- 異體／簡體佔比：pred 與 GT 的替換錯誤中，有多少對落在固定對照表（`bench/realeval/variants.json`：異體 ≈ 30 對，進版控）或——若環境有 `opencc`（Apache-2.0，選用）——落在 t2s 對照。沒裝就省略該欄。
- 每份文件最差 3 頁的頁碼與 NED（不貼文字）。

### 5.5 公式計分（B 組；文件層級）

- **GT**：`gt/formulas.json` 中 `display == "block"` 的 `latex` 清單 G（68／18／191／110 條）。行內公式只在診斷欄報告 recall。
- **pred**：整份輸出 Markdown（所有頁串接）以 `bench.metrics.formulas_in()` 抽出 `$$…$$`、`$…$`、`\[…\]`、`\(…\)` 內容 → 清單 P。
- **正規化 `norm_latex_doc()`** = `bench.metrics.norm_latex()`（去 `\left`／`\right`、間距命令、`\displaystyle`、`\mathrm`、`\operatorname`、全部空白）再加（預先登錄、固定清單）：去 `\label{…}`、`\tag{…}`、`\tag*{…}`、`\nonumber`、`\notag`、`\begin{…}`／`\end{…}`（任何環境名）、對齊符 `&`、換行 `\\`；單一字元的大括號 `{+}`→`+`、`{=}`→`=`（LaTeXML alttext 的寫法）；`\mbox{…}`／`\text{…}`／`\textrm{…}` 保留內容去命令；`\boldsymbol`／`\mathbf`／`\mathcal`／`\mathbb` 去命令保留內容（字體不計）。
- **配對**：對 G 中每條 g，在 P 中找尚未被用掉、`1 − normalized_distance(norm(g), norm(p))` 最高者（greedy，與 `bench.metrics.formula_ned` 同法）。
- **指標**：
  - `R`（**主指標**）= **行間公式回收率** = 最佳配對 NED ≥ `FORMULA_MATCH = 0.80` 的 g 佔比。
  - `S` = 最佳配對 NED 的平均（連續分數，報告）。
  - `R_inline`（診斷）= 行內公式以同法算的回收率。
- **文字護欄 `T`**（確保換引擎不會為了公式犧牲本文）：GT 文字 = `paper.html` 去掉 `<math>`、`<script>`、`<style>` 後的可見文字（`html.parser`），`squash`；pred 文字 = `plain(md)` 後 `squash`；`T` = GT 字元 5-gram 多重集合在 pred 中的 recall（與 `quality` 的涵蓋率同一種量法）。只比兩引擎的差，不解讀絕對值。
- 記錄 `probe_file(paper.pdf).math_hint`、`auto` 的 `RouteDecision.reason`（`pdf_math`／`pdf_default`）與 `engine_used`。
- 已知限制：LaTeXML 把 `align` 多行方程併成一條 alttext，而引擎可能輸出多個 `$$`；只配對到其中一條 → 低估 R（兩引擎同受影響）。

### 5.6 本機 VLM 量測（決策 (2)；與 spike S1 同法）

- **執行檔與模型**（釘死；sha256 必須與 spike 表相同，否則 `insufficient`）：llama.cpp **b11559** Windows CUDA 13.4 x64 zip（`419de129…294a`）＋ cudart zip（`738f8c25…6e8`）；`PaddleOCR-VL-1.6-GGUF.gguf`（BF16，`f3ae46ec…ee8`）＋ `PaddleOCR-VL-1.6-GGUF-mmproj.gguf`（`204d757d…58a`）。完整 URL 與 sha256 見 spike 文件表格。
- **啟動**：`llama-server -m <gguf> --mmproj <mmproj> --host 127.0.0.1 --port <free> -ngl 99 -c 8192 --temp 0 -np 1 --no-ui`；`-v` 日誌須出現 `using device CUDA0` 與全部 18 層 `assigned to device CUDA0`，否則 `insufficient`（沒在 GPU 上跑就不是規則 1 要量的東西）。
- **頁面渲染**：PyMuPDF 200 dpi、長邊 ≤ 1400 px、PNG、data-URI；A 組直接渲染（本來無文字層），C 組渲染衍生檔；**不送任何文字層**。
- **請求**：單一 user 訊息 = 影像 + 文字 `"OCR:"`（`paddle_tasks` 風格），`max_tokens 4096`，`temperature 0`，每頁一請求、不並行。`finish_reason == "length"` 的頁照拿到的部分計分並列出（不重試——規則 1 原本也沒有重試）。
- **指標**：
  - 每頁字袋 recall（§5.2）對 §5.1 正規化後的 GT；**文件 recall = 該文件所有計分頁 common 之和 ÷ GT 字元之和**（字元加權；與 spike 每樣本一個數字的用法對齊）。
  - 每頁秒數 = 該請求的牆鐘（不含渲染、不含模型載入；模型載入到 `/health` 200 另記）。
  - 峰值顯存 = `bench.gpu_sampler.Sampler`（`nvidia-smi` 每 0.5 s）在整個 VLM 階段的 `delta`（峰值 − 啟動 server 前的基線）。基線包含其他 GPU 程式；執行前盡量關閉並記錄基線值。
  - 封面句：`hfad_cover_p1.pdf` 第 1 頁同法送出，回應經 NFKC＋撇號統一＋小寫後含 `wouldn't it be dreamy` → 命中。
- **privacy**：端點主機必須是 loopback（`ipaddress.is_loopback` 或 `localhost`；沿用 `bench/run.py._check_vlm_endpoint()`），否則 harness 退出。

## 6. 正解品質：勘誤、抽查、信心

### 6.1 勘誤（跑之前凍結）
`tests/fixtures/manual/realeval/gt_errata.json`（gitignored）：`[{doc, page, wrong, right, source}]`。初始一條：`zh-ssid-zhuyin-primer` p12 `ㄌㄙ（冷）` → `ㄌㄥ（冷）`（SOURCE.md 已記）。驗證者在 §6.3 抽查中發現的錯誤**可以**加入——它們來自掃描影像對 GT 的比對，與任何引擎輸出無關，所以不是在「調」；但**凍結時間點 = 第一個引擎開始轉換之前**，之後發現的錯誤只能寫進紀錄、不能再改計分。勘誤條目（頁碼與數字元）會出現在紀錄裡。

### 6.2 頁對應檢查（驗證者）
每份 A／C 文件：目視第一個與最後一個正解頁對 PDF 同頁號（12 次），最高法院另確認「右頁先、左頁後」與 GT 順序一致。不符 → 修正 `MANIFEST.json` 的頁對應（這是前處理，不是調整），記錄。

### 6.3 文字正解抽查（驗證者；量 GT 錯誤率）
- 抽樣：A 組每份文件 2 頁，`random.Random(20261022)` 在該文件的正解頁清單上 `sample(…, 2)`，共 `GT_SPOT_PAGES = 12` 頁（約 1,800–2,400 字）。harness 的 `gt_quality.py --draw` 印出頁碼；人不挑頁。
- 判法：對照掃描影像與（套用勘誤後的）GT，逐字數：替換、漏字、多字各算 1；標點、空白、換行不算（與 §5.1 一致）；異體字 GT 照印刷抄即不算錯。每頁記 `k`（錯誤數）與 `n`（GT 計分字元數）。
- 估計：`CER_gt = Σk ÷ Σn`；95 % 上界 = Wilson 區間上界（把字元當 Bernoulli；`bench.page_labels.wilson_lower` 的對稱式）。
- 寫入 `gt_quality.json`：每頁 k/n、合計、`CER_gt`、上界、頁對應檢查結果、勘誤數、抽查者、日期。sha256 進 `env.json`。

### 6.4 公式正解抽查（驗證者）
每篇論文 3 條行間公式（同一種子抽），對照 PDF 渲染頁目視：alttext 是否為該式（容許 LaTeXML 的正規化差異）。12 條中 ≥ 3 條**不是**該式（例如對到別的公式、嚴重截斷）→ 公式 GT 標「低信心」→ 決策 (1b) 直接判 `insufficient`（§7.2）。

### 6.5 信心如何進決策
- GT 錯誤對兩個引擎近似同罰（抄錯的字兩邊都對不上），所以它加雜訊、不加偏差；要求決策差距必須明顯大於雜訊：**`CER_gt` 上界 ≤ `GT_CER_SOFT = 0.025` 時 `ROUTE_MARGIN = 0.05`；上界 > 0.025 時 `ROUTE_MARGIN = 0.10`**。margin 在看到引擎數字之前由 `gt_quality.json` 決定。
- 決策 (2) 的 0.95 門檻**不因 GT 錯誤調整**（使用者要求門檻不變）。recall 的分母是 GT，GT 抄錯一個字大約讓 recall 少一個字的份——紀錄裡在每個 recall 旁標 `CER_gt` 上界，讓讀者看得到這個界，但判定照規則。
- 紀錄的「GT 信心」區塊固定欄位：等級（3／4／機器）、抽查頁數與字數、k、`CER_gt`、上界、頁對應結果、勘誤數、margin 採用值。

### 6.6 2026-10-22 修訂 C：正解重建、margin 機械判定（凍結前；驗證者 Task 9 發現，協調者決定）

- **發現**：原本由抓取腳本把 wikitext 轉成 `gt/page-NNN.txt` 時，丟掉或弄亂了模板內容（`{{UnO}}`、`{{書}}`、`<ref>` 註文混入、`[[w:` 殘留、`{{Page:…}}` 未展開、注音初步的使用者模板未展開造成空頁、`{{ppoem}}`／`{{c}}` 被丟）。驗證者抽查的 12 頁中即有 18 處。
- **處理**：`gt/` 改由進版控、可重現的 `bench/realeval/gt_from_wikitext.py` 從 `gt_raw/` 重建：
  - 印刷文字類模板保留文字（`專`、`書`、`多行合一`、`lsp` 保留全部位置參數；`UnO`、`sic`、`SIC`、`校` 保留第一參數＝印刷字形；版式包裝保留內容）。
  - 註記與間距丟掉（`<ref>`、`{{*|…}}` 錄入者註、`{{?}}`、`gap`、`redact`、`ts`）。
  - 連結取顯示文字。`{{Page:…}}` 轉引與注音初步的使用者模板從本機副本展開：`gt_raw/_external/`、`gt_raw/_templates/`，以 MediaWiki API 禮貌抓取並記下 revision id。
  - `<noinclude>` 內的版面雜項照舊不算正解。
  - 任何未知模板或殘留標記一律報錯。
- **時點**：這是 §4.4／§6.1 允許的前處理。重建時沒有任何引擎看過 6 份繁中掃描，也沒有任何引擎輸出被讀過。
- **重建前後的計分字元數**（`norm_gt` 之後）：

  | 文件 | 重建前 | 重建後 |
  |---|---|---|
  | kaohsiung | 2994 | 3010 |
  | ncl | 3997 | 4250 |
  | roc1912 | 4277 | 4649 |
  | zhuyin-primer | 1939 | 3283 |
  | supreme | 2186 | 2196 |
  | taipei | 1010 | 1010 |
  | en-aashto | 6588 | 6620 |
  | en-christian-ker | 21357 | 22036 |

  逐頁數字見 `gt_rebuild_report.json`（不進版控）；舊版保留在各資料夾的 `gt_v1/`。
- **勘誤重新對位**：`gt_from_wikitext.py --rebase-errata` 對驗證者的 18 條勘誤逐條機械分類：
  - 12 條**被重建取代**（重建後已印出正確文字，證實了驗證者的發現）。
  - 3 條**保留**（印刷與轉錄確實不同：注音 p12 ㄌㄥ、最高法院 p2「林」、公報 p8「卽」）。
  - 1 條**重新對位**（ncl p18：重建印出「點絳唇」，驗證者依掃描讀為「點絳脣」；新 `wrong` 是重建文字中唯一一段編輯距離 ≤ 2 的片段）。
  - 2 條為**整頁轉錄**（公報 p17、注音初步 p11）：依協調者政策，驗證者對掃描的轉錄**優先於**重建結果，以 `replace_page` 套用。

  原檔保留為 `gt_errata.v1.json`。
- **新的共用有效性檢查 `gt_pages`**：任一計分 GT 頁少於 20 字（`norm_gt` 後），或殘留 `{{`、`}}`、`[[`、`<ref`，即不過。不過時真實 run 在轉換前就拒絕。例外清單以 `MANIFEST.json` 的 `short_ok` 明列：只有 ncl 的 p1、p3（書名頁，10、14 字）與 p40（4 字署名頁，「人間詞話終」在頁尾 noinclude）。這三頁經重建確認原文就這麼短，請驗證者重判時對掃描確認。
- **`gt_quality.json` 綁定正解**：`gt_quality.py --summarise` 寫入 `gt_sha256`，即 `gt/` 套用 `gt_errata.json` 後全部計分頁的雜湊。run 開始前比對，不符即拒絕，所以依舊版 GT 判的抽查不能沿用。
- **margin 是機械判定**：重建後，驗證者對**同一批** 12 個抽查頁（種子 20261022，`gt_quality.py --draw`，頁碼不變）重新判 k／n。margin 依 §6.5 由 `gt_quality.json` 的上界直接決定（`rules.margin_for`），不由任何人挑選。
- **已知限制**：注音初步的課文頁由使用者模板展開，模板裡固定的提示句（「這個符號讀做甚麼？」）與印刷可能不同；驗證者已看到 p11 印的是「怎樣讀」，並以整頁轉錄處理。其餘課文頁照模板文字計分，差異兩個引擎同罰。

## 7. 預先登錄的決策規則

常數全部放 `bench/realeval/rules.py`，單元測試逐一釘死數值（計畫 Task 0）；本節是權威版本，程式與本節不符以本節為準並修程式。

### 7.0 共同前提、有效性檢查、三種結論

**最小樣本數**（不足 → 該決策 `insufficient`）：

| 決策 | 下限 |
|---|---|
| (1a) | 兩引擎都有輸出（含失敗計 0 的文件，但至少 `ROUTE_MIN_DOCS = 5` 份文件兩邊都成功轉完）且計分頁 ≥ `ROUTE_MIN_PAGES = 60` |
| (1b) | ≥ `FORMULA_MIN_DOCS = 3` 篇兩引擎都成功轉完；每篇 GT 行間公式 ≥ 15 條（四篇都滿足） |
| (2) | 8 份文件（6 zh + 2 en）全部量到、封面頁量到、顯存抽樣有效（`nvidia-smi` 可用、基線已記） |

**有效性檢查**（跑完立即判；不過 → 整個 run **無效**，修 harness 後可重跑一次並記錄原因；這是 §8 之外唯一允許重跑的情形）：

1. 12 份樣本 sha256 全對、資料夾未被追蹤、兩份衍生影像 PDF 文字層 = 0 字。
2. `env.json.aidoc_dirty == false`，且 `aidoc_commit` 的樹包含本文件與 harness（§8.1 凍結）。
3. 對照組 `en-aashto-usrn-1984`（打字、等級 4）：`docling` 或 `mineru` 至少一個頁均 NED ≥ 0.85——否則 harness／頁對應／正規化有 bug，任何數字不可信。
4. `auto` 在 A 組的 `tried[0]` 必須是 `mineru`（現行 `pdf_scanned` 路由）——不是的話 harness 的設定檔有誤。
5. VLM 階段：server 日誌有 CUDA0 與 18/18 層；端點 loopback；三個檔案 sha256 相符。

> **2026-10-22 修訂 B（凍結前，協調者決定；`bench/realeval/rules.VALIDITY_SCOPE`）**：有效性檢查**分範圍**，取代上段「不過 → 整個 run 無效」：
>
> | 範圍 | 檢查 | 不過的後果 |
> |---|---|---|
> | 共用 | 1（sha256、資料夾未被追蹤、GT 檔齊全、衍生檔 0 字）、2（樹乾淨、commit 含本文件與 harness）、3（對照組 NED；2026-10-22 修訂 B′） | 三個決策全部 `insufficient`（`run invalid (validity checks)`） |
> | 路由 | 4（`auto` 走 `mineru`） | 只有 (1a)、(1b) `insufficient`（`run invalid (route validity checks)`）；(2) 照 §7.3 判 |
> | VLM | 5（CUDA0、18/18 層、loopback、三檔 sha256） | 只有 (2) `insufficient`（`run invalid (VLM validity checks)`）；(1a)、(1b) 照 §7.1／§7.2 判 |
>
> 未列名的檢查一律算共用。重跑規則不變（§8.3：只重跑出問題的部分一次並記錄）。
>
> **2026-10-22 修訂 B′（凍結前，協調者裁定）**：檢查 3（對照組 `en-aashto-usrn-1984` 的 NED ≥ 0.85）改列**共用**。它不過代表 harness、頁對應或正規化有 bug，而這些同樣會污染 VLM 的數字，所以保守地讓三個決策一起 `insufficient`。釘住的測試：`tests/unit/test_realeval_rules.py::test_a_shared_validity_failure_invalidates_every_verdict[control_ned]`、`::test_a_route_validity_failure_leaves_the_vlm_verdict[auto_route]`、常數測試的 `VALIDITY_SCOPE`。

**三種結論**（每個決策只能是其一；由 `rules.decide(results) → decisions.json` 機械判定，人只能加註解）：`change`（含改什麼）、`confirm`（現狀確認，不改）、`insufficient`（證據不足，不改；附 §11 的樣本需求）。

### 7.1 決策 (1a)：繁中掃描路由（`router._rule` 的 `pdf_scanned`，`lang == "cht"`）

現行：`["mineru", "docling"]`。候選：`["docling", "mineru"]`。

令 `Δ`、CI `[lo, hi]`、`wins_D`、`wins_M`、`big_loss_D`、`big_loss_M` 如 §5.3；`margin` 如 §6.5。

| 結論 | 條件（**全部**成立） | 後果 |
|---|---|---|
| **`change` → Docling 優先** | `Δ ≥ margin` 且 `lo > 0` 且 `wins_D ≥ 5`（6 份中）且 `big_loss_D == 0` | 另開計畫：`router._rule` 的 `pdf_scanned` 在 `lang == "cht"` 時改為 `["docling", "mineru"]`；主 spec §4 表、VLM spec §5 表、`test_router.py` 參數化測試更新；修復順序隨主引擎自動變為 B → A → V（逐頁 spec §4 決策 4 不用改）。**`image` 路由不改**（沒有圖片樣本，§11）。 |
| **`confirm` → MinerU 優先不變** | `−Δ ≥ margin` 且 `hi < 0` 且 `wins_M ≥ 5` 且 `big_loss_M == 0` | 不改；verify spec §10.2 的「路由問題」段加一行「真實樣本未確認」 |
| **`insufficient`** | 其餘所有情況（含 `|Δ| < margin`、CI 跨 0、勝場不足、任一方有大敗文件、樣本數不足） | 不改；紀錄寫明哪一條沒過，§11 列需要的樣本 |

- `auto` 欄只報告（含修復後 NED 與 `engine_used`），不進規則。
- 不得由這批樣本導出「直排走 A、橫排走 B」之類的細分路由：沒有偵測器、也等於在 6 份文件上擬合（§8）。

### 7.2 決策 (1b)：公式路由（`router._rule` 的 `pdf_math`）

現行：`math_hint → ["mineru", "docling"]`。對每篇論文 i 有 `R_M(i)`、`R_D(i)`、`T_M(i)`、`T_D(i)`（§5.5）。

| 結論 | 條件 | 後果 |
|---|---|---|
| **`confirm` → MinerU 優先不變，且有真實證據** | 在 ≥ 3 篇上同時：`R_M ≥ FORMULA_RECOVERY_MIN = 0.50`、`R_M − R_D ≥ FORMULA_GAP = 0.30`、`T_M ≥ T_D − TEXT_GUARD = 0.05` | 不改路由。**偵測器子結論**：若 4 篇 `math_hint` 全為真且 `auto` 走 `pdf_math` → 「現行規則在真實 TeX 論文上成立」；若任一篇 `math_hint` 為假 → 另記「偵測器漏判」為後續事項，**修偵測器須用 §11 的新樣本驗證，本評估不改 `_is_math`** |
| **`change` → Docling 優先** | 對稱：≥ 3 篇 `R_D ≥ 0.50`、`R_D − R_M ≥ 0.30`、`T_D ≥ T_M − 0.05` | 另開計畫改 `pdf_math` 為 `["docling", "mineru"]`（實務上 Docling 預設未開公式 enrichment，幾乎不可能；寫出來是為了對稱） |
| **`insufficient`** | 其餘（含兩邊都 < 0.50、差距 < 0.30、護欄不過、§6.4 公式 GT 低信心、樣本不足） | 不改；合成的 0.84 vs 0 標為「真實樣本未確認」 |

### 7.3 決策 (2)：本機 PaddleOCR-VL-1.6 後端——spike 規則 1 在真實掃描上重判

#### 7.3.1 門檻（**與 VLM spec §3.4 規則 1 完全相同，不調**）

| 準則 | 門檻 | 本評估的量法 |
|---|---|---|
| recall | **≥ 0.95** | 8 份文件（6 zh + 2 en 對照）**每一份**的文件 recall（§5.6）都 ≥ 0.95（規則 1 原本是「三個合成掃描每個都要過」，這裡是「八份真實文件每份都要過」） |
| 速度 | **每頁 ≤ 10 s** | 110 頁中**最大**請求牆鐘 ≤ 10 s（spike 報的是 max 4.69 s） |
| 顯存 | **峰值 ≤ 6 GB** | `Sampler.delta` ≤ 6,144 MB |
| 封面句 | **命中** | §5.6 |
| 安裝可重現 | 釘死 URL + sha256 | 三個檔案 sha256 與 spike 表一致 |

#### 7.3.2 結論

| 結論 | 條件 |
|---|---|
| **`pass`** | 五項全過 |
| **`fail`** | 五項都量到，任一不過（紀錄寫明哪一項、哪份文件、數字） |
| **`insufficient`** | 任一項量不到（server 起不來、不在 GPU、`nvidia-smi` 不可用、sha256 不符、某份文件沒量完） |

> **2026-10-22 修訂 B（同 §7.0）**：上表 `insufficient` 的 VLM 設定類原因（不在 GPU、層數不足、sha256 不符、端點非 loopback）**只影響本決策 (2)**，不再經 §7.0 讓 (1a)、(1b) 一併無效；反之，路由範圍的有效性失敗也不影響 (2)。共用範圍的失敗仍讓三者都 `insufficient`。顯存照 §5.6 以 server 啟動前的基線計 delta；基線含桌面程式（量測當下約 4.5 GB）照實記錄，不需關閉。

recall 落在 `[0.95 − CER_gt 上界, 0.95)` 仍是 `fail`——規則照寫；紀錄旁註「在 GT 誤差界內」供讀者判讀，但不改結論。

#### 7.3.3 `pass` 之後要做什麼（**只規劃，本評估不建**）

另開計畫「VLM 本機後端 8a」，內容釘死為：

1. VLM plan **Task 4b**（runner 管理 `llama-server` 生命週期）＋ **Task 8a**（`vlm_runtime.py` 釘死 b11559 三檔 URL/sha256、`selfcheck_vlm.py`、`aidoc setup vlm`）；**不做 8b**。`local_preset = "paddleocr-vl-1.6"`、`prompt_style = "paddle_tasks"` 為預設；`local_supported` 在 Windows x64 + CUDA 為真，其他平台維持現狀直到各自量過。
2. 表格：`"OCR:"` 只給純文字表格 → 寫入已知限制（spike 規則 1 的表格子句：`missing_table` 判 `low` 時由「保留最高分」規則收尾）；`"Table Recognition:"` 兩段式**不啟用**，要啟用須另評估。
3. **重複段落已知限制**：`scanned_cht.pdf` 的 0.75 寫進 README「VLM OCR」節、Settings 卡片說明與 VLM spec §11 已知限制：「同一段文字在一頁內逐字重複出現時，模型可能少輸出一份」。
4. **VLM spec §13 #6 修訂**：本機後端的 recall 驗收改為「本評估 8 份文件各 ≥ 0.95（紀錄為準）＋ `scanned_mixed`／`page.png` ≥ 0.95」；`scanned_cht` 在慢速測試中改為 **strict xfail**（理由字串指向本文件 §7.3.4），不是 skip、不是通過。門檻數字本身不變。
5. 之後任何 prompt、解析度、`ctx` 的調整都要用 §11 的新樣本驗證（§8）。

`fail` → 規則 4 維持（只交付 `openai` 後端），紀錄寫明敗在哪一項；`insufficient` → 同樣不改，列缺什麼。

#### 7.3.4 另行記錄的已知限制（不進 pass/fail）
`scanned_cht.pdf`：同一 47 字段落重複四次，PaddleOCR-VL-1.6 只輸出三次（recall 0.75），各種解析度、裁切、chat template 與 transformers 路徑皆同（spike S1）。真實樣本裡沒有這種頁；本評估不重量它，`pass` 時依 §7.3.3 第 3、4 點記錄。

## 8. 一次性使用與防過擬合

### 8.1 凍結
1. 本文件與 `bench/realeval/`（含 `rules.py` 常數、`MANIFEST.json`、`variants.json`）先 commit；`gt_quality.json`、`gt_errata.json` 由驗證者完成並算出 sha256。
2. 執行時 `env.json` 記下 `aidoc_commit`、`aidoc_dirty`（必須 false）、`gt_quality.json` sha256。驗證者核對 commit 的樹含本文件最終版（§13）。

### 8.2 決策樣本只用一次
- 這 12 份文件（與兩份衍生檔）是**決策樣本**：每個決策只在一次登錄的 run 上判定一次。
- harness 開發、除錯、乾跑只准用 §2.4 列的合成樣本與假引擎（`AIDOC_FAKE_ENGINES`／`tests/fakes/vlm_server.py`）；`--fake` 模式下載入真實樣本的程式路徑直接拒絕。
- run 結束後，這 12 份文件**降為 dev／回歸集**：可以用來確認日後的改動沒有退步，**不可以**再作為任何決策（含 §7.3.3 的後續計畫、偵測器修正、路由細分、prompt 調整）的證據。這些決策各自需要 §11 的新樣本。
- 不得因為看了結果而改 §5 的正規化、§7 的門檻、樣本清單或配對方式。要改，寫成新的 spec、用新的樣本。

### 8.3 重跑
只有兩種允許：(i) §7.0 有效性檢查不過（harness 問題）；(ii) 單一 (設定, 文件) 配對因 harness 原因失敗（§3）。每次重跑在 `env.json.reruns[]` 記原因、時間、影響的配對；紀錄逐條列出。**結果不如預期不是重跑理由。**

### 8.4 2026-10-22 修訂 D：一次性登錄與樣本曝光紀錄（凍結前；驗證者 Task 9 發現，協調者決定）

- **登錄冊 `RUNS.json`**（在樣本資料夾內，gitignored，**不在** `--out` 裡）：第一個真實轉換開始時寫入。
  - 第二個真實 run 不能開始，除非以 `--invalidated-run <id>` 指名一個已登錄、且其 `validity.json` 顯示有效性檢查不過的 run。每個 run 只能被作廢一次。
  - `--rerun-pair` 每個 (設定, 文件) 只准一次，記在登錄冊與 `env.json.reruns`。
  - `--resume` 除了 commit 與 `gt_quality.json`，也比對 `gt_errata.json` 的 sha256。
  - 這把 §8.2「只用一次」從約定變成程式強制。
- **曝光偏差（如實記錄）**：在 verify-benchmark 第 4 輪，獨立驗證者曾把 **4 篇 arXiv 論文與 2 份英文對照**轉換並讀過輸出；**6 份繁中掃描從未被轉換**。本文件 §5–§7 的決策規則由從未看過那些輸出的作者凍結，規則本身不受污染。
- **曝光後的已知風險**：驗證者提醒，`en-aashto-usrn-1984` 的表格可能使對照組檢查（§7.0 檢查 3，NED ≥ 0.85）不過。**不因此改動該檢查**：現在改，本身就是看過輸出之後的調整。若它不過，依修訂 B′ 三個決策都 `insufficient`，並照 §8.3 記錄。
- **凍結前的產品修正**：驗證者發現 urllib／httpx 對 127.0.0.1 也會走 `HTTP_PROXY`。VLM runner 與本機 server client 已改為 loopback 一律直連，評估用的 `vlm_measure` 同樣直連。這次改動在凍結 commit 之前，且與決策規則無關；§13 #1 的「零程式影響」從凍結 commit 起算。

## 9. 隱私：沒有任何頁面離開這台電腦

- 引擎：MinerU、Docling（RapidOCR）在本機 env 執行；MarkItDown 不跑。
- VLM：只接 loopback 的 `llama-server`（或日後的 Ollama／llama.cpp）；harness 以 `_check_vlm_endpoint()` 拒絕非 loopback 主機；`aidoc.toml` 的 `[vlm] backend` 在三個 `aidoc` 設定裡都是 `"none"`。
- 整個 run **不需要網路**：樣本已下載、模型已就位、llama.cpp 與 GGUF 已在本機並 sha256 核過。建議執行時斷網；`env.json` 記下是否斷網（自我宣告）。
- 輸出隔離：`AIDOC_DATA`、輸出根、設定檔都在 `bench/results/<run>/`（gitignored，`.gitignore` 第 35 行）；不碰使用者 Library。
- 進版控的紀錄（§10.2）只有數字、頁碼、檔名、sha256，以及長度 ≤ 30 字元的示意片段（Wikisource 文字為 CC BY-SA 4.0，引用時標 'Wikisource contributors'）。含整頁文字的輸出、逐頁 diff、渲染影像一律留在 `bench/results/`。
- 這些樣本本身是公有領域／CC，規則仍照「私有文件」的標準寫，讓同一支 harness 日後拿私有文件評估時不必改規則。

## 10. 結果紀錄

### 10.1 run 目錄（`bench/results/<YYYY-MM-DD>-realeval/`，不進版控）

| 檔 | 內容 |
|---|---|
| `env.json` | §3 的環境欄位 + 樣本／衍生檔／GT 品質／VLM 三檔 sha256、基線顯存、`reruns[]`、斷網宣告 |
| `pages.jsonl` | 每 (設定, 文件, 頁) 一列：NED、CER、字袋 P/R/F1、dropped、squash 後長度；**不含文字** |
| `results.jsonl` | 每 (設定, 文件) 一列：頁均 NED／CER／F1、最差頁、秒、`engine_used`、`tried`、路由理由、`math_hint`、公式 `R`／`S`／`R_inline`／`T`、狀態、錯誤訊息（截 500 字） |
| `vlm.jsonl` | 每頁：recall、秒、`finish_reason`、tokens；封面句命中；模型載入秒；`Sampler` 基線／峰值／delta |
| `decisions.json` | `rules.decide()` 的輸出：三個決策各自的結論、每個條件的數值與是否成立、採用的 margin、樣本數 |
| `summary.md` | `report.py` 產生：§10.2 的表 + 固定不宣稱文字 |
| `out-<config>/`、`data-<config>/`、`aidoc-<config>.toml`、`run.log` | 原始輸出與資料庫；含文字 |

### 10.2 進版控的紀錄
`docs/superpowers/evals/2026-10-22-real-sample-eval-record.md`，固定章節：環境與凍結資訊（commit、sha256）；GT 信心區塊（§6.5）；表 A（A 組：每份文件 × {auto, docling, mineru} 的頁均 NED／CER／F1、最差頁；最下列 `Δ`、CI、勝場）；表 B（B 組：每篇 × {auto, docling, mineru} 的 `R`／`S`／`T`、`math_hint`、路由理由）；表 C（對照組）；表 V（VLM：每份文件 recall、頁數、最大／平均秒、`length` 頁數；封面句；顯存；載入秒）；`decisions.json` 的三個結論逐條（條件、數值、成立否）；重跑清單；偏差；不宣稱文字（`bench/NONCLAIMS.md` 全文 + 本評估專屬：「6 份繁中文件、4 篇論文，不是 benchmark；正解等級 3／機器產生；時間 n = 1」）。

另外：本文件 §14 填一段摘要；VLM spec §15 與 verify spec §10.2 各加一行交叉參照；README「Benchmark」節加一行指向紀錄（只有連結與三個結論，不放表）。若有 `change`／`pass`，對應的後續計畫檔名寫在 §14。

## 11. 證據不足時需要的額外樣本

| 決策 | 需要 | 來源建議 |
|---|---|---|
| (1a) 繁中掃描 | 再 ≥ 6 份、合計 ≥ 60 頁，**不同來源**，且至少涵蓋：相機拍攝（非平台掃描）、含複雜格線表格的頁、現代橫排鉛印、中英混排；優先 Wikisource **level 4** 的文件 | Wikimedia Commons `PD-ROC-official`／`PD-ROC-exempt` 類別＋ zh.wikisource Index 頁；國家圖書館 NCL 公有領域書（Commons 已有） |
| (1a) `image` 路由 | ≥ 20 張單頁 PNG／JPG（相機、截圖）有正解 | 從上述掃描頁渲染／拍攝 |
| (1b) 公式 — 路由 | 再 ≥ 4 篇 CC BY arXiv，**人工下載 e-print**（robots 禁自動），編譯加頁標記得到**逐頁**公式與文字正解 | arXiv OAI-PMH `license` 欄篩 CC BY |
| (1b) 公式 — 偵測器 | ≥ 10 份**非 TeX** 的公式 PDF（Word／LibreOffice 方程式編輯器、matplotlib／plotly 報表匿出）有來源正解 | 自製（來源檔即正解）＋ 公開教材 |
| (2) VLM | ≥ 20 頁、≥ 5 份含**重複內容**的真實頁（表單、重複標籤的表格、歌詞／經文）以刻畫去重限制；再 ≥ 3 份英文掃描（含手寫批註頁）；Linux／WSL2 一次量測以決定 `local_supported` 的平台範圍 | Commons 公有領域表單；Wikisource level 4 英文 |

## 12. 風險與已知限制

- **閱讀順序**：直排欄序錯誤被 NED 重罰，兩引擎同罰；字袋 F1 只做診斷。若兩引擎都在直排上崩（NED < 0.5），(1a) 大概率 `insufficient`，這是正確結論。
- **正解等級 3**：一次校對；§6 抽查量錯誤率並以 margin 加倍吸收；不假裝是等級 4。
- **6 份文件**：文件層級統計力弱，所以 §7.1 同時要 bootstrap CI 與 5/6 勝場。
- **公式正解是文件層級、機器產生**：多行 `align` 併成一條、巨集展開；§5.5 的正規化只處理固定清單；兩引擎同受影響。
- **塗黑、館章、邊緣行號、頁首頁尾**不在 GT 而引擎會輸出：插入錯誤，兩引擎同罰，不過濾（§5.1）。
- **最高法院 6253×4428 pt 跨頁**：引擎各自決定渲染解析度（可能縮圖）；VLM 路徑統一長邊 1400 px，字會很小——這是真實條件，照量。
- **時間 n = 1**、顯存基線含其他程式；只用於門檻判定（10 s、6 GB），不比較引擎速度。
- **對照組有 OCR 文字層**：§4.3 去除；若衍生檔文字層不為 0，有效性檢查不過。
- **單一機器（RTX 5070 Ti、Windows）**：(2) 的 `pass` 只對這個平台有效；`local_supported` 的平台範圍見 §7.3.3 第 1 點與 §11。

## 13. 驗收標準（給獨立驗證者；逐條打勾，計畫附檢查表）

| # | 項目 | 標準 |
|---|---|---|
| 1 | 零程式影響 | run 前後 `git diff --stat <freeze-commit> -- src/ web/` 為空；`uv run pytest -q` 全綠 |
| 2 | 凍結 | 本文件最終版與 `bench/realeval/` 的 commit 早於 `env.json.date`；`aidoc_dirty == false`；`rules.py` 常數 = §7 數字（測試釘死） |
| 3 | 樣本 | `git ls-files tests/fixtures/manual` 為空；12 個 sha256 與 `INDEX.md` 一致；兩份衍生檔文字層 0 字 |
| 4 | GT 品質 | `gt_quality.json` 由驗證者本人完成、sha256 在 `env.json`；勘誤凍結於第一個轉換之前；margin 採用值與上界一致 |
| 5 | 隱私 | VLM 端點 loopback；三個 `aidoc-*.toml` 的 `[vlm] backend = "none"`；紀錄檔無超過 30 字元的文件文字（測試以長度掃描） |
| 6 | 機械決策 | 以 `uv run python bench/realeval/run.py --decide-only --out <run>` 重算 `decisions.json`，與紀錄逐條相同 |
| 7 | 重跑 | `env.json.reruns` 為空，或每筆都對應 §7.0／§3 允許的原因 |
| 8 | 門檻不變 | 決策 (2) 的 0.95／10 s／6 GB／封面句與 VLM spec §3.4 規則 1 逐字相同；`scanned_cht` 未被重量、已知限制已記 |
| 9 | 紀錄完整 | §10.2 的每個固定章節都有；三個結論都是 `change`／`confirm`／`insufficient`（或 (2) 的 `pass`／`fail`／`insufficient`）之一並附條件數值 |
| 10 | 後續 | 任何 `change`／`pass` 都只對應到「另開計畫」的檔名，本 run 內沒有改 `src/aidoc/` |

## 14. 執行紀錄

（執行後填寫：日期、`env.json.aidoc_commit`、三個結論各一行、紀錄檔連結、後續計畫檔名或「無」。）
