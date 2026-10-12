# AI-Friendly Doc 轉換流程 — 設計文件

- 日期：2026-09-24
- 狀態：待審核（v3：spec review 修正——引擎 API 查證、Windows 細節、資料表補齊、實作分期）

## 1. 目標

把任何類型的文件轉成 AI-friendly 格式（Markdown + 結構化中繼資料），同時服務三種用途：

1. **臨時餵給 LLM**：單檔轉成乾淨 Markdown。
2. **RAG／知識庫**：批次轉換 + 帶頁碼與標題路徑的切段輸出。
3. **Claude Code skill**：對話中說「轉一下這份檔案」，自動挑工具轉換。

另有 **Web 應用**（SaaS 級外觀與操作）做為主要操作介面。CLI、skill、Web 共用同一個核心 pipeline，判斷邏輯只存在一處。

### 使用情境

個人／團隊內部工具（非多租戶 SaaS）。預設只綁 `127.0.0.1`；`aidoc serve --host 0.0.0.0 --token <token>` 可開放內網（例如 Tailscale）並啟用 token 驗證。

### 支援的輸入

Office（docx/pptx/xlsx）、電子 PDF、掃描 PDF／圖片（需 OCR）、含公式或複雜表格的學術技術文件、HTML、EPUB、CSV、音訊等 MarkItDown 支援的格式。

注意：MarkItDown 的音訊轉錄走 `SpeechRecognition` 的 Google Web Speech API（**需要網路、音訊會送到 Google**），mp3／m4a 另需系統有 ffmpeg；沒有離線後端。第一版音訊只做「有裝就能用」，不列入成功標準的 fixture。

### OCR 語言

繁體中文 + 英文（含中英混排）為預設；`--lang en` 可切純英文模型（只影響 Docling 路徑且只在 Docling 使用 EasyOCR 時有獨立英文模型；預設的 RapidOCR 對所有語言載入同一個多語 PP-OCRv6 模型；MinerU 4.x 沒有語言參數，見第 4 節與第 13 節）。

CLI／API 的 `lang` 值只有兩個：`cht`（預設，繁中＋英文）與 `en`。各 engine 的對應寫在第 4 節「OCR 語言設定」，其他地方不出現 engine 專屬的語言代碼。

### 成功標準

- 每種 fixture 類型都能產出品質檢查為 `ok` 的 Markdown。
- 單一檔案失敗不中斷批次作業。
- server 或 engine 在任何時間點被中斷，重啟後能從中斷處續轉（大型 PDF 以段為單位），且不留下半套輸出。
- 三個工具可獨立安裝、獨立升級，互不破壞。

## 2. 使用的工具

| 工具 | 定位 |
|---|---|
| [MarkItDown](https://github.com/microsoft/markitdown) | 輕量；Office／HTML／EPUB／CSV／音訊等。**不會把圖片寫到磁碟**（docx 輸出被截斷的 data URI、pptx 輸出 `![](PictureN.jpg)` 佔位）；runner 需自行從 docx（`word/media/*`）／pptx（`shape.image.blob`）抽圖到 `assets/` 並改寫連結 |
| [Docling](https://github.com/docling-project/docling) | 電子 PDF 的版面、表格、閱讀順序。安裝 `docling[easyocr]`（2.x 的 `docling` 是 meta 套件，實體是 `docling-slim[standard]`） |
| [MinerU](https://github.com/opendatalab/mineru) | 掃描檔、OCR、公式、複雜版面（GPU）。4.x 以 `--tier basic\|standard\|advanced` 取代舊的 backend：`basic` = ONNX／torch 小模型（舊 pipeline），`standard`／`advanced` 再加上 1.2B VLM（預設用 llama.cpp GGUF；`[full]` extra 在 Windows 是 LMDeploy，**RTX 50 系列上有 kernel 不相容回報，第一版不用**） |

**Marker（未來可選）**：功能與 Docling／MinerU 重疊，且為 GPL-3.0 + 模型權重有商用限制，第一版不實作。若 fixture 實測發現某類文件兩者皆處理不佳，再以 `engines/marker.py` + `envs/marker/` 加入為該類備援。

**版本基準**：本文件以 2026-09 的版本為準——MinerU **4.0.x**（2026-09-16 起的重寫版，CLI／SDK 與 2.x／3.x 完全不同）、Docling 2.x、MarkItDown 0.1.8。實作時以 `envs/*/uv.lock` 鎖定。

**授權提醒**：本專案定位為內部工具。若未來改為對外提供服務，需重新檢視各工具的授權條款：MinerU 4.x 為自訂授權（Apache-2.0 加附加條件：超過一定規模需商業授權、線上服務需顯著標示；3.1 之前為 AGPL-3.0）、主程式用的 PyMuPDF 為 AGPL-3.0（有商業授權可選）；MarkItDown 與 Docling 為 MIT。Marker 為 GPL-3.0 且模型權重有商用限制。

## 3. 架構

```
ai-friendly-doc/
├─ src/aidoc/                 ← 主程式（輕量，不裝 torch）
│  ├─ cli.py                  ← aidoc convert / batch / chunk / setup / serve
│  ├─ pipeline.py             ← 單一 task 的執行流程（CLI 與 server 共用）
│  ├─ probe.py                ← 檔案偵測
│  ├─ router.py               ← 決定主要工具與備援順序
│  ├─ segment.py              ← 大型 PDF 分段與合併
│  ├─ engines/
│  │  ├─ base.py              ← Engine 介面
│  │  ├─ runner/              ← 在各 env 內執行的小腳本
│  │  ├─ markitdown.py
│  │  ├─ docling.py
│  │  └─ mineru.py
│  ├─ quality.py              ← 品質評分
│  ├─ normalize.py            ← 統一輸出格式
│  ├─ chunk.py                ← RAG 切段
│  ├─ store.py                ← SQLite 狀態儲存
│  └─ server/
│     ├─ app.py               ← FastAPI app、靜態檔
│     ├─ jobs.py              ← 佇列、worker、啟動恢復
│     ├─ uploads.py           ← 分塊上傳與續傳
│     ├─ sse.py
│     └─ api/*.py
├─ web/                       ← Vite + React + TS + Tailwind + shadcn/ui
├─ envs/                      ← 各工具獨立 uv 環境（pyproject.toml + uv.lock 進版控，.venv 不進）
│  ├─ markitdown/pyproject.toml
│  ├─ docling/pyproject.toml  (torch cu128/cu130，見「環境隔離」)
│  └─ mineru/pyproject.toml   (torch cu128/cu130)
├─ data/                      ← aidoc.db、aidoc.lock、uploads/、work/（執行期產生，不進版控）
├─ skill/SKILL.md
└─ tests/
   ├─ unit/
   ├─ reliability/            ← 假 engine 的中斷／續傳情境
   └─ fixtures/
```

2026-10-05 修訂：加 `src/aidoc/{markers,verify,verify_report}.py`（`aidoc verify`：任何 Markdown 對原始 PDF 的逐頁稽核）與 repo 根目錄的 `bench/`（公開 benchmark，不進 wheel）。見 `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md`。

### 環境隔離

- 主程式依賴僅 PyMuPDF、FastAPI、uvicorn、tiktoken、psutil、nvidia-ml-py 等輕量套件，偵測與判斷都很快。
  - `tiktoken` 第一次使用會從網路下載 BPE 檔；設定 `TIKTOKEN_CACHE_DIR` 指到 `data/` 並在 `aidoc setup` 時預先下載，離線也能切段。
  - `psutil` 用於孤兒 process 檢查與整棵 process tree 結束（見 8.4）；`nvidia-ml-py` 用於 `/system` 顯示 GPU 顯存（主程式不裝 torch）。
- 每個 engine 以 `envs/<工具>/.venv` 的 Python 透過 subprocess 執行 `engines/runner/<工具>_runner.py`，在該環境內呼叫工具的 Python API，把結果寫到指定目錄。
  - venv 的 Python 路徑跨平台不同：Windows 為 `.venv/Scripts/python.exe`，POSIX 為 `.venv/bin/python`。
  - **runner 腳本只能 import 標準函式庫與該工具本身**，不能 import `aidoc`（engine env 內沒有安裝主程式）。
  - runner 協定：參數以 JSON 檔傳入（`workdir/request.json`），結果寫 `workdir/result.json`（Markdown 路徑、圖片清單、頁界資訊、原始輸出路徑），進度以 stderr 上帶前綴的 JSON 行回報（例 `AIDOC_PROGRESS {"page": 12, "total": 40}`），其他 stderr 行視為 log。
  - runner 一律以 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 啟動，主程式讀 stderr 時用 `errors="replace"`；否則 Windows 主控台預設 cp950 會讓含中文的 log 直接讓 runner 崩潰。
- 硬體：RTX 5070 Ti（Blackwell，sm_120，16GB）。sm_120 需要 torch ≥ 2.7 且為 CUDA ≥ 12.8 的 wheel：**cu126 不含 sm_120，PyPI 上 Windows 的預設 `torch` wheel 是 CPU 版**，兩者都不能用。`download.pytorch.org/whl/cu128` 只到 torch 2.11；torch ≥ 2.12 的 Blackwell wheel 在 `whl/cu130`（需驅動支援 CUDA 13.0，R580 以上）。各 env 的 `pyproject.toml` 以 `[[tool.uv.index]]`（`explicit = true`）＋ `[tool.uv.sources]`（`torch`、`torchvision` 皆指到該 index，marker `sys_platform == 'win32' or sys_platform == 'linux'`）指定 index，並鎖進 `uv.lock`；EasyOCR 等未鎖 torch 的相依也會被 uv 導向同一 index。`aidoc setup` 的自我檢查用 `torch.cuda.is_available()` 與 `torch.cuda.get_arch_list()` 含 `sm_120` 雙重確認。三個 env 各自有 `requires-python`（MinerU 4.x 為 ≥3.10,<3.15、建議 3.12），uv 會自動下載對應版本的 Python，不受主機 Python 3.10 限制。
- 某工具未安裝時視為不可用，router 跳到備援順序的下一個，並提示 `aidoc setup <工具>`。`available()` 只檢查 `envs/<工具>/.venv` 的 Python 與 setup 自我檢查成功後寫下的 `envs/<工具>/.ready` 標記檔，不在每次判斷時啟動 subprocess。

### Engine 介面

```python
class Engine(Protocol):
    name: str
    def available(self) -> bool: ...
    def convert(self, src: Path, workdir: Path, opts: ConvertOptions,
                pages: tuple[int, int] | None,
                on_progress: Callable[[float, str], None]) -> RawResult: ...
```

- `pages`：大型 PDF 分段時該段在**原始文件**中的頁碼範圍（含頭含尾，從 1 起算）；`None` 代表整份。分段時 `segment.py` 先用 PyMuPDF 把該段實體切成一個小 PDF 放在工作區交給 engine（`src` 即為小 PDF），engine 不必依賴各工具自己的頁碼範圍參數；`pages` 供 engine／normalize 換算頁碼偏移。這樣每個 subprocess 只看到一份頁數固定的檔案，記憶體與逾時都可預估。
- `on_progress`：解析 subprocess stderr（`AIDOC_PROGRESS` 行、tqdm／log）回報段內進度與 log 行，僅供顯示。
- `RawResult`：Markdown 字串、圖片檔路徑列表、頁界資訊（若工具有提供）、工具原始輸出路徑。`normalize.py` 把它轉成統一格式。
- 同一個 task 的多個段可由**同一個 runner process 依序處理**（runner 以 stdin 逐段接收 request、每段完成就寫 result），避免每段重新載入模型（MinerU／Docling 載模型需數十秒）。每段完成即更新資料庫，中斷後仍以段為單位續轉；只是 `tasks.pid` 對應的是整個 task 的 runner。

### 各 engine 的呼叫方式與頁界資訊來源

| Engine | runner 呼叫 | 頁界 | 圖片 | 表格／公式 |
|---|---|---|---|---|
| MarkItDown 0.1.8 | `MarkItDown().convert(path).markdown`（`.text_content` 已標記淘汰） | pptx 有 `<!-- Slide number: N -->`；**PDF 逐頁擷取**（MarkItDown 自己的逐頁函式，接回後與 `convert()` 逐字相同才採用，否則無頁界 → `page_map_incomplete` → `low`；2026-10-01 修訂 §6.3）；docx 無頁界 | 不寫磁碟，runner 自行抽圖（見第 2 節） | 表格為 GFM；無公式 |
| Docling 2.x（2.130） | `DocumentConverter(format_options=...).convert(path)` → `DoclingDocument`；`convert()` 另有 `page_range=(start, end)`（1 起算、含尾）可用 | 每個元素的 `prov[].page_no` 有頁碼。`export_to_markdown(page_break_placeholder=...)` 只插固定字串且連續空白頁會漏（issue #2623），**不用**；runner 改為逐頁 `export_to_markdown(page_no=n)` 串接並自行加 `<!-- page: n -->`，跨頁元素以首頁為準（實作時以 fixture 驗證不重複） | `generate_picture_images=True`、`images_scale=2.0`，`save_as_markdown(image_mode=ImageRefMode.REFERENCED)` 寫到 `<stem>_artifacts/`，runner 搬到 `assets/` | GFM 表格（`tabulate github` 格式）；`do_formula_enrichment=True` 時公式為 `$…$`／`$$…$$` |
| MinerU 4.x | `mineru.parser.parse(path, tier=..., ocr_mode="auto", page_range="")` → `ParseResult.save(writer)` 產出 `markdown.md`、`middle_json.json`、`structured_content.json`、`images/` | `markdown.md` 沒有頁界標記；**由 `middle_json.json` 以 MinerU 官方 render plan 逐頁渲染，逐頁加標記（空白頁也加）**，逐頁結果串接後須與 `markdown.md` 逐位元組相同，否則改用公開 API 單頁渲染（2026-10-01 修訂 §6.1；不再以文字搜尋推回頁界） | `images/page_{idx}_{type}_{n}.{ext}` | 表格為 **HTML**（normalize 轉 GFM，轉不動的保留 HTML）；公式 `$…$`／`$$…$$` |

MinerU 4.x 也能直接吃圖片（png/jpg/webp/tiff…，內部轉成 PDF）與 Office 檔，但本專案只在 router 指定的類型上用它。

## 4. 偵測與判斷

### probe（PDF 每頁取樣，最多 20 頁）

- `text_ratio`：有文字層的頁數比例
- `image_cover`：圖片佔頁面面積比例（取樣頁平均）
- `math_hint`：出現數學字型（CMMI、CMSY、Symbol、Cambria Math 等）或數學符號密度高
- `layout_hint`：多欄版面（文字區塊 x 座標分布呈雙峰）
- `has_table_lines`：頁面有構成格線的線段
- 同時偵測損毀、加密的 PDF → 直接判定 `input` 類失敗

### 判斷規則（依序比對，第一條符合即採用）

| 條件 | 主要 → 備援 |
|---|---|
| 非 PDF，MarkItDown 支援的格式 | MarkItDown → Docling（僅 docx/pptx/html） |
| 圖片（png/jpg/tiff…） | MinerU → Docling |
| PDF 且 `text_ratio < 0.5` 或 `image_cover > 0.6` | MinerU → Docling |
| PDF 且 `math_hint` | MinerU → Docling |
| 其他 PDF | Docling → MinerU → MarkItDown |

`--engine <名稱>` 可強制指定，此時不走備援；指定的 engine 不支援該檔案類型（例如 `--engine mineru` 配 docx）時直接 `failed(input: engine_unsupported)`。
大型 PDF 分段時，**整份文件用同一個 engine**；每段完成後先用同一套品質指標做**段內快速檢查**（fail-fast，避免壞 engine 再多跑幾十段浪費 GPU 時間），全部段合併後再對整份文件檢查一次。任一次不合格即整份改用下一個備援重轉（已完成的段作廢），避免同一份文件混用不同 engine 的輸出風格。

### OCR 語言設定

- MinerU 4.x：**沒有語言參數**（`-l/--lang` 與 API 的 language 參數已在 4.0 移除）。OCR 一律走內建的 `ch` PP-OCRv6 模型，該模型同時涵蓋繁中、簡中與英數；`chinese_cht` 只是內部別名，仍對應 `ch`。因此 `--lang` 對 MinerU 無作用，runner 忽略它。
- Docling：`EasyOcrOptions(lang=["ch_tra", "en"])`（EasyOCR 規定中文只能與 `en` 搭配；Docling 也接受 `iso:zh-Hant`）；`--lang en` → `["en"]`。必須明確指定 `ocr_options`：Docling 2.56 起預設是 `OcrAutoOptions`，在 Windows 實際會選 RapidOCR（onnxruntime）且 `lang=["ch"]`，不是 EasyOCR。當 Docling 作為掃描檔的備援時設 `mode=OcrMode.FULL_PAGE`（舊的 `force_full_page_ocr` 已淘汰）。
  - 備選：RapidOCR 也支援 `chinese_cht`（`RapidOcrOptions(lang=["chinese_cht"])`，PP-OCRv4／v6 模型，一次只能一種語言但模型本身含英數），且不依賴 torch；若 fixture 實測 EasyOCR 在 Windows 沒吃到 GPU（docling issue #2727 未解）或品質較差，可改用 RapidOCR（GPU 需另裝 `docling[onnxruntime]`）。

### 品質檢查

以下任一條件成立即判定失敗、改用下一個備援工具：

- 平均每頁字數 < 50（排除 probe 判定為空白的頁面；非 PDF 沒有頁的概念，改看整份字數 < 50）
- 亂碼比例 > 5%（`U+FFFD`、私有使用區字元、無意義符號連串）
- probe 的 `has_table_lines` 為真，但輸出中沒有任何表格（GFM 表格或 HTML `<table>` 都算；MinerU 的 md 以 HTML 輸出表格，normalize 之後才轉 GFM）

分數 `score ∈ [0,1]` 由上述指標加權計算，`level` 為 `ok` / `low`。
全部工具都失敗時保留分數最高的結果，標記 `level: "low"`。

**2026-10-01 修訂（逐頁品質）**：品質改為逐頁——頁碼完整度（`page_map_incomplete`）、對齊抽查（`page_map_misaligned`）、文字層損壞頁（`broken_text_layer`／`garbage`，逐頁 OCR 修復後接回）都列入判定，並新增等級 `warn`（內容可用但有未修復的問題頁；不觸發備援）。規則見 `docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md` 第 5、7、8 節。門檻為初始值，依 fixture 實測調整（尤其 `has_table_lines`：表單底線、裝飾框線都會被偵測成格線，第三條規則誤判率可能偏高）。

## 5. 輸出格式

### 單檔

```
out/<檔名>/
├─ <檔名>.md
├─ <檔名>.json
└─ assets/
```

- `<檔名>` 為來源檔的主檔名（不含副檔名），並經過檔名清理：移除 Windows 保留名稱（`CON`、`NUL`…）、結尾的點與空白、`<>:"/\|?*` 等不合法字元。
- Markdown：標題階層、GFM 表格、公式 `$...$` / `$$...$$`，圖片以相對路徑 `assets/…` 引用。
- 頁界以 `<!-- page: N -->` 註解標示（工具有提供頁資訊時；各工具能提供到什麼程度見第 3 節末的「頁界資訊來源」）。
- 同名檔案衝突（例如 `report.pdf` 與 `report.docx`）時輸出目錄加上 sha256 前 8 碼。快取查詢以資料庫 `documents` 表的 sha256 → `output_dir` 為準，不靠猜目錄名；資料庫沒有紀錄時才掃描輸出目錄的 `<檔名>.json`。
- 圖片檔名：各工具命名不同（MinerU 4.x 為 `page_{idx}_{type}_{n}.{ext}`、Docling 為 `image_000001_<hexhash>.png`），加上段落前綴與深層輸出目錄後路徑可能很長；Windows 未開啟長路徑支援時 260 字元上限容易踩到。normalize 統一改名為 `assets/p<頁碼>_<序號>.<ext>`，並在 `aidoc setup` 自我檢查時警告 `LongPathsEnabled` 未開啟。

`<檔名>.json`：

```json
{
  "source": "report.pdf",
  "sha256": "…",
  "pages": 42,
  "engine": "mineru",
  "tried": [{"engine": "mineru", "score": 0.93, "reasons": []}],
  "segments": 2,
  "probe": {"text_ratio": 0.1, "image_cover": 0.85, "math_hint": false},
  "quality": {"score": 0.93, "level": "ok", "reasons": []},
  "lang": "cht",
  "elapsed_s": 38.2,
  "aidoc_version": "0.1.0"
}
```

2026-10-01 修訂：`quality` 另含 `page_check`、`page_map`（PDF：`expected`／`found`／`coverage`／`missing`／`method`／`alignment`）、`pages`（被標記頁，含 `repaired_by`）、`pages_flagged`、`pages_unrepaired`；`probe` 另含 `broken_fonts`、`broken_font_pages_count`。欄位定義見 `docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md` 第 5.4 節。

2026-10-05 修訂：`aidoc verify out/<stem>/ --json report.json [--html report.html]` 產生逐頁稽核報告（不改任何輸出檔、不寫資料庫）；格式見 `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` 第 2.4 節。

### 批次（CLI）

`aidoc batch <輸入目錄> -o <輸出目錄> [--force] [--engine X] [--lang en]`

- 以 sha256 快取：輸出 JSON 的 sha256 相同即跳過；`--force` 重新轉換。
- CLI 批次同樣寫入 SQLite（第 8 節），Web 上看得到 CLI 啟動的工作；CLI 中斷後再執行同一指令即續轉。續轉的依據是 task 以 `(sha256, output_dir)` 為唯一鍵：再跑同一指令會建立新的 job，但同鍵且未完成的 task／segment 直接沿用，不重頭來。
- 輸出 `out/_manifest.jsonl`：每檔一行 `{source, status, engine, score, level, error?}`；每個 task 結束時由資料庫重新產生整份（先寫暫存再改名），批次中斷也有部分結果可看。
- 列出輸出目錄內容時一律略過 `.tmp/`、`.trash/`、`_manifest.jsonl`、`chunks.jsonl`。

### RAG 切段

`aidoc chunk <輸出目錄> [--max-tokens 800]`

- 依標題階層切段，超過上限時在段落邊界再切；表格與公式區塊不切開（單一表格超長時整塊保留並標記 `oversized: true`）。
- token 以 `tiktoken` 的 `cl100k_base` 估算。
- 輸出 `chunks.jsonl`：`{id, source, heading_path, page_start, page_end, text, oversized?}`；文件沒有頁界資訊時 `page_start`／`page_end` 為 `null`。
- 不做向量化（由使用者自行選擇向量資料庫）。

## 6. Web 應用

### 技術

- 前端：Vite + React + TypeScript + Tailwind CSS + shadcn/ui；API 狀態用 TanStack Query，即時更新用 SSE（`EventSource`，斷線自動重連）。
- Markdown 渲染：react-markdown + remark-gfm + rehype-katex；PDF 預覽：pdf.js。
- 後端：FastAPI + uvicorn。開發時 Vite proxy 到 FastAPI；正式使用時 `pnpm build` 產出靜態檔由 FastAPI 提供，`aidoc serve` 一個指令啟動。
- 版面：左側導覽（Convert / Jobs / Library / Chunks / Settings）、頂部狀態列（GPU、佇列數）、深淺色模式。

### API（前綴 `/api`）

| 方法 | 路徑 | 用途 |
|---|---|---|
| POST | `/uploads` | 建立上傳：`{filename, size, sha256}` → `upload_id` |
| PUT | `/uploads/{id}?offset=N` | 上傳一塊（8MB） |
| HEAD | `/uploads/{id}` | 查詢已上傳位置（續傳用） |
| POST | `/jobs` | 建立工作：`{inputs: [upload_id \| 本機路徑], engine, lang, force}` |
| GET | `/jobs` · `/jobs/{id}` | 工作清單與詳情（含 tasks、segments） |
| POST | `/jobs/{id}/cancel` | 取消（結束 subprocess） |
| POST | `/tasks/{id}/retry` | 重試單一 task（含「用新版本重轉」） |
| GET | `/events` | **SSE**：所有 job／task／segment 狀態變化與 log 行 |
| POST | `/queue/pause` · `/queue/resume` | 暫停／繼續佇列 |
| GET | `/documents` · `/documents/{id}` | 已轉換文件 |
| GET | `/documents/{id}/markdown` · `/documents/{id}/assets/{path}` · `/documents/{id}/source` · `/documents/{id}/download.zip` | 預覽與下載 |
| DELETE | `/documents/orphaned` | 清除 orphaned 紀錄 |
| POST | `/chunks` | 對指定文件切段，回傳 `chunks.jsonl` |
| GET | `/system` | 各工具安裝狀態、GPU 名稱與顯存、佇列長度、磁碟剩餘空間 |
| POST | `/system/setup/{engine}` | 背景執行安裝，進度經 SSE |

所有回應帶 `workspace: "default"`，為日後多工作區預留；第一版不實作多工作區與帳號。

- **安全**：`POST /jobs` 的「本機路徑」等於讓呼叫端讀取 server 主機上的任何檔案（轉換結果會回傳內容）。此輸入只在請求來自 loopback、或 `--token` 驗證通過時接受；未帶 token 而綁 `0.0.0.0` 時 server 拒絕啟動。
- **SSE 重連**：每個事件帶遞增 `id`（來自資料庫 `events` 表的流水號），`EventSource` 重連時自動送 `Last-Event-ID`，server 補送遺漏事件；補不到（太舊）時回 `resync` 事件，前端讓 TanStack Query 重新抓 `/jobs`。
- `HEAD /uploads/{id}` 以 `Upload-Offset` header 回傳已收位元組數。
- 2026-10-05 修訂：`POST /verify`（multipart 上傳、`{doc_id}`、本機路徑三種形狀；不寫資料庫），見 `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` 第 2.6 節。

### 頁面

1. **Convert**：拖放區（分塊上傳、顯示上傳進度、斷線自動續傳）、本機資料夾路徑輸入；進階選項收合（工具、語言、強制重轉）。送出後跳到該工作頁。
2. **Jobs**：工作清單，每列有進度條與狀態徽章。詳情頁是 **task 時間軸**（probe 結果 → 選用工具 → 備援切換與原因 → 品質分數），大型 PDF 顯示段落進度（「第 120/300 頁」），下方即時 log。取消、重試、暫停／繼續佇列按鈕。
3. **Library**：已轉換文件的卡片／表格檢視，可依檔名、工具、品質等級篩選。
4. **Document 檢視**：左右兩欄——左側原始檔預覽（PDF 用 pdf.js、圖片直接顯示；原始檔已移除時顯示提示）；右側三分頁：Markdown 渲染（KaTeX、GFM 表格）、原始碼、JSON。**捲動時依 `<!-- page: N -->` 左右同步**（react-markdown 預設會丟掉 HTML 註解，需自訂 remark plugin 把 `<!-- page: N -->` 轉成帶 `data-page` 的錨點元素）。右上：複製 Markdown、下載 zip、送去切段。
5. **Chunks**：選文件、設定 max tokens，預覽切段結果（標題路徑、頁碼範圍），下載 jsonl。
6. **Settings**：三個工具的安裝狀態卡片（一鍵安裝、安裝 log）、GPU 資訊、預設語言、輸出目錄。

2026-10-01 修訂：Library 顯示「頁碼不完整」「N 頁有問題」徽章、橫幅與一鍵重新轉換（`POST /api/documents/reconvert`）；Document 檢視加「跳至頁」、頁碼不完整橫幅與逐頁提示；API 新增 `flags`／`page_summary`、`?flag=`、rescan 重新評估。見 `docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md` 第 9 節。

## 7. Claude Code skill

`skill/SKILL.md`：

- 觸發詞：「轉成 markdown」「把這份文件轉給 AI 讀」「convert this doc」等。
- 行為：執行 `aidoc convert <檔案> -o <暫存或指定目錄>`，讀出 `.md`；`level: "low"` 時主動告知並附上 `reasons`。
- 判斷邏輯不寫在 skill 裡，只呼叫 CLI。
- 2026-10-05 修訂：使用者懷疑某頁時，可執行 `aidoc verify out/<stem>/ --json -` 看逐頁報告（exit code 0／1／3／4）。

## 8. 批次、進度、續傳與錯誤處理

### 8.1 狀態儲存：SQLite

- 標準函式庫 `sqlite3`，WAL 模式，`busy_timeout` 5 秒（CLI 與 server 可能同時開啟），檔案 `data/aidoc.db`。
- 資料表：
  - `jobs(id, created_at, options_json, status, origin)`（`origin` 為 `cli` / `web`）
  - `tasks(id, job_id, source_path, work_path, sha256, size, mtime, lang, engine, tried_json, attempt, status, error_kind, error_msg, quality_json, output_dir, pid, created_at, updated_at)`；`(sha256, output_dir)` 唯一（供 CLI 續轉與快取查詢）
  - `segments(id, task_id, idx, page_start, page_end, status, attempt, output_path)`
  - `documents(id, sha256, source_path, output_dir, engine, quality_json, pages, lang, aidoc_version, created_at, status, work_copy_path, work_copy_expires_at)`；`status` 為 `ok` / `low` / `orphaned`。這是 Library 與 `/documents` API 的資料來源，也是快取查詢的索引
  - `uploads(id, filename, size, sha256, received, created_at, status)`；server 重啟後續傳位置以此表加上磁碟上的檔案大小為準
  - `events(seq, ts, kind, ref_id, payload_json)`：SSE 事件流水，供重連補送；保留最近 N 筆（例如 10,000）
- 每次狀態改變**先寫資料庫、再推 SSE**。資料庫是唯一依據，SSE 只是通知。
- CLI 與 server 用同一個資料庫；同一時間只允許一個佇列執行者，以 `data/aidoc.lock` 保證：
  - 不用 `fcntl`（Windows 沒有）。作法：以 `O_CREAT | O_EXCL` 建立 lock 檔並寫入 `{pid, started_at, host, port, token?}`；已存在時用 `psutil` 檢查該 pid 是否仍是 aidoc process，不是即視為陳舊 lock 直接覆蓋。
  - server 執行中時 CLI `batch` 讀 lock 檔取得 server 位址，把工作送進 server 佇列，然後用 SSE 追蹤進度並在終端機顯示；Ctrl+C 只停止追蹤，不取消工作（要取消用 Web 或 `aidoc cancel <job>`）。

### 8.2 狀態機

```
task: queued → probing → converting → checking → done | low | failed | skipped
                            ↑ 失敗且還有備援 ─┘
        任何非終態 → cancelled（使用者取消）
segment: queued → converting → done | failed
```

- 每個轉換記錄 `attempt`、`engine`、`reason`（存 `tasks.tried_json`），對應 Web 時間軸。
- **失敗分類**：
  - `transient`（逾時、GPU 顯存不足、process 被結束）：自動重試，最多 2 次，退避 5s → 30s；顯存不足時以較小 batch 重試。
    - 顯存不足的判定：runner 結束碼非 0 且 stderr 含 `CUDA out of memory` / `OutOfMemoryError`。
    - 「較小 batch」的實作因工具而異：Docling 調低 `settings.perf.page_batch_size`；MinerU 4.x 已移除 `MINERU_VIRTUAL_VRAM_SIZE`（顯存用量改為自動），沒有 batch 旋鈕，OOM 時改以**降一級 tier** 重試（`standard` → `basic`，不載入 VLM），仍 OOM 才視為 `engine` 失敗換備援。
  - `engine`（品質不合格、工具回報錯誤）：換下一個備援工具。
  - `input`（檔案消失、被修改、損毀、加密、engine 不支援該類型）：直接 `failed`，不重試。
- `skipped`：sha256 快取命中。
- `cancelled`：已完成的段保留；之後對同一 task 按「重試」會從未完成的段續轉。
- 取消／逾時時結束的是**整棵 process tree**（`psutil.Process(pid).children(recursive=True)` 逐一 `kill()`），因為 MinerU 會再 spawn 子 process；Windows 沒有 SIGTERM，`terminate()` 即為硬殺，runner 不需要也不能依賴優雅關閉。

### 8.3 大型 PDF 分段轉換

- 超過 **40 頁**的 PDF 切成每段 40 頁，每段一筆 `segment`。
- 中斷後重跑只轉尚未完成的段。
- 進度 = 已完成頁數 ÷ 總頁數；段內進度來自 `on_progress`，僅供參考。
- 所有段完成後依序合併：頁碼標記加上偏移、圖片檔名加段落前綴避免衝突。
- **跨段表格**：若段落 N 結尾與段落 N+1 開頭都是表格，且欄數相同，合併為一個表格（省略第二個表頭，若其與第一個表頭相同）。只有在工具提供 bbox、且能確認前段表格觸及頁尾、後段表格從頁首開始時才合併；沒有 bbox 就不合併，寧可多一個表格也不要錯併兩個不同的表。
- 段內快速檢查與合併後的整份品質檢查見第 4 節；不合格則整份換備援。
- 段的實體切分用 PyMuPDF `insert_pdf(from_page, to_page)` 產生小 PDF（見第 3 節 Engine 介面），切分後的檔案放 `data/work/<task_id>/seg_<idx>.pdf`。

### 8.4 啟動恢復

server（或 CLI batch）啟動時：

1. 結束上次留下的孤兒 subprocess（依資料庫記錄的 `pid`，用 `psutil` 確認命令列含 `aidoc` runner 腳本路徑才結束，避免 Windows PID 重用時殺錯；連同子 process 一起結束）。此步驟必須在下一步之前，否則孤兒還在寫暫存目錄。
2. `probing`／`converting`／`checking` 的 task 改回 `queued`；分段 task 只重設未完成的段（狀態為 `converting` 的段其 `output_path` 一併清除）。
3. 清除所有已知輸出目錄（資料庫中 `tasks.output_dir` 的集合）下 `.tmp/` 內沒有對應進行中 task 的暫存目錄，以及 `.trash/`。

「暫停佇列」會等目前的段完成後才停止接新工作。

### 8.5 檔案消失或被修改

| 時間點 | 處理 |
|---|---|
| 建立工作時 | 立即計算 sha256，記錄大小與 mtime；檔案不存在直接回報錯誤 |
| 開始轉換前 | 將來源檔複製到 `data/work/<task_id>/`（上傳檔本來就在工作區；一律實際複製、不用硬連結——硬連結與原檔共用資料，轉換中原檔被就地改寫會讓後段分段讀到新內容〔P3 修訂〕）並比對 sha256：不存在 → `failed(input: source_missing)`；內容不同 → `failed(input: source_changed)`，Web 提供「用新版本重轉」 |
| 轉換過程中 | engine 只讀工作區副本，原始檔被移走不受影響 |
| 瀏覽 Library 時 | 原始檔與工作區副本都不存在時，結果照常顯示，原始檔預覽顯示「原始檔已移除」 |
| 輸出目錄被刪除 | 列出文件與啟動時以磁碟實際內容為準；資料庫中對不上的紀錄標記 `orphaned`，可一鍵清除 |

工作區副本在 task 成功完成後保留 7 天（供 Document 檢視預覽），之後清除；可在 Settings 調整。

### 8.6 原子寫入

- 轉換結果先寫到 `out/.tmp/<task_id>/`，全部完成後 rename 成正式目錄（同一磁碟分割區，rename 為原子操作）。
- **Windows 細節**（`os.replace` 對目錄的行為與 POSIX 不同）：
  - 目標目錄已存在（`--force` 重轉、重試）時 `os.replace` 會失敗，不會覆蓋。流程改為三步：先把舊目錄 rename 到 `out/.trash/<task_id>/`，再把 `.tmp/<task_id>/` rename 成正式目錄，最後刪除 `.trash/`。三步中任一步中斷，啟動恢復都能收拾（`.trash/` 直接刪、`.tmp/` 沒有對應 task 就刪）。
  - 目錄內有檔案被其他 process 開著（防毒即時掃描、搜尋索引、pdf.js 正在讀原始檔、使用者用編輯器開著 `.md`）時 rename 會丟 `PermissionError`。對 rename／刪除一律做指數退避重試（例 0.2s × 5 次），仍失敗則 task 標 `failed(transient)` 讓自動重試接手。
  - 所有 `.json`／`.md`／`.jsonl` 的原子寫入用「寫到同目錄的 `.<name>.tmp` → `os.replace`」，單一檔案的 `os.replace` 在 Windows 可覆蓋既有檔案，不需要 `.trash/`。
- `_manifest.jsonl`、`chunks.jsonl` 同樣先寫暫存檔再改名。
- 因此快取判斷（第 5 節）只會看到完整的輸出。

### 8.7 上傳續傳

- 前端把檔案切成 8MB 分塊，依序 `PUT /uploads/{id}?offset=N`；server 只接受 `offset` 等於目前已收大小的分塊。
- 前端算 sha256 不能用 `crypto.subtle.digest`（它要一次吃整個 ArrayBuffer，2GB 檔案會爆記憶體）；改用可串流的實作（例如 `hash-wasm`）在 Web Worker 內邊讀邊算，且與上傳並行。
- 斷線後前端以 `HEAD /uploads/{id}` 取得已上傳位置，從該處續傳。
- 全部收完後比對建立時提供的 sha256，不符則刪除並回報錯誤。
- 未完成的上傳 24 小時後自動清除；上傳完成的檔案在建立 job 時搬進 `data/work/<task_id>/`（同一磁碟分割區用 rename，不複製）。

### 8.8 資源保護

- 建立工作前檢查磁碟空間：剩餘空間需 ≥ 輸入總大小 × 3，不足則拒絕並提示。
- 單檔上傳上限預設 2GB，可調整。
- subprocess 逾時：每頁 60 秒、最少 120 秒（分段時以段的頁數計算），可用 `--timeout` 覆寫。沒有頁的格式（Office、HTML、音訊等）以檔案大小估：每 MB 30 秒、最少 120 秒、最多 1800 秒。逾時計時從 runner 回報第一個進度或 log 行起算，避免第一次載入模型的時間吃掉配額。
- `/system` 顯示 GPU 顯存使用量（`nvidia-ml-py`，主程式不裝 torch）。

## 9. 安裝

`aidoc setup [markitdown|docling|mineru|all]`（Web Settings 頁可一鍵執行）

- 以 `uv sync --project envs/<工具>` 建立 `.venv` 並安裝 `uv.lock` 鎖定版本（含 cu128／cu130 torch，見第 3 節）。
  - MinerU：安裝 `mineru[torch]`（基本套件不含 torch；Windows 要 GPU 必須自己裝 torch），設定 `MINERU_MODEL_SMALL_BACKEND=torch` 讓小模型走 GPU（預設 ONNX 走 CPU）。**不裝 `[full]`**（Windows 對應 LMDeploy，RTX 50 系列未驗證）。
  - 所有模型與設定放在專案內、不污染使用者家目錄：MinerU 設 `MINERU_HOME=data/models/mineru`（`config.yaml` 與 `models/` 都在其下）；Docling／EasyOCR 設 `HF_HOME`、`EASYOCR_MODULE_PATH` 指到 `data/models/`。runner 啟動時由主程式注入這些環境變數。
- 預先下載模型：MinerU `mineru-kit models download --tier <tier> --source auto`；Docling `docling-tools models download --output-dir data/models/docling layout tableformer code_formula easyocr --easyocr-lang iso:zh-Hant --easyocr-lang en`（EasyOCR 預設不在下載清單內，要明列），runner 以 `PdfPipelineOptions(artifacts_path=...)` 指向該目錄，此時 Docling 會關閉執行期自動下載，所以清單必須完整。
- Docling GPU 設定：`AcceleratorOptions(device="cuda")`、`settings.perf.page_batch_size` 預設 16（Docling 對 12–16GB 卡的建議值 16–32），OOM 時減半重試。
- 最後執行自我檢查：轉換一個內建小樣本（含一頁繁中掃描圖），並確認 GPU 可用（`torch.cuda.is_available()` 且 `get_arch_list()` 含 `sm_120`）；通過才寫 `envs/<工具>/.ready`。
- 設定檔：`aidoc.toml`（專案根目錄，可用 `AIDOC_CONFIG` 覆寫）存 MinerU tier、預設語言、輸出目錄、工作區保留天數、上傳上限等；Web Settings 頁改的就是這個檔。

## 10. 測試

- **單元測試**（不需 GPU）：`probe`、`router`、`quality`、`normalize`、`segment`（分段與合併、跨段表格）、`chunk`、`store`（狀態轉換）。
- **可靠性測試**（假 engine，不需 GPU）：
  - 轉換中結束 server → 重啟後從未完成的段續轉，已完成的段不重轉
  - 轉換前刪除／修改來源檔 → `source_missing`／`source_changed`
  - 上傳到一半中斷 → 續傳成功且 sha256 相符
  - 假 engine 模擬逾時、顯存不足、品質不合格 → 分別重試、重試、換備援
  - 殺掉 engine process → 視為 transient 重試
  - 寫入中途失敗 → 不留下正式輸出目錄
  - 正式輸出目錄已存在（`--force`）且其中一個檔案被另一個 handle 開著 → 三步 rename 在退避重試後成功或標 `transient`，`.trash/` 不殘留（Windows 專用情境，CI 在 Windows runner 跑）
- **API 測試**：FastAPI TestClient 覆蓋各端點與 SSE 事件順序。
- **前端**：Vitest 測關鍵元件邏輯（上傳續傳、SSE 狀態合併）；Playwright 跑一條端到端流程（上傳 → 轉換 → 預覽）。
- **真實樣本關卡與同步準確度**（2026-10-01 修訂）：`tests/fixtures/manual_samples.json` 列出真實樣本（不進版控），`AIDOC_REQUIRE_MANUAL=1` 時缺樣本或缺引擎環境即失敗；`web/e2e/sync.spec.ts`（假引擎 45 頁）與 `sync-real.spec.ts`（真實樣本）驗證左右同步。見 `docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md` 第 10 節。
- **verify 與 benchmark**（2026-10-05 修訂）：見 `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` 第 6 節（方言、推頁、負樣本、第三方原生輸出錄製、benchmark 管線的 `--fake` 測試與校準閘門）。
- **整合測試**（`@pytest.mark.slow`，需要 GPU）：`tests/fixtures/` 每類一個小樣本——docx、pptx、xlsx、電子 PDF、繁中掃描 PDF、中英混排掃描 PDF、公式 PDF、png，以及一份 > 40 頁的 PDF 驗證分段。斷言：引擎選擇正確、品質為 `ok`、產出檔案齊全。

## 11. 不在範圍內（第一版）

- Marker 引擎（見第 2 節）
- 帳號系統、多工作區、多租戶
- 向量化與向量資料庫整合
- LLM 輔助修正輸出
- 設計精修動畫
- 離線音訊轉錄（Whisper 系列）

## 12. 實作分期

整份規格太大，不適合寫成單一實作計畫；依相依關係切成四期，每期各自一份實作計畫、各自可交付與可測試。後一期只依賴前一期已完成的介面。

| 期 | 內容 | 交付物／驗收 |
|---|---|---|
| **P1 核心 CLI** | `envs/` 三個 uv 環境與 runner 協定、`aidoc setup`、`probe`／`router`／`quality`／`normalize`、三個 engine、`store`（SQLite）、`aidoc convert` 單檔、`aidoc batch`（含 sha256 快取、`_manifest.jsonl`）、原子寫入（含 Windows `.trash/` 三步法）| 單元測試 + 每類 fixture 的 `@slow` 整合測試；成功標準中除「續轉」一條外全部成立 |
| **P2 可靠性與 RAG** | 大型 PDF 分段（實體切分、常駐 runner、合併、跨段表格）、啟動恢復、孤兒 process 清理、失敗分類與重試、來源檔消失／修改處理、`aidoc chunk`、`skill/SKILL.md` | `tests/reliability/` 假 engine 全部情境；> 40 頁 fixture 的分段整合測試 |
| **P3 Server 與 API** | FastAPI app、`jobs.py` 佇列與 worker、`aidoc.lock` 單一執行者、CLI batch 轉送到 server、分塊上傳與續傳、SSE 與 `events` 表、`/documents`／`/system`／`setup` 端點、token 驗證 | FastAPI TestClient 覆蓋各端點與 SSE 順序；上傳中斷續傳測試 |
| **P4 Web 前端** | Vite + React 六個頁面、SSE 狀態合併、上傳 Worker（串流 sha256）、Document 左右同步捲動、Settings 一鍵安裝 | Vitest 元件測試；Playwright 一條端到端流程；`aidoc serve` 提供 build 後靜態檔 |

P1 結束即可讓 skill（第 7 節）先以 CLI 上線；P2 之前 skill 對超過 40 頁的 PDF 會整份一次轉，只是沒有續轉。

## 13. 決議事項（2026-09-24，spec review 後由主控 agent 以保守預設定案，可隨時調整）

1. **MinerU tier**：預設 `basic`（純小模型，Windows + RTX 50 上風險最低）；`standard` 為設定選項，P1 的 fixture 實測若 llama.cpp GPU 在 sm_120 可用、且品質明顯較好，再改預設。OOM 降級規則仍為 `standard → basic`。
2. **`--lang en`**：純英文的掃描 PDF／圖片在 `lang=en` 時改路由為 **Docling（EasyOCR `en`）→ MinerU**；`lang=cht` 路由不變。
   - **P2 修訂（2026-09-24，P1 驗證回饋）**：P1 spike B 後 Docling 預設 OCR 改為 RapidOCR，而 RapidOCR（docling 2.130 / rapidocr 3.9.2）對 `en` 與 `chinese_cht` 載入同一個多語 `PP-OCRv6_rec_small`，沒有英文專屬模型可選（英文 v4/v5 模型只能手動指定路徑，實測在 fixture 上沒有可量測的提升）。因此上述改路由**只在 `docling_ocr = "easyocr"` 時生效**；預設 RapidOCR 時 `lang=en` 的掃描檔與 `cht` 同路由（MinerU → Docling）。
3. **音訊**：預設**關閉**（會把音訊送到 Google）。需以設定 `enable_audio=true` 或 CLI `--allow-online-audio` 明確開啟；關閉時音訊檔判定 `failed(input: audio_disabled)`。
4. **`low` 結果的快取**：再次批次時視為快取命中（`skipped`），另提供 `--retry-low`（Web：「重轉低品質」按鈕）。
5. **Docling OCR 引擎**：先用 EasyOCR（`ch_tra` + `en`）；P1 以 fixture 比較 RapidOCR，若 RapidOCR 品質相當或更好則改用（少一個 torch 相依）。
