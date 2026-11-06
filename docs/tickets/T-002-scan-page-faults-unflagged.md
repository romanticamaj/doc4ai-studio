# T-002 掃描頁（無文字層）的頁面錯誤沒有被標出：文件仍報 `ok`、0 頁被標

- 狀態：**已修正**（2026-10-22）。偵測：`src/aidoc/scanchecks.py`，接進 `quality.assess` 與 `pipeline.check_document`；根因修正：`mineru_runner.undrain`。
- 建立：2026-10-22
- 回報：真實樣本評估（2026-10-22），驗收時由協調者列為最重要的後續。
- 嚴重度：高。內容遺失或錯頁，品質層級卻是 `ok`、`pages_flagged` 0，使用者不會被提醒，也不會觸發修復或換引擎。

## 現象

以下三個例子都是無文字層的掃描，品質都是 `ok`、0 頁被標：

| 引擎 | 文件 | 實際情形 |
|---|---|---|
| MinerU | `zh-kaohsiung-court-1973` | 第 2、3、7、8 頁的區段是空的；第 1 頁 774 字（正解 121），第 6 頁 1098 字（正解 435） |
| MinerU | `en-aashto-usrn-1984` | 第 5 頁是空的，它的表格落在第 4 頁下（2264 字，正解 1341） |
| Docling | `en-christian-ker-lastwords` | 第 3 頁被截到 63 字，它的文字落在第 2 頁（1589 字，正解 856） |

sidecar 範例：`{"level": "ok", "score": 1.0, "pages_flagged": 0, "page_map": {"coverage": 1.0, "alignment": {"decidable": 0}}}`。

## 根因

1. **頁面檢查全部依賴文字層。** `quality.page_checks` 的 `page_dropped`／`page_misaligned`／`page_oversized`／`page_partial` 都拿區段去比 PDF 文字層，還要求 body 層至少 120 字（`DROPPED_LAYER_MIN`）；對位抽查 `spot_check` 也要 120 字。掃描頁沒有文字層，所以每一項都被跳過。只剩亂碼、引擎自報的失敗頁、缺頁標記與文件層級字數這幾項，而這幾例都碰不到。
2. **MinerU 的頁歸屬（kaohsiung、aashto 的直接原因）。**
   - MinerU 的 `middle_json` 其實把每個區塊放在正確的頁上（`page_idx` 0…n−1，每頁的區塊都有自己的文字）。
   - 但標了 `continues_prev` 的區塊會被 docvortex 的 DEFAULT render plan 併到這條鏈**開始**的那一頁。kaohsiung 的 p2–p3、p7–p8 整頁都被標成延續，結果併進 p1、p6；aashto p5 的表格延續 p4 的表格，被併進 p4。
   - runner 只要 plan 串起來與 `markdown.md` 逐位元組相同就採用它（本來的設計，spec 2026-10-01 §4：段落尾巴跨頁約 1–3 %）。這兩份文件都符合，所以被吞掉的頁就留下空標記。
3. **Docling 在 christian 上**也是頁歸屬錯誤（p3 的文字被輸出在 p2 下），屬引擎行為，本 ticket 只偵測、不修 Docling。

## 流程缺口

1. 合成 Tier A 有 8 份掃描（`syn-scan-zh-*`、`syn-scan-mixed-*`），但：
   - 頁面檢查的設計與門檻只在有文字層的頁上校準過；
   - `bench/calibrate.py` 的 gate v2 明確把掃描列排除在外（「scans reported separately」）；
   - 負樣本 `syn-dropped-pages` 只有文字層版本。

   所以「掃描頁被丟或錯頁」從未被測過，`ok` 也從未被質疑。
2. 頁面標註集（`page_labels.json`）的 476 列裡，只有 4 列是無文字層頁，全是 `clean`：這個盲區在標註時就沒人看見。
3. 品質層級的語意是「沒發現問題」，但對掃描來說其實是「沒辦法檢查」。這兩者沒有被區分，也沒有任何層級會因此提醒使用者。

## 修正

### 1. 不需要文字層的頁面檢查：`src/aidoc/scanchecks.py`

**量什麼**（`page_ink()`，只量文字層少於 20 字的頁）：

- 每頁以 72 dpi 灰階渲染，扣掉 3 % 頁邊。
- 墨水：比頁面自己的紙色（90 百分位灰階）× 0.55 更暗的像素，所以泛黃紙不算墨水。
- 行距：在 6 條窄條帶中量列／欄墨水剖面，避免歪斜把行抹平。
  - 走向：取「沿著字走」的那個方向（帶狀較長者）：橫排是列、直排是欄。
  - 有 3 行以上：取自相關的**第一個**強峰（峰值 ≥ 最強峰 × 0.5，不取倍頻）作為行距，且不小於帶厚。
  - 少於 3 行：帶厚 × 1.6。
- 字格數：cells = 墨水 ÷（行距² × 0.12）。字的大小互相抵消，所以 96 pt 標題頁的字格少，9 pt 密排頁的字格多；字格數追得上字數，不管字級。
- 墨水超過 35 % 的頁是圖片（封面照片），不當作文字頁判。

**判什麼**（`scan_page_checks()`）：

- **`page_empty_scan`**：區段少於 20 字，而該頁至少 20 個字格、且至少是文件中位數的 10 %。會提高層級，修復方式同 `page_dropped`。
- **掃描頁的錯頁**：以文件中位數的「字數÷字格」為 r，下列三項都成立時，第 i 頁標 `page_oversized`、那一串鄰頁標 `page_misaligned`：
  - 第 i 頁的字數 ≥ 1.5 × r·cells，且多出至少 40 字；
  - 相鄰一串頁的字數 ≤ 0.5 × 各自的預期；
  - 多出的量與缺少的量在 0.5–2 倍之間互相吻合。

  只看「密度不均」不夠：密排頁旁邊的標題頁本來就不均，所以要求兩邊的量互相吻合。

每筆結果都帶 `metrics.scan = true`。

### 2. 接進修復與換引擎（同 `page_dropped`）

- `PAGE_REASONS` 加入 `page_empty_scan`。
- `pipeline.check_document` 會修復掃描頁，包括錯頁的**兩邊**：對每頁影像重新 OCR，不可能把文字帶到別頁，所以兩邊都重做不會產生重複文字。文字層頁的錯頁仍然只標不修。
- 未修好的頁計入 `pages_unrepaired`（`warn`）；超過 20 % 時給 `pages_flagged`（`low`），轉下一個引擎。
- `PAGE_CHECK_VERSION` 改為 6，已存的 PDF 會重新評等。`verify` 與重新評等（reassess）都走 `assess(pdf=…)`，一併生效。
- 文字層 PDF 不需要任何渲染：已知每頁都有文字層時，`page_ink` 直接回傳。

### 3. MinerU 根因：`mineru_runner.undrain()`

- plan 被採用後，如果某頁的 plan 少於 20 字、但它單頁渲染有文字，代表它被吞了。這條鏈（從開始頁到最後一個被吞的頁）改用單頁渲染，每頁拿回自己的文字。
- 只把段落尾巴併過一個頁界（沒有頁被吞）的情形照舊。
- 在真實輸出上：

  | 文件 | 修正前（每頁字數） | 修正後 |
  |---|---|---|
  | kaohsiung | 1153 / 0 / 0 / … / 1098 / 0 / 0 | 530 / 113 / 438 / 457 / 376 / 432 / 428 / 393 / 72 |
  | aashto p4 / p5 | 2690 / 0 | 1296 / 1202 |

  runner 日誌會記下重新渲染的鏈（例如 `[(1, 3), (6, 8)]`）。

## 門檻凍結（2026-10-22）

- 依據：設計集，`bench/scan_calib.py`。
- 凍結：commit `8cd45a9`，由 `tests/unit/test_scan_checks.py::test_thresholds_are_frozen` 釘住。

**合成 Tier A 掃描**：8 份、235 個內容頁，含歪斜、雜訊、泛黃等退化。

| 模擬情境 | 案例數 | 召回 |
|---|---|---|
| 忠實轉換（= 正解） | 235 頁 | 誤報 **0 頁** |
| 整頁空白 | 235 | **99.2 %**（233） |
| 下一頁文字併到本頁 | 222 | **97.8 %** |
| 截短且其餘併到前頁 | 222 | **97.8 %** |
| 兩頁併進一頁 | 209 | **100 %** |

所有情境中，標到預期頁以外的頁都是 0。

**設計標註頁**（`page_labels.json` 的 17 份文件）：判了 18 個無文字層頁（其中 4 頁有標註、全是 `clean`），**0 個標記**。

過程中依設計集修過的地方：

- 行距改用條帶剖面：歪斜頁原本讀成 5 倍行距；
- 取第一個強峰，不取最強峰：倍頻；
- 空白頁門檻 0.25 → 0.10 × 中位數：短的末頁；
- 加入圖片頁上限 0.35：設計標註集 ortho2 p1 的封面照片墨水 56 %，合成文字頁最高 19 %。

## 真實樣本回歸集（資訊，只看一次，未據以調整任何門檻）

- 執行方式：在凍結的門檻上，對 2026-10-22 run 的三個設定輸出各跑一次（`bench/scan_calib.py --realeval`）。這些輸出是 MinerU 修正前產生的。
- 真值：同一 run 的 `pages.jsonl`。某頁字袋 recall < 0.5 視為「丟失」，precision < 0.5 視為「膨脹」。
- 結果：330 頁中 **TP 37、FN 16、FP 14、TN 263**（精確率約 0.73、召回約 0.70）。

逐例：

| 引擎 | 文件 | 結果 |
|---|---|---|
| MinerU | kaohsiung | p2、p3 `page_empty_scan`；p6 `page_oversized` 配 p7、p8 `page_misaligned`。全部吞頁都抓到；p1（併了 p2–p3）沒有標 oversized |
| MinerU | aashto | p5 `page_empty_scan` 抓到；p4 沒有標 oversized |
| Docling | christian-ker | 標了 p3／p4、p10／p11 兩對。真正的錯是 p2 吞了 p3：p3 標對，p4 的 oversized 是誤報 |

14 個誤報集中在版面特殊的頁：1702 小冊的 p3／p4 在三個設定都被標（大型首字、標題頁），以及 ncl、roc1912 的少數頁。

這些數字是資訊：門檻凍結在設計集上，不因回歸集調整。

## 已知限制

- 字格只是估計：同一文件內混排不同字級或字體（大標題配小字、附註）時，密度會偏，錯頁判斷可能誤報；所以要求「多出的與缺少的量互相吻合」，而且只會使層級變成 `warn`、觸發修復。
- 漏抓「多出」的那一頁（kaohsiung p1、aashto p4）時，吞頁仍然會被 `page_empty_scan` 抓到並修復，但多出的那頁會留著重複的文字。
- 沒有做 OCR 探測（協調者列為可選）。字格估計在設計集上已足夠，OCR 探測要多啟動一個引擎 runner，留待需要時再做。

## 驗證

- 單元測試：
  - `tests/unit/test_scan_checks.py`：17 項，含字格、紙色、四種錯誤、標題頁、圖片頁、文字層頁（不渲染）、凍結門檻；
  - `tests/unit/test_scan_checks_pipeline.py`：7 項，涵蓋 `assess`、`verify`，以及假引擎的吞頁修復、不可修時 `warn`、錯頁兩邊都修且文字不重複；
  - `tests/unit/test_mineru_pagemap.py`：吞頁鏈、表格延續、段落尾巴保留、空白頁保留。
- 慢速測試 `tests/integration/test_mineru_pages_env.py::test_continued_blocks_no_longer_drain_whole_pages`：在回歸樣本 kaohsiung、aashto 上（spec 2026-10-22 §8.2，只作回歸）。修正前兩份都失敗，修正後通過。
- 真實樣本只作回歸用，不作任何決策。
