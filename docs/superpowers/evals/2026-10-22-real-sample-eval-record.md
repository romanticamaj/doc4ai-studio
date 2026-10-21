# 真實樣本評估（繁中掃描路由、公式路由、本機 VLM） — 紀錄

run 目錄：`bench/results/2026-10-22-realeval/`（不進版控）。登錄冊 `RUNS.json`：唯一一筆，id `2026-10-22-realeval`，開始於 2026-10-22T03:26:50+08:00，無重跑。

> **結果一句話：這一次登錄的 run 無效。** 共用有效性檢查 3（對照組 `en-aashto-usrn-1984` 頁均 NED ≥ 0.85）沒過：Docling 0.6976、MinerU 0.593。依 §7.0 修訂 B′，三個決策一律 `insufficient`（`run invalid (validity checks)`）。
>
> 沒找到 harness 原因，所以 §8.3 不允許重跑；理由見「評註」。下面各表的數字照實列出，但**都不是結論**。

## 環境與凍結

| field | value |
|---|---|
| date | 2026-10-22T03:26:43+08:00 |
| aidoc_commit | b5fdc3a981c4 |
| aidoc_dirty | no |
| spec / harness commit | 122532f |
| gt_quality.json sha256 | 0cb9b9336aa9ae0a9637d40779c6b77594e4e0b560b8207c1a9f76d01478dbbd |
| GPU | NVIDIA GeForce RTX 5070 Ti |
| offline (declared) | no |
| fake | no |
| sample arxiv-2506.00106 | 3b54409c2337a8e94ce9ebe2625109ebf8caac1fcae13cb0844164c204232f47 |
| sample arxiv-2506.00108 | 9eabc0feef646dbaf5ca2e138def95a30e0f23942044a89d7439feec2a847262 |
| sample arxiv-2506.00231 | f0975c7c12db948266b004a490dacb60e526d55fd093eb3b4718d2b66c4f4481 |
| sample arxiv-2506.00451 | 285990e516332d50dce6dd4faa51f461137fcb8bcdf6298e57cc7f8e8753b6ba |
| sample en-aashto-usrn-1984 | 380aee33a484f39e49e46be98d66b2223e7468a384af644926278733209fc8a1 |
| sample en-christian-ker-lastwords | f3a3a482eab757432b405a1c0d8115f30bfd72801a729408d7ad32742ad6facc |
| sample zh-kaohsiung-court-1973 | 8bc7e28f7b63948e2bb59b87b7c8a1d70b0077720ca3464cc1e5d7ddb1b8ce8d |
| sample zh-ncl-renjian-cihua | e9271e247dc35f4337b3b86b0bdf4d4330c45083533fa6cb1adbc47b7e88302d |
| sample zh-roc1912-gazette02 | 4a1088c513e5c2d8077635dbaa656c0ab9575ba3b5ec9cc26315f2dd6fce2565 |
| sample zh-ssid-zhuyin-primer | c1651f6cff92a94302c49df07e5c65dd75821bd9d428032be2ff5c280f9603ed |
| sample zh-supreme-court-1972 | 53f61b952f99c1b2113b9c973612593d028a926e31ecf7240656447d1aca7a95 |
| sample zh-taipei-court-1992 | 909f7f428ea0748c24417321fc47127f64355f02167858f6b871a0733d39a9c8 |
| derived en-aashto-usrn-1984 | 5a42e535cc70e212c18cdc40f9fc9479ab3669f0e9d8119f14093e7d88f5755c · text_chars 0 |
| derived en-christian-ker-lastwords | ed8325b30f3e54799f48b1448a18ba3f1628cc5cad69c8af4ad041ab183ed929 · text_chars 0 |

## GT 信心

| 欄位 | 值 |
|---|---|
| 等級 | 3（繁中，Wikisource 一次校對）／4（英文對照）／機器（公式） |
| 抽查頁數 | 12 |
| 字數 | 4463 |
| k | 0 |
| CER_gt | 0.0000 |
| 95 % 上界（Wilson） | 0.0009 |
| 頁對應 | yes |
| 勘誤 | 15 |
| 公式 GT 低信心 | no |
| margin | 0.05 |
| 抽查者 / 日期 | independent-verifier / 2026-10-22T03:19:14+08:00 |

## 表 A — 繁中掃描（頁均 NED／CER／字袋 F1，最差頁）

| 文件 | NED auto | NED docling | NED mineru | CER auto | CER docling | CER mineru | F1 auto | F1 docling | F1 mineru | 最差頁 auto | 最差頁 docling | 最差頁 mineru | auto engine | 狀態 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| zh-kaohsiung-court-1973 | 0.092 | 0.124 | 0.092 | 1.644 | 0.876 | 1.644 | 0.307 | 0.821 | 0.307 | p2 (0.000) | p1 (0.041) | p2 (0.000) | mineru | auto:done / docling:done / mineru:done |
| zh-ncl-renjian-cihua | 0.160 | 0.160 | 0.558 | 0.908 | 0.908 | 0.503 | 0.926 | 0.926 | 0.737 | p3 (0.000) | p3 (0.000) | p29 (0.000) | docling | auto:done / docling:done / mineru:done |
| zh-roc1912-gazette02 | 0.573 | 0.120 | 0.573 | 0.471 | 0.896 | 0.471 | 0.793 | 0.919 | 0.793 | p6 (0.045) | p10 (0.062) | p6 (0.045) | mineru | auto:done / docling:done / mineru:done |
| zh-ssid-zhuyin-primer | 0.754 | 0.429 | 0.754 | 0.245 | 0.571 | 0.245 | 0.851 | 0.500 | 0.851 | p24 (0.530) | p19 (0.000) | p24 (0.530) | mineru | auto:done / docling:done / mineru:done |
| zh-supreme-court-1972 | 0.228 | 0.000 | 0.228 | 0.839 | 1.000 | 0.839 | 0.632 | 0.000 | 0.632 | p2 (0.018) | p1 (0.000) | p2 (0.018) | mineru | auto:done / docling:low / mineru:done |
| zh-taipei-court-1992 | 0.922 | 0.786 | 0.922 | 0.078 | 0.221 | 0.078 | 0.963 | 0.906 | 0.963 | p2 (0.854) | p2 (0.687) | p2 (0.854) | mineru | auto:done / docling:done / mineru:done |

Δ (docling − mineru, 頁加權) = -0.3340；95 % CI [-0.4060, -0.2638]；計分頁 89；兩邊皆完成 6 份；wins_D 1、wins_M 5；big_loss_D 5、big_loss_M 0

**不宣稱的事**

1. **不是 OmniDocBench／olmOCR-Bench 的分數，不可與其他專案發表的數字比較**：語料、正規化、指標定義都不同。
2. **合成掃描不等於真實掃描**：退化方式是我們選的；真實掃描的對照只有不可重現的 Tier C。
3. **單引擎欄位是「我們出廠設定下的該引擎」**，不是該引擎的最佳表現；MinerU 用 `basic` tier、Docling 用 RapidOCR、MarkItDown 無 OCR；各引擎在其他設定下可能更好。
4. **這量的是編排層**：`auto` 比單引擎好的部分來自「換引擎 + 逐頁修復」，不代表我們的 OCR 更強——我們沒有自己的 OCR。單引擎與 `auto-norepair` 欄位都關閉逐頁修復（`AIDOC_BENCH_NO_REPAIR=1`）。
5. **時間 n=1**，含模型載入；不同機器不可比；只看相對順序。
6. **`verify` 分數不是品質的絕對量**：它與正解 NED 的相關性寫在校準表裡；相關性只在合成語料上量過。
7. **VLM 欄（若有）的幻覺未被 NED 以外的指標量化**；本機 VLM 不計費用。
8. **Tier C 不可重現**，列出只為說明兩本真書的實際結果，與公開數字分開看。
9. 任何「第一」「最強」「SOTA」字眼都不出現在 README 與文件中。

**本評估專屬**：6 份繁中文件、4 篇論文、2 份英文對照，不是 benchmark；正解等級 3（繁中）／4（英文對照）／機器產生（公式，未人工校對）；時間 n = 1；單一機器（Windows、單張 GPU）。

## 表 B — 公式論文（文件層級）

| 論文 | 設定 | R（行間） | S | T（本文護欄） | R_inline（診斷） | GT 行間數 | math_hint | auto 路由理由 | 狀態 |
|---|---|---|---|---|---|---|---|---|---|
| arxiv-2506.00451 | auto→mineru | 0.956 | 0.952 | 0.861 | 0.066 | 68 | yes | pdf_math | warn |
| arxiv-2506.00451 | docling | 0.956 | 0.949 | 0.840 | 0.008 | 68 | yes | pdf_math | warn |
| arxiv-2506.00451 | mineru | 0.956 | 0.952 | 0.861 | 0.066 | 68 | yes | pdf_math | warn |
| arxiv-2506.00108 | auto→mineru | 0.889 | 0.927 | 0.902 | 0.122 | 18 | yes | pdf_math | done |
| arxiv-2506.00108 | docling | 0.833 | 0.883 | 0.902 | 0.000 | 18 | yes | pdf_math | done |
| arxiv-2506.00108 | mineru | 0.889 | 0.927 | 0.902 | 0.122 | 18 | yes | pdf_math | done |
| arxiv-2506.00106 | auto→mineru | 0.764 | 0.870 | 0.978 | 0.378 | 191 | yes | pdf_math | done |
| arxiv-2506.00106 | docling | 0.686 | 0.826 | 0.963 | 0.000 | 191 | yes | pdf_math | done |
| arxiv-2506.00106 | mineru | 0.764 | 0.870 | 0.978 | 0.378 | 191 | yes | pdf_math | done |
| arxiv-2506.00231 | auto→mineru | 0.909 | 0.943 | 0.923 | 0.463 | 110 | yes | pdf_math | done |
| arxiv-2506.00231 | docling | 0.791 | 0.877 | 0.859 | 0.000 | 110 | yes | pdf_math | done |
| arxiv-2506.00231 | mineru | 0.909 | 0.943 | 0.923 | 0.463 | 110 | yes | pdf_math | done |

**不宣稱的事**

1. **不是 OmniDocBench／olmOCR-Bench 的分數，不可與其他專案發表的數字比較**：語料、正規化、指標定義都不同。
2. **合成掃描不等於真實掃描**：退化方式是我們選的；真實掃描的對照只有不可重現的 Tier C。
3. **單引擎欄位是「我們出廠設定下的該引擎」**，不是該引擎的最佳表現；MinerU 用 `basic` tier、Docling 用 RapidOCR、MarkItDown 無 OCR；各引擎在其他設定下可能更好。
4. **這量的是編排層**：`auto` 比單引擎好的部分來自「換引擎 + 逐頁修復」，不代表我們的 OCR 更強——我們沒有自己的 OCR。單引擎與 `auto-norepair` 欄位都關閉逐頁修復（`AIDOC_BENCH_NO_REPAIR=1`）。
5. **時間 n=1**，含模型載入；不同機器不可比；只看相對順序。
6. **`verify` 分數不是品質的絕對量**：它與正解 NED 的相關性寫在校準表裡；相關性只在合成語料上量過。
7. **VLM 欄（若有）的幻覺未被 NED 以外的指標量化**；本機 VLM 不計費用。
8. **Tier C 不可重現**，列出只為說明兩本真書的實際結果，與公開數字分開看。
9. 任何「第一」「最強」「SOTA」字眼都不出現在 README 與文件中。

**本評估專屬**：6 份繁中文件、4 篇論文、2 份英文對照，不是 benchmark；正解等級 3（繁中）／4（英文對照）／機器產生（公式，未人工校對）；時間 n = 1；單一機器（Windows、單張 GPU）。

## 表 C — 英文對照（去文字層）

| 文件 | NED auto | NED docling | NED mineru | CER auto | CER docling | CER mineru | F1 auto | F1 docling | F1 mineru | 最差頁 auto | 最差頁 docling | 最差頁 mineru | auto engine | 狀態 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| en-aashto-usrn-1984 | 0.593 | 0.698 | 0.593 | 0.474 | 0.303 | 0.474 | 0.719 | 0.989 | 0.719 | p5 (0.000) | p3 (0.645) | p5 (0.000) | mineru | auto:done / docling:done / mineru:done |
| en-christian-ker-lastwords | 0.946 | 0.599 | 0.946 | 0.054 | 0.467 | 0.054 | 0.956 | 0.776 | 0.956 | p10 (0.932) | p11 (0.027) | p10 (0.932) | mineru | auto:done / docling:done / mineru:done |

**不宣稱的事**

1. **不是 OmniDocBench／olmOCR-Bench 的分數，不可與其他專案發表的數字比較**：語料、正規化、指標定義都不同。
2. **合成掃描不等於真實掃描**：退化方式是我們選的；真實掃描的對照只有不可重現的 Tier C。
3. **單引擎欄位是「我們出廠設定下的該引擎」**，不是該引擎的最佳表現；MinerU 用 `basic` tier、Docling 用 RapidOCR、MarkItDown 無 OCR；各引擎在其他設定下可能更好。
4. **這量的是編排層**：`auto` 比單引擎好的部分來自「換引擎 + 逐頁修復」，不代表我們的 OCR 更強——我們沒有自己的 OCR。單引擎與 `auto-norepair` 欄位都關閉逐頁修復（`AIDOC_BENCH_NO_REPAIR=1`）。
5. **時間 n=1**，含模型載入；不同機器不可比；只看相對順序。
6. **`verify` 分數不是品質的絕對量**：它與正解 NED 的相關性寫在校準表裡；相關性只在合成語料上量過。
7. **VLM 欄（若有）的幻覺未被 NED 以外的指標量化**；本機 VLM 不計費用。
8. **Tier C 不可重現**，列出只為說明兩本真書的實際結果，與公開數字分開看。
9. 任何「第一」「最強」「SOTA」字眼都不出現在 README 與文件中。

**本評估專屬**：6 份繁中文件、4 篇論文、2 份英文對照，不是 benchmark；正解等級 3（繁中）／4（英文對照）／機器產生（公式，未人工校對）；時間 n = 1；單一機器（Windows、單張 GPU）。

## 表 V — 本機 PaddleOCR-VL-1.6

| 文件 | recall（字元加權） | 頁 | 最大 s | 平均 s | length 頁 | 完整 |
|---|---|---|---|---|---|---|
| zh-kaohsiung-court-1973 | 0.957 | 9 | 1.66 | 1.14 | 0 | yes |
| zh-ncl-renjian-cihua | 0.996 | 35 | 0.74 | 0.67 | 0 | yes |
| zh-roc1912-gazette02 | 0.979 | 17 | 1.07 | 0.92 | 0 | yes |
| zh-ssid-zhuyin-primer | 0.773 | 22 | 7.25 | 1.02 | 1 | yes |
| zh-supreme-court-1972 | 0.956 | 4 | 2.03 | 1.50 | 0 | yes |
| zh-taipei-court-1992 | 0.975 | 2 | 1.36 | 1.34 | 0 | yes |
| en-aashto-usrn-1984 | 0.995 | 5 | 7.31 | 2.45 | 1 | yes |
| en-christian-ker-lastwords | 0.973 | 16 | 1.39 | 1.19 | 0 | yes |

封面句：yes；最大請求 7.31 s；顯存基線 4698 MB、峰值 7084 MB、delta 2386 MB；模型載入 4.3 s；server {'device_cuda': True, 'layers_on_gpu': 18, 'layers_total': 18}；端點 127.0.0.1

**不宣稱的事**

1. **不是 OmniDocBench／olmOCR-Bench 的分數，不可與其他專案發表的數字比較**：語料、正規化、指標定義都不同。
2. **合成掃描不等於真實掃描**：退化方式是我們選的；真實掃描的對照只有不可重現的 Tier C。
3. **單引擎欄位是「我們出廠設定下的該引擎」**，不是該引擎的最佳表現；MinerU 用 `basic` tier、Docling 用 RapidOCR、MarkItDown 無 OCR；各引擎在其他設定下可能更好。
4. **這量的是編排層**：`auto` 比單引擎好的部分來自「換引擎 + 逐頁修復」，不代表我們的 OCR 更強——我們沒有自己的 OCR。單引擎與 `auto-norepair` 欄位都關閉逐頁修復（`AIDOC_BENCH_NO_REPAIR=1`）。
5. **時間 n=1**，含模型載入；不同機器不可比；只看相對順序。
6. **`verify` 分數不是品質的絕對量**：它與正解 NED 的相關性寫在校準表裡；相關性只在合成語料上量過。
7. **VLM 欄（若有）的幻覺未被 NED 以外的指標量化**；本機 VLM 不計費用。
8. **Tier C 不可重現**，列出只為說明兩本真書的實際結果，與公開數字分開看。
9. 任何「第一」「最強」「SOTA」字眼都不出現在 README 與文件中。

**本評估專屬**：6 份繁中文件、4 篇論文、2 份英文對照，不是 benchmark；正解等級 3（繁中）／4（英文對照）／機器產生（公式，未人工校對）；時間 n = 1；單一機器（Windows、單張 GPU）。

## 決策（decisions.json，機械判定）

margin 0.0500（CER_gt 上界 0.0009）；有效性 no：samples yes, clean_tree yes, gt_pages yes, control_ned no, auto_route yes, vlm_setup yes；範圍（2026-10-22 修訂）：shared no, route yes, vlm yes

### (1a) 繁中掃描路由：`insufficient`

run invalid (validity checks)

| 條件 | 值 | op | 門檻 | 成立 |
|---|---|---|---|---|
| gt_quality | yes | == | yes | yes |
| n_docs_both | 6 | >= | 5 | yes |
| n_pages | 89 | >= | 60 | yes |
| delta | -0.3340 | >= | 0.0500 | no |
| ci_lo | -0.4060 | > | 0.0000 | no |
| wins_D | 1 | >= | 5 | no |
| big_loss_D | 5 | == | 0 | no |
| neg_delta | 0.3340 | >= | 0.0500 | yes |
| ci_hi | -0.2638 | < | 0.0000 | yes |
| wins_M | 5 | >= | 5 | yes |
| big_loss_M | 0 | == | 0 | yes |

### (1b) 公式路由：`insufficient`

未成立：papers_mineru；run invalid (validity checks)

| 條件 | 值 | op | 門檻 | 成立 |
|---|---|---|---|---|
| formula_gt_confidence | yes | == | yes | yes |
| n_papers | 4 | >= | 3 | yes |
| papers_mineru | 0 | >= | 3 | no |
| papers_docling | 0 | >= | 3 | no |
| arxiv-2506.00451 M recovery | 0.9559 | >= | 0.5000 | yes |
| arxiv-2506.00451 M gap | 0.0000 | >= | 0.3000 | no |
| arxiv-2506.00451 M text_guard | 0.8610 | >= | 0.7901 | yes |
| arxiv-2506.00451 D recovery | 0.9559 | >= | 0.5000 | yes |
| arxiv-2506.00451 D gap | 0.0000 | >= | 0.3000 | no |
| arxiv-2506.00451 D text_guard | 0.8401 | >= | 0.8110 | yes |
| arxiv-2506.00108 M recovery | 0.8889 | >= | 0.5000 | yes |
| arxiv-2506.00108 M gap | 0.0556 | >= | 0.3000 | no |
| arxiv-2506.00108 M text_guard | 0.9022 | >= | 0.8518 | yes |
| arxiv-2506.00108 D recovery | 0.8333 | >= | 0.5000 | yes |
| arxiv-2506.00108 D gap | -0.0556 | >= | 0.3000 | no |
| arxiv-2506.00108 D text_guard | 0.9018 | >= | 0.8522 | yes |
| arxiv-2506.00106 M recovery | 0.7644 | >= | 0.5000 | yes |
| arxiv-2506.00106 M gap | 0.0785 | >= | 0.3000 | no |
| arxiv-2506.00106 M text_guard | 0.9785 | >= | 0.9134 | yes |
| arxiv-2506.00106 D recovery | 0.6859 | >= | 0.5000 | yes |
| arxiv-2506.00106 D gap | -0.0785 | >= | 0.3000 | no |
| arxiv-2506.00106 D text_guard | 0.9634 | >= | 0.9285 | yes |
| arxiv-2506.00231 M recovery | 0.9091 | >= | 0.5000 | yes |
| arxiv-2506.00231 M gap | 0.1182 | >= | 0.3000 | no |
| arxiv-2506.00231 M text_guard | 0.9232 | >= | 0.8090 | yes |
| arxiv-2506.00231 D recovery | 0.7909 | >= | 0.5000 | yes |
| arxiv-2506.00231 D gap | -0.1182 | >= | 0.3000 | no |
| arxiv-2506.00231 D text_guard | 0.8590 | >= | 0.8732 | no |

需要的樣本（§11）：>= 4 more CC BY arXiv papers with hand-downloaded e-prints compiled with page markers (per-page formula and text GT)；detector: >= 10 non-TeX formula PDFs (Word/LibreOffice equation editor, matplotlib/plotly) with source GT

### (2) 本機 VLM：`insufficient`

未成立：recall；run invalid (validity checks)；known_limitation: scanned_cht repeated paragraph 0.75 (spec §7.3.4)

| 條件 | 值 | op | 門檻 | 成立 |
|---|---|---|---|---|
| pinned | yes | == | yes | yes |
| on_gpu | yes | == | yes | yes |
| vram_measured | yes | == | yes | yes |
| docs_measured | 8 | >= | 8 | yes |
| cover_measured | yes | == | yes | yes |
| recall | 0.7728 | >= | 0.9500 | no |
| max_sec | 7.3070 | <= | 10.0000 | yes |
| vram | 2386 | <= | 6144 | yes |
| cover | yes | == | yes | yes |

## 重跑

無

## 偏差

- **凍結與執行**：FREEZE ＝ `b5fdc3a`（`env.json.aidoc_commit`，`aidoc_dirty = false`）。執行前在同一個 commit 上全套 `pytest -m "not slow"` 綠：1486 passed、1 skipped。
- **GT**：
  - `gt_quality.json` 由獨立驗證者重判後凍結：抽查 12 頁，k = 0、n = 4463，`CER_gt` 上界 0.00086，margin 0.05。
  - `gt_sha256` ＝ `55b7643b…63ae`，與 harness 重算的值相同；勘誤 15 條。
  - 正解的重建與勘誤重新對位見 spec §6.6（修訂 C）。
- **VLM 由 harness 啟動 llama-server**（計畫偏差 #6）：
  - 顯存基線是 server 啟動前量的（4698 MB，含桌面程式）。執行前目測 4617 MB、GPU 0 %；VLM 階段前 4698 MB、0 %。依協調者指示照實記錄，沒有關閉任何程式。
  - 模型載入到 `/health` 200 共 4.31 s；結束後確認沒有殘留 llama-server 行程。
  - 四個釘死檔 sha256 全部相符，`llama-server.exe` 與 zip 內成員逐位元組相同；端點 127.0.0.1。
- **未宣告斷網**（`offline_declared = false`）。引擎在本機執行，VLM 只走 loopback 而且不經 proxy（§8.4 的隱私修正）。
- **曝光**：見 spec §8.4。4 篇論文與 2 份英文對照曾在 verify 第 4 輪被驗證者轉換過；6 份繁中掃描在本 run 之前從未被轉換。對照組檢查沒有因為這次曝光而修改。
- 其他實作偏差見計畫的 deviations 表 #1–#28。

## 評註

（只做解讀；結論以上方 decisions.json 為準，三個都是 `insufficient`。）

**1. 為什麼無效，以及為什麼不重跑。** 對照組檢查的本意是抓 harness／頁對應／正規化的 bug（§7.0）。逐頁診斷顯示失敗來自引擎在這份文件上的行為，不是 harness：

| 引擎 | 文件 | 現象 | 數字 |
|---|---|---|---|
| Docling | en-aashto | 五頁的字幾乎全對，順序不同：無框表格的儲存格閱讀順序與 GT 的列順序不同（驗證者事前的提醒） | 字袋 recall 0.95–0.99、precision 0.98–1.00，NED 0.65–0.77 |
| MinerU | en-aashto | 第 5 頁的文字被標在第 4 頁的頁標記下，第 5 頁是空的：引擎自己的頁歸屬錯誤 | p4 precision 0.59，pred 2264 字對 GT 1341 字 |
| Docling | en-christian-ker | 同樣的頁歸屬錯誤（p2 吞了 p3） | 該文件頁均 NED 0.599 |
| MinerU | en-christian-ker | 正常 | 頁均 NED 0.946 |

頁切分照 §4.2 用 `aidoc.pagemap.split_pages()`，正規化照 §5.1，兩者都沒有發現錯誤，所以 §8.3「修 harness 後重跑一次」的前提不成立。是否接受「對照組本身不適合當 harness 檢查」並另開新評估（新的對照樣本，§11），由協調者決定；本 run 不改判。

**2. 若 run 有效，條件會怎麼判**（以下只是讀 `decisions.json` 的條件值，**不是結論**，也不能當任何改動的依據；§8.2：這 12 份文件現在降為回歸集）：

- **(1a) 繁中掃描路由。** 確認方向的條件全部成立：Δ = −0.334，95 % CI [−0.406, −0.264]，`wins_M` 5、`big_loss_M` 0；計分頁 89、兩邊都完成 6 份，最小樣本數也成立。
  - 也就是說，現行「MinerU 優先」在真實掃描上**大幅領先**，與合成基準的訊號（Docling 較好）相反。
  - Docling 的頁均 NED 很低，但字袋 F1 高（例如 ncl 0.160 對 0.926、roc1912 0.120 對 0.919），顯示主因是直排的閱讀順序（§5.4 診斷，不決策）。
- **(1b) 公式路由。**
  - 四篇的行間公式回收率 R 兩引擎都 ≥ 0.50（MinerU 0.764–0.956、Docling 0.686–0.956）。
  - 差距 `R_M − R_D` 只有 0.000–0.118，四篇都沒到 0.30，所以會是 `insufficient`。
  - 偵測器：四篇 `math_hint` 全為真、`auto` 走 `pdf_math` → MinerU。
- **(2) 本機 VLM。** 會是 `fail`，唯一沒過的是 recall。
  - 失敗的文件：`zh-ssid-zhuyin-primer` 0.7728（< 0.95），22 頁中有 1 頁 `finish_reason = length`。
  - 其他 7 份都 ≥ 0.95：0.956–0.996，其中最高法院 0.9563、高雄 0.9571 最接近門檻。
  - 速度 max 7.307 s ≤ 10 s；顯存 delta 2386 MB ≤ 6144 MB；封面句命中；釘死 sha256 相符。
  - primer 的課文頁是驗證者依閱讀順序整頁轉錄的；注音課本的版面（大字、表格、注音並排）是已知的難點。

**3. 對 MinerU 與 Docling 在個別文件上的觀察**（診斷，不決策）：

| 引擎 | 文件 | 觀察 |
|---|---|---|
| Docling | zh-supreme-court-1972 | 6253×4428 pt 的跨頁輸出空白（狀態 `low`，四頁全部 0） |
| MinerU | zh-kaohsiung-court-1973 | 多頁文字被併到同一個頁標記下：p1 有 774 字、GT 只有 121 字；p2、p3、p7、p8 是空的 |

兩者都是頁歸屬或輸出問題，依 §4.2 照計分。

**4. 下一步（依 spec）。** 三個決策都是 `insufficient`，**不改任何路由、不開 VLM 本機後端計畫**。§11 列出了每個決策還需要的新樣本。是否要為對照組檢查開新的評估 spec，由協調者決定。

## 不宣稱

**不宣稱的事**

1. **不是 OmniDocBench／olmOCR-Bench 的分數，不可與其他專案發表的數字比較**：語料、正規化、指標定義都不同。
2. **合成掃描不等於真實掃描**：退化方式是我們選的；真實掃描的對照只有不可重現的 Tier C。
3. **單引擎欄位是「我們出廠設定下的該引擎」**，不是該引擎的最佳表現；MinerU 用 `basic` tier、Docling 用 RapidOCR、MarkItDown 無 OCR；各引擎在其他設定下可能更好。
4. **這量的是編排層**：`auto` 比單引擎好的部分來自「換引擎 + 逐頁修復」，不代表我們的 OCR 更強——我們沒有自己的 OCR。單引擎與 `auto-norepair` 欄位都關閉逐頁修復（`AIDOC_BENCH_NO_REPAIR=1`）。
5. **時間 n=1**，含模型載入；不同機器不可比；只看相對順序。
6. **`verify` 分數不是品質的絕對量**：它與正解 NED 的相關性寫在校準表裡；相關性只在合成語料上量過。
7. **VLM 欄（若有）的幻覺未被 NED 以外的指標量化**；本機 VLM 不計費用。
8. **Tier C 不可重現**，列出只為說明兩本真書的實際結果，與公開數字分開看。
9. 任何「第一」「最強」「SOTA」字眼都不出現在 README 與文件中。

**本評估專屬**：6 份繁中文件、4 篇論文、2 份英文對照，不是 benchmark；正解等級 3（繁中）／4（英文對照）／機器產生（公式，未人工校對）；時間 n = 1；單一機器（Windows、單張 GPU）。
