# VLM OCR 引擎（最後一道 fallback 與逐頁修復引擎）— 設計文件

- 日期：2026-10-05
- 狀態：待審核（v1：含查證結果與 spike 定義；spike 數字待實測）
- 依據：`docs/superpowers/research/2026-10-02-similar-projects.md` §8.1 建議 1、§7 差距表「轉換品質（掃描/複雜版面）」列、§5 B 表 B7（PaddleOCR-VL）／B5／B5''（Marker／Chandra 授權）、A7（doc7 純 VLM 路線）。
- 相關：主 spec `2026-09-24-ai-friendly-doc-design.md`（§3 架構與 Engine 介面、§4 路由、§8.2 失敗分類、§9 安裝）、逐頁品質 `2026-10-01-per-page-quality-and-page-map.md`（§4 決策 4 修復引擎、§5 逐頁模型、§8.2 修復流程；以下稱「逐頁 spec」）、契約 `plans/2026-09-24-index.md`（§3 models、§5 runner 協定、§6 fake scenario、§7 設定）、MCP spec `2026-10-01-mcp-server-design.md`、`aidoc verify` 與 benchmark `2026-10-05-verify-and-benchmark-design.md`（benchmark 把本引擎列為受測設定之一）。
- 實作計畫：`docs/superpowers/plans/2026-10-05-vlm-ocr-engine.md`
- Spike 腳本與原始輸出：`.superpowers/spike/`（gitignored）；write-up 進 `docs/superpowers/spikes/2026-10-vlm-ocr.md`。

> **一句話：** 新增第四個引擎 `vlm`——一個引擎名、兩個後端：本機 **PaddleOCR-VL-1.6**（官方 Apache-2.0 GGUF，由我們管理的 `llama-server` 子行程提供；§3 查證：原生 PaddlePaddle 在 Windows + sm_120 不可行）與**任何 OpenAI 相容的視覺端點**（LM Studio／Ollama／vLLM／雲端；預設關閉、要明確同意頁面影像離開這台電腦）。兩個後端共用同一支 HTTP runner，與現有三個引擎同一套 runner 協定。它只在兩個位置出現：所有 PDF／圖片路由的**最後一道 fallback**，以及逐頁修復順序的**最後一個修復引擎**。

## 1. 背景、目標、非目標

### 1.1 背景

- 2025–26 文件解析品質的主戰場是 0.9B～7B 的「文件 VLM」（PaddleOCR-VL、MinerU VLM tier、olmOCR-2、Chandra、dots.mocr、DeepSeek-OCR、GLM-OCR；研究文件 §5 結論）。我們目前只透過 MinerU 的 `standard` tier 間接碰到 VLM，而 §13.1 的決議是預設 `basic`（Windows + RTX 50 風險最低），所以**實際上沒有任何 VLM 在跑**。
- 逐頁 spec 的修復引擎 A（MinerU `ocr_mode="ocr"`）與 B（Docling pypdfium + FULL_PAGE + RapidOCR）在 T-001 上 4/4 命中；但兩者都是「版面模型 + 傳統 OCR」，對手寫、低解析度掃描、複雜表格、豎排／混排的繁中頁面仍會失手，而失手時我們**沒有下一個可試的東西**——頁留在 `warn`。
- pdfmux 把 BYOK LLM 當最強後端、doc7 用單一 VLM 打天下、PaddleOCR Local 讓使用者在五個 VLM 之間手動切（研究文件 A2／A3／A7）。我們的差異化是自動路由與品質 fallback，所以 VLM 應該**放在路由與修復的尾端**，由品質模型決定什麼時候輪到它，而不是讓使用者選。

### 1.2 目標

1. 新引擎 `vlm`，與 `markitdown`／`docling`／`mineru` 一樣是 `ENGINE_NAMES` 的一員：可 `--engine vlm` 強制、可 `aidoc setup vlm`、Settings 有卡片、sidecar 的 `engine`／`tried`／`repaired_by` 都能出現。
2. 兩個後端走**同一個 Engine 介面與 runner 協定**（index §5）：`local`（本機：由 runner 啟動並監管的 `llama-server` + PaddleOCR-VL-1.6 GGUF 預設組合，loopback 隨機 port）與 `openai`（外部 OpenAI 相容 `/v1/chat/completions`）。兩者共用一支 HTTP runner；切換後端不改 pipeline 一行。
3. 路由：VLM 是每一條 PDF／圖片規則的最後一道 fallback（§5）。修復：VLM 是修復順序的最後一個（§6）。兩個角色都可個別關閉。
4. 遠端後端：**預設關閉**；開啟需要明確同意；每一份文件的 sidecar 與 task log 都記下「哪幾頁送到哪個主機」；有每日頁數預算與單檔頁數上限；逾時、429、5xx 有明確的重試與退避。
5. 品質：VLM 輸出一樣走逐頁品質模型（頁碼由構造保證 100%、對齊抽查、亂碼），另加 VLM 專屬的兩個驗收規則（拒答樣板、截斷）。
6. 測試：假 VLM 端點（stdlib `http.server`）讓 unit／API／reliability 測試不需 GPU、不連網；慢測試在真實 PaddleOCR-VL 與（可選）真實端點上跑。

### 1.3 非目標

- 不做「頁級混用引擎」（研究文件 §8.1 建議 2：先分類頁面複雜度、只把難頁送 MinerU／VLM）——那是另一份 spec；本設計的 VLM 只在 fallback 與修復兩處出現。
- 不整合 Marker／Chandra（§2.3 授權）、不整合 vLLM／LMDeploy／SGLang 自架服務（Windows 原生皆不支援；使用者若在 WSL2／Linux 自架，透過 `openai` 後端接進來即可）。
- 不做圖片抽取（遠端後端拿不到 bbox；本機後端有版面結果時才切圖，§4.5）。
- 不做 LLM 後處理（表格修復、圖說生成；主 spec §11 既有非目標）。
- 不做 prompt 自訂 UI；prompt 是程式內常數並有版本號（`prompt_version`），改 prompt 要改版本號並重跑 benchmark。

## 2. 候選與授權

### 2.1 為什麼是 VLM OCR

傳統 OCR（RapidOCR／EasyOCR／PP-OCR）是「偵測文字框 → 逐框辨識」，對版面、表格、公式、手寫一概不管；MinerU／Docling 再疊版面模型補結構。文件 VLM 直接看整頁（或整個版面區塊）輸出 Markdown／HTML／LaTeX，對手寫、低品質掃描、複雜表格、古籍／罕字的表現在 OmniDocBench 上已超過前者（§3.1）。對我們而言，它是「A、B 都失敗之後還有一手」的最佳候選。

### 2.2 候選比較（2026-10-05 查證，細節與來源見 §3）

| 候選 | 授權（程式／權重） | 大小 | 繁中 | Windows 原生 | 結論 |
|---|---|---|---|---|---|
| **PaddleOCR-VL-1.6**（PaddlePaddle，2026-05-28） | Apache-2.0／Apache-2.0 | 0.9B | 109 語、中文最強之一（OmniDocBench v1.6 96.33） | 原生 Paddle **不可行**（§3.2）；**llama.cpp GGUF 可行**；transformers 路徑可行但只有 VLM 沒有版面模型 | **本機後端首選**（GGUF 路徑） |
| olmOCR-2-7B-1025（AI2，2025-10） | Apache-2.0／Apache-2.0 | 7B | 以英文為主，無中文宣稱 | 需 vLLM（Linux）或任何 OpenAI 相容端點 | 不內建；使用者可自架後以 `openai` 後端接入 |
| Qwen2.5-VL／Qwen3-VL 系列 GGUF | Apache-2.0 | 3B／7B | 中文佳、通用 | llama.cpp／LM Studio／Ollama 皆可 | 作為 `local_preset = "generic-gguf"` 的自訂選項與 spike 的對照組，不是預設 |
| Chandra 2（datalab-to，2026-03） | Apache-2.0／**修改版 OpenRAIL-M**：研究、個人、年營收或募資 < 200 萬美元免費；「**Commercial self-hosting requires a license**」 | 5B | 90+ 語 | HF transformers 或 vLLM | **不採用** |
| Marker v2 + surya 權重 | Apache-2.0／**修改版 OpenRAIL-M**：< 500 萬美元門檻、超過需商業授權 | — | 90+ 語 | — | **不採用** |
| dots.mocr、DeepSeek-OCR-2、GLM-OCR | MIT／Apache-2.0 | 1.7B～3B | 中文佳 | 多為 vLLM | 可經 `openai` 後端接入；不內建 |

### 2.3 為什麼避開 Marker／Chandra

兩者**程式**是 Apache-2.0，但**模型權重**是修改版 OpenRAIL-M，附帶營收／募資門檻（Marker 500 萬、Chandra 200 萬美元）與「不得與其 API 競爭」「商業自架需另購授權」條款（README 原文：*"Commercial self-hosting requires a license"*）。Doc4AI Studio 是 AGPL-3.0、可自架、可能被公司內部或對外服務使用：

1. 我們無法替使用者判斷他們是否符合門檻；把權重下載寫進 `aidoc setup` 等於替他們做了授權決定。
2. 「不得與其 API 競爭」對一個轉換服務來說邊界模糊。
3. 主 spec §2 當時以「GPL-3.0 + 商用限制」排除 Marker 的理由仍然成立（程式已改 Apache-2.0，但權重條款更明確了）。

PaddleOCR-VL 程式與權重皆 Apache-2.0，olmOCR 亦然，沒有這些問題。研究文件 §8.1 建議 1 的結論維持。

## 3. 查證結果（主要來源）與 spike 定義

數字與引述來自 2026-10-05 讀到的原始來源（HF model card、GitHub releases／issues、官方文件、arXiv）。標 **未驗證** 者不得當事實引用；spike 要補齊。

### 3.1 PaddleOCR-VL-1.6

| 項目 | 結果 | 來源 |
|---|---|---|
| 版本／日期 | PaddleOCR-VL-1.6（VLM `PaddleOCR-VL-1.6-0.9B` + 版面 PP-DocLayoutV3），2026-05-28；隨 PaddleOCR v3.6.0 發佈；套件最新 v3.7.0（2026-06-11，PP-OCRv6，VL 無變動） | HF card `PaddlePaddle/PaddleOCR-VL-1.6`；GitHub releases v3.6.0／v3.7.0；arXiv 2606.03264 |
| 授權 | 程式 Apache-2.0；權重 HF card license tag `apache-2.0`（1.6 與 GGUF 版皆是）。原始 front-matter 文字未直接讀到，但 tag 無歧義 | HF cards；repo LICENSE |
| 品質 | OmniDocBench v1.6 總分 **96.33**（text edit 0.033、formula CDM 97.49、table TEDS 94.76）；VL-1.5 94.93、MinerU2.5-Pro 95.75、Gemini 3 Pro 92.91。v1.0 edit distance EN 0.041／ZH 0.062（原始 VL）。1.6 release note：「significantly improves … Chinese ancient document parsing, and Chinese rare character recognition」 | arXiv 2606.03264；v3.6.0 release notes；arXiv 2510.14528v2 |
| 繁體中文 | 宣稱 109 語；訓練資料分類含 traditional Chinese（**弱驗證**：來自技術報告摘要）；**沒有繁中專屬 benchmark 數字** → 由 spike 與我們的 benchmark 補 | arXiv 2510.14528 |
| 輸出 | Markdown；JSON 含 bbox 與閱讀順序；表格 HTML；公式 LaTeX | paddleocr.ai PaddleOCR-VL pipeline 文件 |
| 輸入 | 整頁影像或 PDF；NaViT 動態解析度，VLM `max_pixels` 預設 1280×28×28 = **1,003,520 px**（約 1000×1000）；建議 DPI **未驗證**（文件未寫） | HF card；GGUF card `image_max_pixels` |
| 速度／顯存（官方） | A100 + vLLM 1.22 頁/s、43.7 GB（vLLM 預先配置）；RTX 4090D + vLLM 1.15 頁/s、16.6 GB（**部分驗證**：搜尋結果引自同一報告，未在 v2 HTML 找到該表）；transformers + flash-attn 社群回報約 3.3 GB、19 s/頁；RTX 5060 Ti 社群回報 2.8–3.0 GB。**消費級 GPU 的 1.6 官方數字不存在** | arXiv 2510.14528v2；HF discussion #59 |

### 3.2 Windows 11 + RTX 5070 Ti（sm_120）可行性

| 路徑 | 事實 | 判定 |
|---|---|---|
| **(a) 原生 `paddlepaddle-gpu`** | Windows pip 只提供 3.2.2 的 cu118／cu126／**cu129**（無 cu128／cu130）；Paddle issue #72746（2025-05，Windows）：「compute capability 120 … not supported by Paddle」，關閉無修正；社群 sm_120 wheel 只有 Linux x86_64 + Python 3.10；**Paddle v3.4.0（2026-09-11）release note 明寫「本版本是最后一个提供 Windows 系统支持的版本」**。PaddleOCR 文件：SGLang／FastDeploy 後端要求 8.0 ≤ CC < 12.0（明確排除 Blackwell） | **不可行**，且是死路（Windows 支援終止） |
| **(b) llama.cpp GGUF** | llama.cpp PR #18825（2026-02-19，b8110）加入 PaddleOCR-VL；官方 GGUF `PaddlePaddle/PaddleOCR-VL-1.6-GGUF`（Apache-2.0）含 `mmproj`；`llama-server -m … --mmproj … --temp 0` 即 OpenAI 相容端點；任務 prompt 為 `"OCR:"`／`"Table Recognition:"`／`"Formula Recognition:"`／`"Chart Recognition:"`／`"Spotting:"`；Windows 預編譯有 CUDA 12.4 與 **CUDA 13** 兩種，12.4 版早於 sm_120 → 用 CUDA 13 版（驅動需 R580+；本機驅動 591.86 符合）或自建 `-DGGML_CUDA_ARCHITECTURES=120`；已知 issue #22551 為 Windows **AMD** 零輸出，與 NVIDIA 無關 | **可行**，本機後端的主線。缺點：只有 VLM、沒有 PP-DocLayoutV3 版面模型 → 整頁一次 `OCR:` 的表格／閱讀順序品質要 spike 量 |
| **(c) transformers ≥ 5 + torch cu128** | HF card 有 transformers 範例；torch 2.7+ cu128 Windows wheel 支援 sm_120（與現有 env 相同作法）；flash-attn 非必要；只驅動 VLM，版面模型是否能走 Paddle-free 的 Transformers backend **未驗證** | 可行但多一個 torch env（約 5 GB）、速度未知；作為 (b) 失敗時的備案 |
| **(d) WSL2 + Docker genai_server（vLLM）** | PaddleOCR 文件：「vLLM, SGLang, and FastDeploy cannot run natively on Windows. Please use our Docker images.」；vLLM 文件 OS: Linux、Blackwell 需 CUDA ≥ 12.8 | 完整管線（版面 + VLM）唯一路徑，但不是「本機後端」的範圍；進階使用者自架後以 `openai` 後端接入 |

### 3.3 OpenAI 相容端點

| 項目 | 結果 | 來源 |
|---|---|---|
| 請求形狀 | Chat Completions `messages[].content[]` 的 `{"type":"image_url","image_url":{"url":"data:image/png;base64,…","detail":"auto|low|high|original"}}`；`finish_reason: "length"` = 截斷；`max_completion_tokens` 取代已淘汰的 `max_tokens`（本機 server 多半仍只認 `max_tokens` → 兩個都送） | OpenAI API reference |
| 限制／錯誤 | 總 payload 512 MB、每請求 1,500 張、每張 30,000 patches（舊的 20 MB/張已不在現行文件）；429 帶 `Retry-After`（最少等待秒數）與 `x-ratelimit-*` 標頭；額度／帳務類 429 不應重試 | OpenAI rate limits 文件 |
| 本機 server | LM Studio：支援 `image_url` data URI（但 0.4.9 有 issue #1752 拒收標準 data URI，**要測實際形狀**）；Ollama `/v1`：只收 base64、不收 URL；vLLM：URL／base64／file；llama.cpp `llama-server`：`--mmproj` 後 `/chat/completions` 支援影像 | 各官方文件 |
| 費用（2026-10-05 價目，自行換算一頁 A4 150 dpi 輸入） | gpt-4o-mini high ≈ 36.8k tokens ≈ $0.0055；gpt-4.1-mini ≈ 3.5k ≈ $0.0014；Gemini 2.5 Flash ≈ 1.5k ≈ $0.0005；另加輸出 1–2k tokens ≈ $0.001–0.005/頁 | OpenAI／Gemini pricing 頁（換算是我們的算術） |
| 參考實作 doc7 | `dpi: 220`、`image_detail: high`、`max_image_mb: 9`、`workers: 5`、`timeout_seconds: 120`、`retry_count: 3`；輸出上限 8,192 tokens/頁；超出 context 時以較低解析度重試（兩階、最低長邊 720 px）；輸出截斷 → 標頁失敗、不靜默寫入 | github.com/magicrew/doc7（MIT） |

### 3.4 Spike S1：本機 PaddleOCR-VL-1.6 在本機器上的可行性與品質

- 環境：Windows 11、RTX 5070 Ti 16 GB、驅動 591.86。腳本 `.superpowers/spike/vlm_s1_*.py`（不進版控）；write-up `docs/superpowers/spikes/2026-10-vlm-ocr.md`（進版控，含表格與決策）。
- 樣本：`tests/fixtures/scanned_cht.pdf`、`scanned_mixed.pdf`、`page.png`、`text.pdf`（頁 2 有表格）、`formula.pdf`、`tests/fixtures/manual/hfad_cover_p1.pdf`、`hfad_garbled_mix.pdf`、`ortho_p300-340.pdf`（取 5 頁：表格／公式／圖說）。
- 路徑 (b)：下載 llama.cpp Windows CUDA 13 預編譯（釘 tag ≥ b8110）＋ `PaddleOCR-VL-1.6-GGUF`（F16 與 Q8_0 兩種）＋ mmproj；`llama-server --temp 0 --image-max-pixels 1003520`（旗標名以該版 `--help` 為準）；整頁一次 `"OCR:"`；另測 `"Table Recognition:"` 對整頁與對表格裁切。對照組：同一 llama-server 跑 `Qwen2.5-VL-3B-Instruct` GGUF + §4.4 generic prompt。
- 路徑 (c)：只在 (b) 不過時做：`envs/vlm/` torch cu128 + transformers ≥ 5，同樣樣本。
- 量測：每頁字元 recall（與 `make_fixtures.py` 的 ZH／EN 字串比；真實樣本與 MinerU `ok` 輸出比）、`text.pdf` 頁 2 是否出現可解析的表格（GFM 或 HTML，≥ 3 欄）、`formula.pdf` 是否出現 LaTeX、`hfad_cover_p1` 是否含 `Wouldn't it be dreamy`、每頁秒數（含／不含載入）、`nvidia-smi` 峰值顯存、安裝步驟可否完全由 `aidoc setup vlm` 重現（釘 URL + sha256）。
- **決策規則**：
  1. (b) 的 PaddleOCR-VL 整頁 `OCR:` 在三個合成掃描樣本 recall ≥ 0.95、`hfad_cover_p1` 命中、每頁 ≤ 10 s、峰值顯存 ≤ 6 GB、安裝可重現 → **採用 (b)，`local_preset = "paddleocr-vl-1.6"` 為預設**。表格若只出現為純文字，記為已知限制（VLM 作為 fallback 時 `missing_table` 會把它判 `low`，此時既有「保留最高分」規則收尾）；若 `"Table Recognition:"` 對整頁可用且不傷文字 recall，改為兩段式（先 `OCR:`，probe `has_table_lines` 為真的頁再補一次 `Table Recognition:`），寫進 §4.3。
  2. (b) 的 PaddleOCR-VL 不過、但 Qwen2.5-VL-3B GGUF + generic prompt 過（recall ≥ 0.93、表格有結構）→ 採用 (b) 但預設 preset 改為 `qwen2.5-vl-3b`，PaddleOCR-VL 保留為可選 preset，spec §2.2 表更新。
  3. (b) 兩者都不過（例如 CUDA 13 build 在 sm_120 崩潰、速度 > 10 s/頁）→ 試 (c)；(c) recall ≥ 0.95 且顯存 ≤ 8 GB → 採用 (c)（`envs/vlm/` 多一個 torch env）。
  4. 全部不過 → **只交付 `openai` 後端**，本機後端延後；spec 註記、Settings 卡片顯示「本機後端尚未支援此平台」。架構（一支 HTTP runner）不變，因為 (b)／(c) 的差別只在誰啟動本機 server。
- 時程：S1 在 Task 1，最多 1 個工作天；超時即落入規則 4。

### 3.5 Spike S2（可選）：真實外部端點的形狀差異

- 對 LM Studio（issue #1752 的 data URI 形狀）、Ollama `/v1`、llama-server 各送一頁，記錄：接受的 `image_url` 形狀、是否回 `usage`、是否尊重 `max_tokens`／`max_completion_tokens`、`finish_reason` 值、是否把整頁包進 ```` ```markdown ````。結果寫進 `vlm_runner.py` 的相容性註解與假端點的行為清單（§11.1）。不設決策規則，純收集。

## 4. 架構

### 4.1 一個引擎、兩個後端

```
src/aidoc/engines/vlm.py                 VlmEngine(RunnerEngine)：依 config.vlm.backend 決定 engine_opts()/available()；python() 固定為主程式解譯器
src/aidoc/engines/runner/vlm_runner.py   一支 HTTP runner，兩個後端共用；以主程式的 Python 執行（同 fake runner）；stdlib + pymupdf
                                          backend=local 時由它啟動／監管 llama-server 子行程（loopback、隨機 port），再走同一條 HTTP 路徑
src/aidoc/engines/runner/_vlm_prompt.py  prompt 常數、prompt_version、本機 preset 的任務 prompt（stdlib only）
src/aidoc/engines/runner/selfcheck_vlm.py  aidoc setup vlm 的自我檢查（本機：啟 server + 轉內建樣本；遠端：連線測試）
src/aidoc/vlm_runtime.py                 本機 runtime 管理：llama.cpp 預編譯與 GGUF 的下載清單（釘 tag/URL/sha256）、解壓到 data/tools/、preset 表
envs/vlm/manifest.json                   本機後端「安裝了什麼」的紀錄（runtime tag、preset、檔案 sha256）；.ready 與其他引擎同位置
```

- `VlmEngine.name == "vlm"`；`ENGINE_NAMES` 變成 `("markitdown", "docling", "mineru", "vlm")`。所有以 `ENGINE_NAMES` 迭代的地方（registry、`/api/system`、Settings、MCP schema 的 `engine` Literal、CLI `--engine` choices）自動多一個。
- 後端由 `aidoc.toml [vlm] backend = "none" | "local" | "openai"` 決定（預設 `"none"`）。`"none"` 時 `available()` 永遠 False → 路由把它當未安裝（`missing`），既有行為完全不變。**這是本設計對現有使用者的零影響保證。**
- **沒有 `envs/vlm/.venv`**：本機後端不是 Python 套件而是 llama.cpp 預編譯二進位 + GGUF（§4.3），所以 `python()` 兩個後端都回 `sys.executable`、`script()` 都回 `vlm_runner.py`。`envs/vlm/` 只放 `manifest.json` 與 `.ready`，維持「每個引擎一個 `envs/<name>/` 目錄」的慣例與 `paths.ready_marker("vlm")`。若 spike S1 落到路徑 (c)（transformers），才會多出 `envs/vlm/pyproject.toml` 與 `.venv`，`python()` 改回 `paths.venv_python("vlm")`；runner 的 HTTP 部分不變、只是本機 server 換成 runner 內建的 transformers 迴圈——這是把兩個後端收斂成一支 runner 的理由。
- `available()`：
  - `local`：`envs/vlm/.ready` 存在且 `manifest.json` 列的檔案都在磁碟上（`llama-server.exe`、GGUF、mmproj；只檢查存在，不驗 sha256——驗 sha 留給 setup）。
  - `openai`：`config.vlm.remote_enabled is True` 且 `base_url`、`model` 非空、且 API key 可取得（`api_key_env` 指到的環境變數有值，或 `api_key` 非空；端點不需要金鑰時 `api_key_env` 可設為空字串 `""` 並以 `allow_no_key = true` 明示）。**不做網路探測**（`available()` 在每次路由都會被呼叫，不能慢、不能有副作用）。連線問題在轉換時以 `transient` 處理（§4.7）。
- `supports(probe)`：`probe.kind in ("pdf", "image")`。非 PDF／圖片一律不支援（Office 走 MarkItDown／Docling 已足夠；VLM 讀 docx 要先轉 PDF，不在範圍）。

### 4.2 runner 協定（index §5）的使用方式

request `engine_opts`（兩個後端共用的鍵加各自的鍵）：

```json
{"backend": "openai",
 "dpi": 150, "max_image_px": 2000, "image_format": "png",
 "prompt_version": 1, "lang": "cht",
 "base_url": "http://127.0.0.1:1234/v1", "model": "qwen2.5-vl-7b-instruct", "api_key_env": "AIDOC_VLM_API_KEY",
 "request_timeout_s": 120, "max_concurrent": 2, "max_output_tokens": 4096, "detail": "high",
 "page_cap": 200}
```

```json
{"backend": "local", "preset": "paddleocr-vl-1.6", "prompt_style": "paddle_tasks", "dpi": 200, "max_image_px": 1400,
 "server_exe": "C:/…/data/tools/llamacpp/b8xxx/llama-server.exe", "gguf": "C:/…/data/models/vlm/PaddleOCR-VL-1.6-Q8_0.gguf",
 "mmproj": "C:/…/data/models/vlm/PaddleOCR-VL-1.6-mmproj.gguf", "n_gpu_layers": 99, "ctx_size": 8192,
 "image_max_pixels": 1003520, "request_timeout_s": 120, "max_concurrent": 1, "max_output_tokens": 4096,
 "prompt_version": 1, "lang": "cht"}
```

- `api_key_env` 傳**環境變數名稱**而不是金鑰本身；runner 從自己的環境讀（host 啟動 runner 時 `env = os.environ + runner_env()`，所以主程式環境裡的變數會傳下去）。`api_key` 若寫在 `aidoc.toml`，主程式啟動 runner 時以 `AIDOC_VLM_API_KEY_INLINE` 注入環境，不經過 `request.json`（request.json 落在 `data/work/` 磁碟上，不該含金鑰）。
- `result.json`：與其他 runner 相同的欄位（`markdown_path`、`images`、`has_page_markers=true`、`page_count`、`page_map_method="vlm_per_page"`、`failed_pages`、`errors`、`images_missing`），**新增** `vlm: {backend, model, endpoint_host, pages_sent, usage: {prompt_tokens, completion_tokens, requests}, truncated_pages: [], refused_pages: [], dpi, prompt_version}`。host 的 `raw_from_result()` 把它放進 `RawResult.extra["vlm"]`（新欄位 `RawResult.extra: dict = {}`，index §3 增補）。
- 進度：每頁完成即 `AIDOC_PROGRESS {"page": k, "total": n}`；`AIDOC_READY` 在本機 server 的 `GET /health` 回 ok（本機；模型已載入）或第一次成功的 `GET {base_url}/models`（遠端，只做一次、逾時 10 s、失敗不致命只 log）之後送出，所以 index A1 的計時規則不變（本機 server 載模型的時間落在 900 s 的啟動上限內，0.9B 模型實際只需數秒）。
- 多請求模式（P2 常駐 runner）照常：同一個 task 的多段共用一個 runner process；本機後端只載模型一次。

### 4.3 本機後端：PaddleOCR-VL-1.6 GGUF，由 runner 管理的 `llama-server`

查證（§3.2）排除了原生 PaddlePaddle；本機後端是 **llama.cpp 預編譯二進位 + 官方 GGUF**，由 `vlm_runner.py` 在 loopback 上啟動並監管，然後走與遠端完全相同的 HTTP 路徑。

- **runtime 安裝（`aidoc setup vlm`，`vlm_runtime.py`）**：
  1. 下載釘死的 llama.cpp release（tag ≥ b8110、Windows `cuda-13` 預編譯 zip + 對應 `cudart` zip；Linux 用 `ubuntu-x64` 或要求系統自備 `llama-server`）到 `data/tools/llamacpp/<tag>/`，驗 sha256（清單寫死在 `vlm_runtime.py`，升級要改程式與測試）。
  2. 下載 preset 的 GGUF 與 mmproj 到 `data/models/vlm/`（HF URL + sha256 釘死）。預設 preset `paddleocr-vl-1.6`：`PaddleOCR-VL-1.6-Q8_0.gguf` + `PaddleOCR-VL-1.6-mmproj-F16.gguf`（實際檔名以 HF repo 為準，spike 補）；量化等級由 spike 決定（Q8_0 與 F16 皆量，0.9B 模型顯存差距不到 1 GB，取 recall 較高者）。
  3. `selfcheck_vlm.py`：啟動 `llama-server`（`--host 127.0.0.1 --port 0`，實際旗標見下）→ `GET /health` → 送內建樣本 `samples/scanned_cht_page.png`（已有，由 `make_fixtures.py` 複製）→ `sample_chars ≥ 20` 且含樣本中的至少 3 個繁中字 → 寫 `.ready`（含 `runtime_tag`、`preset`、`quant`、`gpu_offloaded_layers`、`device`）。GPU 檢查：server 的啟動 log 必須出現 CUDA 裝置行且 `n_gpu_layers` 全數卸載；否則 setup 失敗並提示「CUDA 13 runtime／驅動 R580+」。
  4. `envs/vlm/manifest.json` 記錄上述所有檔案的路徑與 sha256；`available()` 只看存在。
- **runner 內的 server 生命週期**：
  - 啟動：`llama-server -m <gguf> --mmproj <mmproj> --host 127.0.0.1 --port <free> -ngl 99 -c <ctx_size> --temp 0 --no-webui`（影像像素上限旗標名依該版 `llama-server --help`，spike 記錄；GGUF card 預設 `image_max_pixels: 1003520`）；stdout／stderr 轉成 runner 的 log 行（前綴 `llama-server:`），OOM 字串（`cudaMalloc failed`、`out of memory`）轉成 `_proto.OOM_MARKERS` 能認的訊息，讓主程式的 OOM 路徑生效。
  - 就緒：輪詢 `GET /health` 直到 200（上限 `startup_timeout_s`），然後 `AIDOC_READY`。
  - 關閉：runner 收到 stdin EOF 或自身結束時 `terminate()` server；主程式的 `kill_tree()` 本來就會連子行程一起殺（主 spec §8.2），所以取消／逾時不會留下孤兒 server；`procs.is_aidoc_runner()` 的孤兒判定擴充為「命令列含 `vlm_runner.py` **或** 工作目錄在 `data/work/` 下的 `llama-server`」。
  - 同一個 task 的多段共用同一個 server（runner 常駐）；修復與主引擎各開一個 runner session（既有行為），server 啟動約數秒，可接受。
- **prompt_style**（`_vlm_prompt.py`）：
  - `paddle_tasks`（preset `paddleocr-vl-1.6` 預設）：單一 user 訊息 = 影像 + 文字 `"OCR:"`；回應是該頁文字（含 Markdown 風格的標題／表格時照收）。spike 決策規則 1 若採兩段式：runner 對 `engine_opts.table_pages`（主程式由 probe 的逐頁格線結果填入）再送一次 `"Table Recognition:"`，把回傳的 HTML 表格附在該頁文字之後（表格文字會重複一次；`normalize` 轉 GFM）。
  - `generic`（其他 GGUF 與遠端）：§4.4 的 system + user prompt。
- 表格：HTML → `normalize._convert_tables()` 既有邏輯轉 GFM（與 MinerU 相同路徑）。公式：LaTeX → `$…$`／`$$…$$`。
- 圖片：本機路徑同樣**沒有版面 bbox**（版面模型 PP-DocLayoutV3 不在 GGUF 路徑裡）→ 與遠端相同，`<!-- image missing -->` + `images_missing`（§4.5）。這是相對於完整 PaddleOCR 管線的已知損失，Settings 卡片與 README 明寫。
- OOM／降級：`oom_downgrade()` 依序：`max_image_px 1400 → 1000`、`ctx_size 8192 → 4096`；再 OOM → `engine` 失敗。
- 並行：本機 `max_concurrent = 1`（llama-server 預設單 slot；`-np` 多 slot 會乘顯存，spike 若量到 2 slots 仍 ≤ 6 GB 且吞吐提升 > 1.5× 再開）。

### 4.4 遠端後端：任何 OpenAI 相容視覺端點

- 相容對象：OpenAI、Azure OpenAI（`base_url` 含 deployment）、LM Studio、Ollama（`/v1`）、vLLM／SGLang／llama.cpp `llama-server`（多為 Linux／WSL2 自架）、OpenRouter、Gemini 的 OpenAI 相容層。**只用 Chat Completions**（`POST {base_url}/chat/completions`，`messages[].content[]` 混合 `text` 與 `image_url`（data URI），`max_tokens`，`temperature=0`）；不用 Responses API、不用 function calling、不用 streaming（簡單、相容面最廣）。
- 每頁一個請求（不批次多頁進同一請求：上下文與定位都更可控，也是 doc7 的做法）。並行度 `max_concurrent`（預設 2），以 `ThreadPoolExecutor` 實作，頁序由頁碼決定、不依完成順序。
- 頁面渲染：PyMuPDF `page.get_pixmap(dpi=dpi)`，長邊超過 `max_image_px` 時等比縮小；PNG；單張超過 `max_image_bytes`（預設 15 MB）時改 JPEG q=85。**不送 PDF 文字層**（有些端點會優先相信文字、而壞字型頁的文字層正是錯的）。
- prompt（`_vlm_prompt.py`，`prompt_version = 1`；system + user 兩段，繁中／英文各一份依 `lang` 選）：

```
system: You convert one page image of a document into GitHub-Flavored Markdown. Output only the Markdown
        of the page content, nothing else: no preface, no code fence around the whole page, no commentary.
        Keep the page's language and script exactly as printed (Traditional Chinese stays Traditional Chinese;
        never translate). Preserve reading order (left column before right column). Headings -> #/##/###,
        tables -> GFM pipe tables (one header row; merged cells repeated), formulas -> LaTeX in $…$ or $$…$$,
        figures/photos/charts -> the single line <!-- image --> followed by the caption if there is one.
        Page headers, footers and page numbers -> omit. If the page is blank or has no text, output exactly
        <!-- blank -->.
user:   [image]  "Page {k} of {n}. Return the Markdown of this page."
```

  - 「Page k of n」只給模型（與假端點）定位用；runner 不解析回應裡的頁碼。
  - 回應後處理（runner 內）：剝掉包住整頁的 ```` ```markdown ```` 圍欄；`<!-- blank -->` → 空區段；移除結尾的「Let me know if…」等**已知尾句**（小型 regex 清單，有單元測試）；偵測**拒答樣板**（`^(I'm sorry|I cannot|I can't|As an AI|無法|抱歉)` 等開頭、且全文 < 200 字）→ 該頁記 `refused_pages`、`failed_pages[k] = "refused"`、區段留空；`finish_reason == "length"` → 以 `max_output_tokens × 2`（上限 16384）重試一次，仍截斷 → 記 `truncated_pages`、`failed_pages[k] = "truncated"`，**保留已拿到的部分**（部分內容比空白好，但該頁會被標 `page_conversion_failed` 進修復／`warn`）。
- 重試與退避（在 runner 內，對單一頁請求）：連線錯誤／逾時／5xx／429 → 最多 4 次，退避 2 s → 8 s → 30 s → 60 s（429 且有 `Retry-After` 時以該值為準、上限 120 s）；`401`／`403`／`404`（模型不存在）→ 立即 `RunnerError("engine", …)` 整個 request 失敗（這是設定錯誤，重試沒有意義；host 端 `engine` 失敗 → fallback，而 VLM 之後沒有引擎 → 既有「全部失敗保留最高分」規則）；`400`（例如圖片太大）→ 該頁以較低 `dpi`（−50，最低 72）重試一次，仍 400 → `failed_pages[k]`。
- 逾時：主程式對整段的 `compute_timeout()` 不變（每頁 60 s、最少 120 s）；對遠端這太緊（雲端 VLM 一頁 10–40 s、並行 2）→ `VlmEngine` 覆寫 `timeout_factor`：`openai` 後端的段逾時 = `max(compute_timeout, pages × request_timeout_s ÷ max_concurrent × 1.5)`。單一請求逾時 `request_timeout_s`（預設 120）。
- 用量：每個回應的 `usage.prompt_tokens`／`completion_tokens` 累加進 `result.json.vlm.usage`；沒有 `usage` 的端點（部分本機 server）記 `null`。

### 4.5 圖片與 `images_missing`

- 兩個後端都拿不到版面 bbox，無法裁圖：Markdown 裡的 `<!-- image -->` 保留為 `<!-- image missing -->`（逐頁 spec 驗證者發現 4 的既有機制），逐頁計入 `quality.metrics.images_missing`。Document 檢視會顯示「此頁 N 張圖片未擷取」。這是 VLM 引擎的已知取捨，Settings 卡片與 README 明寫；它只影響 VLM 作為**主引擎**的文件（修復只替換文字區段，被修復頁原本的圖片處理規則見逐頁 spec §8.2 步驟 6）。
- 若日後本機後端改走完整 PaddleOCR 管線（WSL2／Linux），版面 bbox 可用時再加裁圖；介面已預留（`result.json.images`）。

### 4.6 privacy：頁面會離開這台電腦

- `[vlm] remote_enabled = false` 預設；`backend = "openai"` 但 `remote_enabled = false` → `available()` False，Settings 卡片顯示「已設定但未啟用」與一個需要勾選的同意框：「我了解開啟後，被送到此引擎的頁面影像會傳到 `<endpoint_host>`。」勾選後才寫 `remote_enabled = true`。
- **loopback 例外**：`base_url` 的主機是 `127.0.0.1`／`localhost`／`::1` 時（LM Studio、Ollama 本機），不視為「離開這台電腦」：同意框文字改為「端點在本機」，仍需 `remote_enabled = true`（維持單一開關），但 UI 不顯示紅色警示。tailnet／內網位址一律視為遠端。
- 每次使用遠端後端：task log 一行 `vlm(openai): sending N page image(s) to <host> model=<model>`；sidecar `vlm.endpoint_host`、`vlm.pages_sent`；Document 檢視 JSON 分頁可見；Library 卡片對 `engine == "vlm"` 且遠端的文件顯示小字「VLM（遠端）」。
- 每日預算 `remote_daily_page_budget`（預設 500 頁；0 = 不限）：主程式在送出前檢查當日已送頁數（`vlm_usage` 表，§8）；超出時該引擎對這份文件視為**不可用**（路由 `missing` 加 `vlm(budget)`，修復順序跳過），log 說明。單檔上限 `remote_max_pages_per_doc`（預設 200）：頁數超過的 PDF 在路由時就把 `vlm` 拿掉（`missing` 記 `vlm(page_cap)`），修復則不受此限（修復只送被標記頁，受每日預算約束即可）。
- 金鑰：`api_key_env`（預設 `AIDOC_VLM_API_KEY`）優先；`api_key` 寫在 `aidoc.toml` 時 `GET /api/settings` 回 `"***"`（同 `server.token` 的遮罩規則），`PUT` 可寫新值、傳回 `"***"` 視為不變。金鑰永不進 log、sidecar、`request.json`、SSE。
- MCP：透過 `convert_document`／`convert_path` 轉換的文件同樣可能落到 VLM 遠端後端——這是管理者層級的決定（開關在 `aidoc.toml`），tool description 不逐次提示；但 `get_document_info` 回的 `engine` 會是 `vlm`，且 MCP spec §5.1 的「tool 回應不含秘密」規則涵蓋金鑰。

### 4.7 失敗分類（主 spec §8.2 對應）

| 情況 | 分類 | 處理 |
|---|---|---|
| 本機：`llama-server` 啟動失敗、`/health` 逾時、CUDA 不可用 | `transient`（runner 回報 `AIDOC_ERROR kind=transient` 或死亡）→ 2 次重試 → `engine` | 之後沒有 fallback → 保留最高分候選；Settings 卡片的「測試」能提前抓到 |
| 本機：OOM（server log 含 `cudaMalloc failed`／`out of memory`） | `transient(oom)` | `oom_downgrade()`：max_image_px 1400→1000、ctx 8192→4096；再 OOM → `engine` |
| 本機：server 在轉換中死亡 | `transient` | runner 偵測子行程結束 → `AIDOC_ERROR transient`；主程式重試會重開 server |
| 遠端：`GET /models` 失敗 | 不致命 | log；仍嘗試第一頁 |
| 遠端：連線／逾時／5xx／429 用盡重試 | `transient` | 主程式重試 2 次（5 s→30 s）→ `engine` |
| 遠端：401／403／404 | `engine`（設定錯誤） | 立即 fallback（無）→ 收尾；Settings 卡片「測試連線」能提前抓到 |
| 遠端：單頁 400／拒答／截斷 | 頁層級 | `failed_pages`，走逐頁 spec 的 `page_conversion_failed` 路徑 |
| 預算用盡／頁數超限 | 不是失敗 | 路由／修復時跳過，`missing` 記原因 |

## 5. 路由變更（主 spec §4 判斷規則）

| 條件 | 現行 | 新 |
|---|---|---|
| 非 PDF | MarkItDown → Docling（docx/pptx/html） | 不變（`vlm` 不支援） |
| 圖片 | MinerU → Docling | MinerU → Docling → **VLM** |
| PDF 掃描（`text_ratio < 0.5` 或 `image_cover > 0.6`） | MinerU → Docling | MinerU → Docling → **VLM** |
| PDF `math_hint` | MinerU → Docling | MinerU → Docling → **VLM** |
| 其他 PDF | Docling → MinerU → MarkItDown | Docling → MinerU → MarkItDown → **VLM** |
| `lang=en` + EasyOCR 的掃描例外（§13.2） | Docling → MinerU | Docling → MinerU → **VLM** |

- **VLM 永遠排最後，包含排在 MarkItDown 之後。** 理由：MarkItDown 零成本、瞬間完成，而逐頁品質模型會把它不行的輸出（無頁界 → `page_map_incomplete`、漏表格 → `missing_table`）判 `low`，VLM 自然輪到；反過來把 VLM 放前面，會讓 1000 頁數位 PDF 在 Docling／MinerU 都失手時直接燒掉幾百頁的 VLM 時間或費用。
- 加入條件：`config.vlm.use_as_fallback`（預設 true）且 `available()`；遠端後端另受 `remote_max_pages_per_doc` 與每日預算約束（§4.6），不符時 `RouteDecision.missing` 記 `vlm(page_cap)`／`vlm(budget)`，pipeline 既有的「engines not installed, skipped: …」log 會顯示。
- `--engine vlm` 強制：照既有規則（無 fallback、`last_resort=True` → 所有被標記頁都修、§逐頁 spec 偏差 1）。`engine_supports("vlm", probe)` 對 Office 回 False → `engine_unsupported`。
- **`last` 的語意要改**：現行 pipeline 以「路由順序的最後一個」判 `last_resort`。VLM 加入後，MarkItDown 不再是 pdf_default 的最後一個，所以 MarkItDown 的被標記頁會進逐頁修復（修復順序裡有 VLM）→ 這正是想要的；反之當 VLM 不可用時順序不變、行為不變。
- 分段：VLM 與其他引擎一樣整份用同一個引擎、每段快速檢查；1192 頁的書若真的落到遠端 VLM 會先被 `remote_max_pages_per_doc` 擋掉；本機 VLM 不設上限（spike 量速度後，若 < 1 頁/秒則文件 > 400 頁時 log 提醒預估時間）。

## 6. 逐頁修復順序（逐頁 spec §4 決策 4、§8.2）

| 主引擎 | 現行 | 新 |
|---|---|---|
| Docling／MarkItDown | B（Docling pypdfium+FULL_PAGE）→ A（MinerU `ocr`） | B → A → **V** |
| MinerU | A → B | A → B → **V** |
| VLM | — | **（無）**：VLM 自己的輸出沒有「文字層」可壞；被標記頁只可能是 `garbage`／`page_conversion_failed`，重送同一頁給同一個 VLM 期望值低。留 `warn`。 |

- V = `("vlm", {<後端相關 engine_opts>}, "vlm:local:<preset>" | "vlm:openai:<model>")`；`repaired_by` 標籤為 `vlm:local:paddleocr-vl-1.6` 或 `vlm:openai:<model>`（model 名最多 64 字、只留 `[A-Za-z0-9._:-]`）。
- 加入條件：`config.vlm.use_for_repair`（預設 true）且 `available()` 且（遠端時）每日預算尚有餘額 ≥ 待修頁數。
- 修復驗收：逐頁 spec §8.2 步驟 5 的規則不變（區段非空、`flag_page(sig, broken_font=True)` 為空）**再加兩條 VLM 專屬**：該頁不在 `refused_pages`／`truncated_pages`；區段不含 `<!-- blank -->`（模型說空白但原頁有壞字型文字 → 不接受，留給「未修復」）。
- 修復的 `pages.pdf` 是被標記頁抽成的小 PDF（`segment.extract_pages`），對遠端後端每頁一請求、計入當日用量。
- 風格：VLM 修復頁的 Markdown 風格（標題層級、表格）可能與 Docling 主引擎不同，與逐頁 spec 對 A 的取捨相同——V 排最後，只有 A、B 都失敗才用。

## 7. 品質互動

- **頁界**：runner 每頁加標記 → `coverage = 1.0`、`page_map_invalid = 0` 由構造保證；`page_map.method = "vlm_per_page"`。
- **對齊抽查**：對有文字層的 PDF 照常（探針來自 PDF 文字層、在 VLM 輸出裡找）；這同時是**幻覺的粗篩**——VLM 若憑空生成、探針就找不到 → undecidable 比例升高；但 `page_map_misaligned` 只在「找得到卻落在別頁」時成立，所以幻覉不會被誤判成錯位。`verify`／benchmark 以正解量幻覺（verify spec §3.4）。
- **新頁層級訊號 `vlm_low_overlap`（advisory，v1 不影響等級）**：頁有可用文字層（squash 後 ≥ 120 字、不在壞字型頁）時，計算該頁文字層與 VLM 區段的字元 3-gram Jaccard；< 0.2 → 記在 `quality.pages[*].metrics.vlm_overlap` 並列 advisory。先只收集數字，benchmark 跑過後再決定是否升級為 `warn`（寫進 §12 風險）。
- **壞字型閘門**：VLM 輸出不受 PDF 字型影響，但 `flag_pages()` 的閘門是「頁 ∈ `probe.broken_font_pages`」且訊號命中；VLM 輸出沒有 `�`、位移字串，所以不會被誤標。`garbage` 規則照常適用。
- **`pages_flagged` 20% 規則**：不變。VLM 作為最後引擎時 `last_resort=True`（既有邏輯）。
- **分數**：`score` 公式不變。sidecar `quality` 不新增必填欄位。
- `page_conversion_failed`（拒答／截斷／400）進修復（若 VLM 不是主引擎）或留 `warn`（VLM 是主引擎）。

## 8. sidecar、資料庫、設定

### 8.1 sidecar（`<stem>.json`）新增 `vlm` 區塊（只在用到 VLM 時出現）

```json
{
  "engine": "vlm",
  "vlm": {"backend": "openai", "model": "qwen2.5-vl-7b-instruct", "endpoint_host": "127.0.0.1:1234",
          "local_endpoint": true, "dpi": 150, "prompt_version": 1, "pages_sent": 12,
          "usage": {"requests": 13, "prompt_tokens": 18234, "completion_tokens": 9120},
          "truncated_pages": [], "refused_pages": [], "role": "primary"},
  "quality": {"pages": [{"page": 61, "reasons": ["broken_text_layer"], "repaired_by": "vlm:openai:qwen2.5-vl-7b-instruct"}]}
}
```

- `role`：`primary`（VLM 是主引擎）或 `repair`（只用於修復；此時 `engine` 仍是主引擎，`vlm` 區塊照樣出現，`pages_sent` = 修復送出的頁數）。兩者都用到時 `role: "primary"`（主引擎為 VLM 時沒有修復）。
- `tried[]` 的 `engine: "vlm"` 照常。

### 8.2 資料庫：`vlm_usage`（schema v5）

```sql
CREATE TABLE vlm_usage (
  day TEXT NOT NULL,            -- 本地日期 YYYY-MM-DD
  backend TEXT NOT NULL,        -- "openai" | "local"
  endpoint_host TEXT NOT NULL,  -- 本機後端為 "local"
  pages INTEGER NOT NULL DEFAULT 0,
  requests INTEGER NOT NULL DEFAULT 0,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, backend, endpoint_host)
);
```

- `Store.add_vlm_usage(day, backend, host, pages, requests, pt, ct)`（UPSERT 累加）、`Store.vlm_usage_today(backend, host) -> dict`、`Store.vlm_usage_days(n=30)`。
- 由 pipeline 在每個 VLM runner 結果回來時寫（主引擎段與修復都算）；runner 死亡沒有結果時不計（低估可接受）。
- 預算檢查讀此表；跨程序（CLI 與 server 同時跑）以 SQLite 為準，不另設記憶體計數。
- 保留 180 天，由 `maintenance.py` 既有的定期清理順手刪。

### 8.3 設定 `aidoc.toml [vlm]`（index §7 增補）

```toml
[vlm]
backend = "none"                  # "none" | "local" | "openai"
use_as_fallback = true
use_for_repair = true
dpi = 150                         # 遠端預設 150；本機以 local_dpi 為準
max_image_px = 2000               # 遠端：長邊上限
request_timeout_s = 120
max_output_tokens = 4096
# --- openai 後端
remote_enabled = false            # 明確同意：頁面影像會送到 base_url
base_url = ""                     # 例 http://127.0.0.1:1234/v1
model = ""
api_key_env = "AIDOC_VLM_API_KEY"
api_key = ""                      # 可留空改用環境變數；API 讀取時遮罩
allow_no_key = false              # 端點不需金鑰時設 true（api_key_env 可為空）
max_concurrent = 2
remote_max_pages_per_doc = 200
remote_daily_page_budget = 500    # 0 = 不限
# --- local 後端（llama.cpp + GGUF）
local_preset = "paddleocr-vl-1.6" # "paddleocr-vl-1.6" | "qwen2.5-vl-3b" | "custom"
local_dpi = 200
local_max_image_px = 1400
local_ctx_size = 8192
local_gguf = ""                   # preset = "custom" 時必填（絕對路徑）
local_mmproj = ""
local_prompt_style = ""           # "" = 依 preset；"paddle_tasks" | "generic"
```

- 驗證（`config.py` 載入時與 `PUT /api/settings`）：`backend` 在枚舉內；`openai` 時 `base_url` 必須是 `http(s)://` 絕對 URL、不含 query、不含 `@`（避免把金鑰塞進 URL）；`model` 非空；`max_concurrent` 1–8；`dpi`／`local_dpi` 72–300；`max_image_px`／`local_max_image_px` 512–4096；`local_ctx_size` 2048–32768；`request_timeout_s` 10–600；`max_output_tokens` 256–16384；預算與上限 ≥ 0；`local_preset = "custom"` 時 `local_gguf`／`local_mmproj` 必須是存在的絕對路徑（載入時只警告、`PUT` 時 422）。
- `remote_enabled = true` 但 `backend != "openai"` → 載入時警告並忽略（不致命）。
- `api_key` 寫在檔案裡時 `load_config()` 加一條 `warnings`：「建議改用環境變數 AIDOC_VLM_API_KEY」；README 同樣建議。
- `ConvertOptions` 新增 `vlm_remote: bool | None = None`（CLI `--no-vlm-remote` 設 False；None = 依設定）。`options_key()` **加入** `vlm_backend` 與 `vlm_model`（後端／preset／model 名）：換了後端或模型，VLM 主引擎已完成的段不得沿用（§11.3 最後一條）；`vlm_remote` 本身不進 key。MCP 的 `convert_*` 不暴露此選項。

## 9. 安裝與自我檢查

- `aidoc setup vlm`：
  - `backend = "local"`：§4.3 的四個步驟（下載 llama.cpp 預編譯 + cudart、下載 preset GGUF/mmproj、`selfcheck_vlm.py`、寫 `manifest.json` 與 `.ready`）。沒有 `uv sync`。`setup_engines.plan_commands("vlm")` 回傳的是 `[sys.executable, "-m", "aidoc.vlm_runtime", "install"]` 與 selfcheck 兩條指令，讓既有的 log／SSE 機制照常顯示下載進度。GPU 檢查：`.ready` 必須記到 `device` 含 `CUDA` 且 `gpu_offloaded_layers == total_layers`（從 server 啟動 log 解析），否則 setup 失敗（不接受 CPU 推論悄悄過關——會慢到不可用）。
  - `backend = "openai"`：`setup` 不建 env，改做**連線測試**（同 `POST /api/system/vlm/check`）：`GET {base_url}/models`（可選、失敗只警告）＋ 送一張內建合成影像 `samples/vlm_check.png`（我們自己產生的 300×120 px 圖，上面寫 `AIDOC VLM CHECK 繁體中文 2026`；不含使用者資料）→ 回應需含 `AIDOC` 與 `繁體`（忽略大小寫與空白）→ 寫 `envs/vlm/.ready`，內容 `{"backend": "openai", "endpoint_host": …, "model": …, "checked_at": …}`。`available()` 對 `openai` **不要求** `.ready`（§4.1）；`.ready` 只供 Settings 顯示「上次測試時間」。
  - `backend = "none"` → 印出「先在 aidoc.toml 或 Settings 選擇後端」並 exit 1。
- Settings 卡片的「測試連線」按鈕對應 `POST /api/system/vlm/check`（§10）。

## 10. API 與 UI

### 10.1 API（前綴 `/api`）

| 方法 + 路徑 | 說明 |
|---|---|
| `GET /system` | `engines.vlm` 在既有 `{installed, ready, checked_at, version, …}` 之外多 `backend`、`remote` (`bool`)、`endpoint_host`、`model`、`local_endpoint`、`budget: {used_today, limit, requests_today} \| null`、`roles: {fallback: bool, repair: bool}` |
| `POST /system/vlm/check` | 執行 §9 的連線測試（遠端）或自我檢查（本機；不重裝）；`202` + 進度走 SSE `setup.log`／`setup.done`（`engine: "vlm"`），結果寫 `.ready`；`409 setup_running`／`engine_busy` 同既有規則 |
| `GET /settings` · `PUT /settings` | `[vlm]` 區段；`api_key` 遮罩 `"***"`；驗證規則見 §8.3；`remote_enabled: true` 必須與 `backend: "openai"` 同時成立否則 `422` |
| `GET /system/vlm/usage?days=30` | `vlm_usage` 彙總（Settings 卡片的用量圖） |

### 10.2 Web

- **Settings → 轉換引擎**：第四張 `EngineCard`（`ABOUT.vlm = {what: "VLM OCR：最後一道 fallback 與逐頁修復", gpu: 依後端}`）。卡片內容依後端：
  - `none`：「尚未選擇後端」＋ 後端 Select（none／local／openai）。
  - `local`：與其他引擎相同的安裝／重新安裝／log；preset Select；詳細列 `runtime`（llama.cpp tag）、`preset`／`quant`、`device`、`GPU 層數`；提示「不抽取圖片」。
  - `openai`：欄位 `base_url`、`model`、`api_key`（password 輸入；已設定時顯示 `***`）、`request_timeout_s`、`max_concurrent`、`remote_max_pages_per_doc`、`remote_daily_page_budget`；**同意框**（§4.6 文字；loopback 端點顯示藍色資訊、非 loopback 顯示紅色警示 `TriangleAlert`）；「測試連線」按鈕（SSE log 面板同安裝 log）；用量 `Meter`（今日 N／預算 M 頁，30 天 sparkline 重用 `features/mcp/Sparkline.tsx`）。
  - 兩個角色開關：「當作最後一道 fallback」「用於逐頁修復」。
- **Library**：引擎篩選多 `vlm`；卡片對 `engine == "vlm"` 顯示引擎名，遠端時加小字 `遠端`；品質徽章不變。
- **Document 檢視**：頁提示文字（`pageWarnings.ts`）在 `repaired_by` 以 `vlm:` 開頭時顯示「第 N 頁：文字層損壞，已用 VLM OCR 修復（<model>）」；PDF 側被 VLM 修復的頁外框用資訊色（與現行已修復頁相同）。JSON 分頁顯示 `vlm` 區塊。
- **Jobs 時間軸**（`buildTimeline.ts`）：attempt 的引擎名 `vlm` 加後端副標（`vlm · openai · qwen2.5-vl-7b`）；修復 log 行 `repair: vlm:openai:… on N page(s)` 既有顯示。
- **Convert 頁進階選項**：引擎 Select 多 `vlm`（只在 `available` 時啟用）；勾選框「本次不使用遠端 VLM」對應 `--no-vlm-remote`（只在遠端後端啟用時顯示）。
- **MCP**：`schemas.py` 的 `engine` Literal 加 `"vlm"`；`list_documents`／`search_library` 篩選可用；其餘不變。

## 11. 測試

### 11.1 假 VLM 端點（`tests/fakes/vlm_server.py`，stdlib only）

- `http.server.ThreadingHTTPServer`，背景執行緒，隨機 port；實作 `GET /v1/models`、`POST /v1/chat/completions`。
- 行為由 JSON 情境檔（`AIDOC_FAKE_VLM_SCENARIO`）決定，鍵與 fake engine scenario 同風格：`default`、`rules[{match: {page, request_no, model}, behavior}]`。行為：`ok`（回「`# Page k`＋情境指定的文字」：runner 的 user 文字含 `Page k of n`，假端點解析 k 取 `pages[k]` 的文字；沒給就用通用句）、`ok_fenced`（整頁包 ```` ```markdown ````）、`blank`、`refuse`（「I'm sorry, I can't help with that.」）、`length`（`finish_reason: "length"` 且內容截半）、`rate_limit`（429 + `Retry-After: 1`）、`server_error`（500）、`unauthorized`（401）、`model_missing`（404）、`bad_request`（400）、`slow`（延遲 `delay_s`）、`hang`（永不回應，測逾時）、`no_usage`（省略 `usage`）。
- 記錄每個請求到 `calls.jsonl`（時間、path、model、`Page k of n`、影像 bytes、header 有無 Authorization）供斷言：**遠端未啟用時 0 個請求**、金鑰以 `Authorization: Bearer` 傳、影像長邊 ≤ `max_image_px`。
- 它本身有單元測試（情境解析、429 計數）。

### 11.2 單元（不需 GPU、不連網）

- `router`：五條規則都以 `vlm` 收尾；`available["vlm"]=False` 時與現行完全相同（既有 `test_rules` 參數化加一組 `ALL_WITH_VLM`）；`page_cap`／`budget` 讓 `vlm` 進 `missing` 並帶原因。
- `repair.repair_order`：三種主引擎的順序；`use_for_repair=false` 時沒有 V；預算不足時沒有 V。
- `config`／`settings API`：`[vlm]` 驗證、遮罩、`remote_enabled` 與 `backend` 的一致性、`base_url` 含 `@` 被拒。
- `vlm_runner`（直接 import 其函式）：頁面渲染尺寸、prompt 組裝（`prompt_version`、繁中／英文、`paddle_tasks` vs `generic`）、回應清理（圍欄、尾句、`<!-- blank -->`）、拒答偵測、截斷重試、退避序列、`Retry-After` 解析、`usage` 累加、`api_key_env` 讀取、金鑰不出現在 `request.json`；本機 server 管理以一個**假 `llama-server`**（`tests/fakes/fake_llama_server.py`：接受相同旗標、提供 `/health` 與 `/v1/chat/completions`，可模擬啟動慢、啟動即死、中途死亡、印 OOM 字串）測試啟動／就緒／關閉／孤兒清理。
- `vlm_runtime`：下載清單 sha256 驗證（以本機假 HTTP 檔案伺服器供檔）、解壓、manifest 寫入、缺檔時 `available()` False。
- `VlmEngine`：`available()` 三種後端、`engine_opts()` 依後端／preset、`oom_downgrade()` 兩階、逾時放大。
- `Store.vlm_usage`：UPSERT、當日彙總、跨日。
- `quality`：`vlm_low_overlap` advisory 計算；`page_map_method="vlm_per_page"` 被接受。
- `_vlm_prompt`：改 prompt 文字而沒改 `prompt_version` → 測試失敗（以 prompt 的 sha256 釘住版本）。

### 11.3 可靠性（假引擎 + 假端點）

- 三個假引擎全 `low` → 第四個 `vlm`（fake 名稱 `vlm`，`FakeEngine` 支援）→ `done`；`tried` 四筆。
- `garbled`：Docling 主引擎、A／B 修不好（`garbled_sticky` 對 docling／mineru）、VLM 修好 → `repaired_by` 以 `vlm:` 開頭、`pages_unrepaired == 0`。
- 遠端未啟用：同上情境、假端點 0 個請求、頁留 `warn`。
- 預算：`remote_daily_page_budget = 5`、文件 10 頁 → 路由 `missing: vlm(budget)`；修復 3 頁 → 可用；再修 3 頁 → 不可用。
- `hang` → 單頁逾時 → 重試 → `transient` → 主程式重試 → fallback 無 → 收尾為最高分候選；取消在等待中生效（`cancel` 事件 → runner tree 被殺、`cancelled`）。
- `refuse`／`length` → `failed_pages` → `page_conversion_failed` → `warn`；sidecar `vlm.refused_pages`。
- Ctrl+C／server 中斷後重跑：VLM 主引擎已完成的段沿用（`opts_key` 含 `vlm` 後端與 model：**`options_key()` 需加入 `vlm_backend`、`vlm_model`**，否則換 model 後舊段被沿用）。

### 11.4 API／Web

- `/api/system` 的 `engines.vlm` 欄位；`POST /system/vlm/check` 對假端點成功／401 失敗的 SSE 事件；`PUT /settings` 遮罩。
- Vitest：`EngineCard` 四種狀態渲染、同意框才能啟用、loopback 判斷（`isLoopbackUrl()`）、`pageWarnings` 的 VLM 文字、timeline 副標。
- Playwright：Settings 選 `openai` → 填假端點 → 勾同意 → 測試連線成功 → 轉換一份三引擎皆 `low` 的假文件 → Library 顯示 `vlm`（一條 e2e 即可）。

### 11.5 慢測試（`@slow`，GPU／網路）

- 本機：`AIDOC_REQUIRE_MANUAL=1` 模式下 `require_engine("vlm")`；`scanned_cht.pdf`、`scanned_mixed.pdf`、`page.png` 強制 `--engine vlm` → 字元 recall ≥ 0.95（與 `make_fixtures.py` 的 ZH／EN 字串比）且等級 `ok`／`warn`；`hfad_cover_p1.pdf` 以 Docling 主引擎、停用 A／B（測試用 `AIDOC_REPAIR_ONLY=vlm`）→ 修復含 `Wouldn't it be dreamy`；`formula.pdf` 輸出含 `$`（`paddle_tasks` 風格下若 spike 判定公式只以文字輸出，此斷言改為「含 ∑ 或 \\sum」並在 spike write-up 註記）；`text.pdf` 頁 2 有表格（同上，依 spike 決策規則 1 的表格結論調整）。
- 遠端真實端點：`AIDOC_VLM_REAL_BASE_URL`／`_MODEL`／`AIDOC_VLM_API_KEY` 有值才跑（否則 skip；驗收模式不強制，因為需要外部服務）；同上斷言但 recall 門檻 0.90。
- 效能記錄：每頁秒數、峰值顯存（本機）、tokens／頁（遠端）寫進 spike write-up 與 benchmark。

## 12. 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| llama.cpp CUDA 13 預編譯在 sm_120 崩潰或極慢；`cudart` DLL 與驅動不合 | 本機後端無法交付 | spike S1 路徑 (b)→(c)→只交付遠端的決策規則（§3.4）；二進位放 `data/tools/`、不碰主程式與其他 env |
| GGUF 路徑沒有版面模型：整頁 `OCR:` 的表格／閱讀順序弱於完整 PaddleOCR 管線 | VLM 作主引擎時 `missing_table` 可能把它判 `low` | spike 量化；兩段式 `Table Recognition:` 選項；README 明寫「VLM 引擎不抽圖、表格結構依 preset」；完整管線可由 WSL2 自架後以 `openai` 接入 |
| llama.cpp 升版改旗標名／GGUF 檔名變動 | setup 失敗 | tag、URL、sha256 全部釘死在 `vlm_runtime.py`，升版是一個有測試的 PR |
| VLM 幻覺（流暢但不存在的文字） | 品質模型抓不到 | 對齊抽查 + `vlm_low_overlap` advisory 先收數字；benchmark 以正解量化；README 明寫 |
| 遠端費用／速率 | 失控的帳單、429 | 每日頁數預算、單檔上限、並行上限、429 退避；用量表與 Settings 顯示 |
| 隱私：頁面外送 | 法規／信任 | 預設關閉、明確同意、每次 log、sidecar 記主機；loopback 例外清楚定義 |
| prompt 改動造成輸出風格漂移 | 品質分數不可比 | `prompt_version` 釘死（測試以 sha256 檢查）；改 prompt 必跑 benchmark |
| 修復頁風格與主引擎不同 | 少數頁不一致 | V 排最後；只在 A、B 失敗後 |
| 大 PDF 全部落到 VLM | 數小時／高費用 | 路由順序把 VLM 排最後；遠端單檔上限；本機超過 400 頁 log 預估時間 |
| 端點相容性差異（`detail` 不支援、`usage` 缺、回 Markdown 圍欄） | runner 失敗或髒輸出 | 不送非必要欄位（`detail` 僅 OpenAI 系端點送）、`usage` 可缺、回應清理有測試；假端點涵蓋這些變體 |
| `ENGINE_NAMES` 增加影響既有測試與 UI | 回歸 | Task 0 全面搜尋 `ENGINE_NAMES`／`EngineName` 使用點；`backend="none"` 時行為與現行完全相同是驗收項 |

## 13. 驗收標準

| # | 項目 | 標準 |
|---|---|---|
| 1 | 零影響 | `backend = "none"`：`uv run pytest -q` 全綠，`route()` 結果與現行逐字相同（參數化測試），Settings 多一張「尚未選擇後端」卡片 |
| 2 | 路由 | 五條規則以 `vlm` 收尾；`page_cap`／`budget` 進 `missing`；`--engine vlm` 可用 |
| 3 | 修復 | 三種主引擎的順序如 §6；VLM 修復頁 `repaired_by` 以 `vlm:` 開頭；拒答／截斷頁不被接受 |
| 4 | 隱私 | 遠端未啟用時假端點 0 請求；金鑰不在 `request.json`／log／sidecar／SSE（測試 grep）；sidecar 有 `endpoint_host`、`pages_sent` |
| 5 | 預算 | 每日預算與單檔上限各有可靠性測試；用量表跨程序一致 |
| 6 | 本機後端（若 S1 通過） | `scanned_cht`／`scanned_mixed`／`page.png` recall ≥ 0.95；`hfad_cover_p1.pdf` 以 VLM 修復成功；每頁 ≤ 10 s、峰值顯存 ≤ 6 GB；`aidoc setup vlm` 在乾淨 `data/` 下可重現（sha256 全對）；取消轉換後無 `llama-server` 殘留 |
| 7 | 遠端後端 | 對假端點所有行為有測試；對一個真實本機端點（LM Studio／Ollama）手動驗收並記錄 model、每頁秒數、tokens |
| 8 | UI | 同意框未勾無法啟用；loopback 顯示資訊色、非 loopback 顯示警示；Document 頁提示有 VLM 文字 |
| 9 | 回歸 | `uv run pytest`、`pnpm --dir web test`、`pnpm --dir web e2e` 全綠；`AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow -q` 在 GPU 機全綠（本機後端未交付時 `require_engine("vlm")` 的測試以 `backend` 設定 skip，並在驗收紀錄註明） |

## 14. 對主 spec 與 plan index 的修訂

- 主 spec §2 工具表加 `vlm` 一列（PaddleOCR-VL／OpenAI 相容端點；授權 Apache-2.0／依端點）；§2「Marker（未來可選）」段改為「Marker／Chandra 不採用（授權）」並指向本文件 §2.3。
- 主 spec §4 判斷規則表依 §5 更新；§8.2 失敗分類加 §4.7 的列。
- 主 spec §5 sidecar 加 `vlm` 區塊；§6 Settings 頁「三個工具」改「四個引擎」；§9 安裝加 `aidoc setup vlm`；§11 非目標刪「LLM 輔助修正輸出」以外不動（VLM OCR 不是 LLM 後處理）。
- 逐頁 spec §4 決策 4 修復順序加 V；§8.2 步驟 5 加 VLM 專屬驗收。
- index：§0 加 `vlm` 規則（主程式不裝 paddle／torch；`vlm_runner.py` 在主程式解譯器執行、stdlib + pymupdf；本機 runtime 是 `data/tools/llamacpp/` 的預編譯二進位，`envs/vlm/` 只有 `manifest.json` 與 `.ready`）；§1 layout 加 `engines/vlm.py`、`runner/vlm_runner.py`、`runner/_vlm_prompt.py`、`runner/selfcheck_vlm.py`、`vlm_runtime.py`、`tests/fakes/vlm_server.py`、`tests/fakes/fake_llama_server.py`；§3 `ENGINE_NAMES` 加 `"vlm"`、`RawResult.extra`、`ConvertOptions.vlm_remote`；§4 schema v5 `vlm_usage`；§5 runner 協定加 `vlm` 的 `engine_opts` 鍵與 `result.json.vlm`、`page_map_method` 枚舉加 `vlm_per_page`；§6 fake scenario 的 `engine` 可為 `vlm`；§7 `[vlm]`；§8 API 新列；§2 新增 A21（VLM 永遠最後，含排在 MarkItDown 之後）、A22（遠端後端的 privacy 規則：預設關、loopback 例外、每次 log）、A23（`options_key()` 含 VLM 後端與模型）。詳細文字由實作計畫 Task 0 寫入。
