# Doc4AI Studio docling-serve 相容 API — 設計文件

- 日期：2026-10-05
- 狀態：待審核
- 依據：`docs/superpowers/research/2026-10-02-similar-projects.md` §7「生態整合」、§8.2 建議 8（相容 API）與 §D（RAG 平台只看解析側）
- 相關：MCP 設計 `2026-10-01-mcp-server-design.md`（token 模型、呼叫紀錄、`doc4ai:convert` scope）、逐頁品質 `2026-10-01-per-page-quality-and-page-map.md`（`<!-- page: N -->`、`quality.pages`）、契約 `plans/2026-09-24-index.md` §8、`plans/2026-10-01-mcp-server.md` §4
- 實作計畫：`docs/superpowers/plans/2026-10-05-docling-compat-api.md`
- 外部事實的查證日期皆為 2026-10-05；每一項都附來源 URL，查不到的標「（未驗證）」。

> **一句話：** 讓 Open WebUI、Kotaemon、RAGFlow、Dify 這些已經「parser 可插拔」的 RAG 平台，把 Doc4AI Studio 當成一台 **docling-serve** 用——同樣的 URL、同樣的 request／response 形狀、同樣的 `X-Api-Key`——但背後跑的是我們的路由 + 逐頁品質 + 修復管線，而且 Markdown 裡帶著頁碼。

## 1. 目標與範圍

### 1.1 為什麼是 docling-serve 相容，而不是自己的 API

調查（研究文件 §D）顯示主流 RAG 平台的文件解析層都已經「可插拔」，而 **Docling（經 docling-serve）與 MinerU 是它們共同的選項**。我們既有的 `/api/*` 是給自家 Web UI 用的（帶 `workspace` envelope、可續傳上傳、SSE），沒有任何平台能直接接。與其說服每個平台為 Doc4AI 寫外掛，不如讓 Doc4AI **說它們已經會說的話**：

- Open WebUI 的「Docling」文件抽取引擎只要填一個 server URL（加選用的 API key），就會把每份上傳檔送到 docling-serve 的 convert 端點，拿回 Markdown 切段入庫。
- 其他平台（Kotaemon、RAGFlow、Dify）的 Docling 整合方式各不相同（有的 in-process、有的 HTTP），第 8 節逐一說明「能不能接、怎麼接」，不能接的誠實標出。

好處是**使用者入口**：一個已經在跑 Open WebUI 的人，改一個 URL 就能用到 Doc4AI 的品質 fallback 與繁中 OCR，不必學我們的 UI 或 MCP。

### 1.2 範圍（本文件實作）

| 項目 | 內容 |
|---|---|
| 端點 | docling-serve **v1** HTTP API 的子集：同步 `convert/source`、`convert/file`，非同步 `…/async` + `status/poll` + `result`，以及 `/health`（第 4 節；確切路徑與 schema 以第 3 節查證結果為準） |
| 後端 | 既有 `JobQueue` + `pipeline.run_task`（路由、逐頁品質、修復、分段續跑）；**不**新開第二條轉換路徑 |
| 輸出 | `document.md_content` 保留 `<!-- page: N -->`；另附 `document.doc4ai`（doc_id、engine、quality、flagged pages、page 清單）；`md_page_break_placeholder` 選項照 docling-serve 語意支援 |
| 認證 | `X-Api-Key: <PAT>` 或 `Authorization: Bearer <PAT>`，PAT 即 MCP 的 personal access token，需 `doc4ai:convert`（讀結果需 `doc4ai:read`）；PAT **仍然**對 `/api/*` 無效 |
| 紀錄 | 每個相容請求一筆 `mcp_calls`（`method = "compat/docling"`），後台「呼叫紀錄」「連線」「Tokens」原地可見；Jobs 頁的 `origin = "compat"` |
| 相容測試 | 重放 Open WebUI 等 client **實際送出的 request 形狀**（第 9 節），同步／非同步、逾時、大檔 |
| 整合食譜 | Open WebUI、Kotaemon、RAGFlow、Dify 各一節（第 8 節；可行性分級、未驗證者標明） |

### 1.3 不在範圍內

- 完整 docling-serve API（chunking 端點 `/v1/chunk/*`、WebSocket 狀態、`/ui` playground、`return_as_file` zip、`DoclingDocument` JSON／HTML／doctags 輸出）。
- `sources[].kind = "http"`（伺服器端抓 URL）：SSRF 風險，MCP 設計 §13 已另案；本版回 `422 unsupported_source`（第 4.3 節）。
- Open WebUI 的「External」document loader 協定與 Dify／Unstructured 相容 API：第 8 節評估，若值得做另開規格。
- 多使用者／多工作區（`workspace` 持續為 `default`）。

### 1.4 成功標準

1. Open WebUI（最新 release）把「Content Extraction Engine」設為 Docling、URL 指到 Doc4AI、填入 PAT 後，上傳一份 **掃描 PDF**（繁中）與一份 **壞字型 PDF**，兩份都成功入庫，問答能引用內容；Doc4AI 後台 Jobs 看到 `origin = compat` 的工作、呼叫紀錄看到 `compat/docling convert_file ok`。
2. 以 docling-serve 自己的 OpenAPI schema 驅動的相容測試全綠：request 必填欄位、response 必有欄位、`status` 字串、錯誤形狀。
3. 一份 300 頁 PDF 經同步端點：client 不中斷（在 `compat.sync_timeout_s` 內完成，或得到明確的 `504` + 非同步替代說明）；經非同步端點：`status/poll` 回報進度、`result` 一次成功。
4. 無效／撤銷 PAT → `401`（docling-serve 風格 `{"detail": …}`）；缺 `doc4ai:convert` → `403`；每一筆都在後台可見；PAT 對 `/api/*` 仍是 401。
5. `md_content` 的 `<!-- page: N -->` 數量 = 文件頁數（PDF、頁碼完整時）；`document.doc4ai.pages[]` 與之一致。

## 2. 架構總覽

```
Open WebUI / curl / 任何 docling-serve client
        │  POST /v1/convert/file  (multipart, X-Api-Key)
        ▼
FastAPI (既有 app，同一個 port)
  ├─ /api/*            既有 Web UI API（server token / loopback）         ← PAT 無效
  ├─ /mcp              MCP Streamable HTTP（McpGate → SDK）               ← PAT
  ├─ /v1/*, /health    ★ 新：CompatRouter（PAT via X-Api-Key / Bearer）   ← PAT
  │     ├─ CompatAuth（沿用 PatVerifier、AuthFailureGuard、RateLimiter）
  │     ├─ options 轉譯（docling options → ConvertOptions）
  │     ├─ 同步：start_job_for_bytes → wait_for_task（上限 sync_timeout_s）
  │     ├─ 非同步：start_job_for_bytes → task_id = job_id；poll/result 查 Store
  │     └─ 回應組裝：md_content（含頁碼標記）+ document.doc4ai
  │     └─ CallRecorder.record(method="compat/docling", tool_name=…)
  └─ /{path}           SPA catch-all（在 /v1 之後註冊）
```

**核心原則**

1. **一條轉換管線**：相容端點只是另一個「入口」，和 MCP 的 `convert_document` 共用 `tools_convert.start_job_for_bytes`／`reserve_job`／`wait_for_task`，因此同時進行中的工作上限、磁碟檢查、magic bytes 檢查、`already_converting` 全部沿用。
2. **一套身分**：`Principal` 不變；相容端點只是多一個取 token 的 header（`X-Api-Key`）。scope、撤銷、到期、限流、auth-flood 防護都是既有程式。
3. **一本帳**：`mcp_calls` 多一種 `method`；後台不另開頁面，只在既有的三個分頁多出一種來源徽章。
4. **對外形狀以 docling-serve 為準，對內資料以 Doc4AI 為準**：`document.md_content` 就是我們的 `<stem>.md`；多出來的欄位只放在 `document.doc4ai` 與 top-level `doc4ai`，不改既有欄位的語意。

## 3. 查證結果（2026-10-05）

### 3.1 docling-serve 現況

來源：`https://github.com/docling-project/docling-serve`（releases API、`docling_serve/app.py`、`docling_serve/auth.py`、`docling_serve/settings.py`、`docs/usage.md`）；request／response／options 的 pydantic 模型現在放在 **docling 核心套件** `docling/datamodel/service/{options,requests,responses}.py`（docling-serve 下的同名檔是棄用轉接）。

- **版本**：v1.36.0（2026-10-01）。v1.35.0 加入 `enable_api_docs` 開關、移除舊的 openapi-3 端點。
- **端點**（`app.py`）：
  - `GET /health` → `{"status": "ok"}`；另有 `/ready`、`/readyz`、`/livez`、`/version`、`GET /v1/capabilities`（**不需** API key）。
  - 同步：`POST /v1/convert/source`、`POST /v1/convert/file`。
  - 非同步：`POST /v1/convert/source/async`、`POST /v1/convert/file/async`、`POST /v1/convert/source/batch` → `TaskStatusResponse`；`GET /v1/status/poll/{task_id}?wait=<秒>`；`WS /v1/status/ws/{task_id}`（`?api_key=`）；`GET /v1/result/{task_id}`。
  - 切段：`POST /v1/chunk/{hybrid|hierarchical}/{source|file}`（+ `/async`）。
  - 文件：`/docs`、`/swagger`、`/scalar`，**`/openapi.json`**（`DOCLING_SERVE_ENABLE_API_DOCS` 預設 true）——可快照做相容測試。
- **`/v1/convert/source` request**（`ConvertSourcesRequest`，舊名 `ConvertDocumentsRequest`）：

  ```json
  {"options": {…ConvertDocumentsOptions…},
   "sources": [{"kind": "file", "base64_string": "...", "filename": "x.pdf"} | {"kind": "http", "url": "...", "headers": {}} | s3 | azure_blob | google_cloud_storage | google_drive],
   "target": {"kind": "inbody" | "zip" | "s3" | "azure_blob" | "google_cloud_storage" | "google_drive" | "put" | "presigned_url"},
   "callbacks": [{"url": "...", "headers": {}, "ca_cert": ""}]}
  ```

  `sources` 至少 1、預設最多 3（`max_sources_per_request`）。**注意**：`docs/usage.md` 仍示範舊的 `http_sources`／`file_sources` 鍵，但現行模型只有 `sources`，`app.py` 不再處理舊鍵；RAGFlow 的 Go client 把 `file_sources` 當 `/v1alpha` 形狀、`sources[kind=file]` 當 `/v1` 形狀（§3.4）。
- **`/v1/convert/file` request**：multipart；`files`（可重複的 `UploadFile`）、options 以**個別 form 欄位**出現（`FormDepends(ConvertDocumentsOptions)`）、另有 `target_type`（`inbody|zip|presigned_url`）與 `callbacks`。usage.md 的 curl：`-F 'ocr_engine=easyocr' -F 'pdf_backend=dlparse_v2' -F 'from_formats=pdf' -F 'image_export_mode=embedded' -F 'files=@x.pdf;type=application/pdf'`。
- **Response**（`responses.py`）：

  ```json
  {"document": {"filename": "x.pdf", "md_content": "...", "json_content": null, "html_content": null,
                "text_content": null, "doctags_content": null, "doclang_content": null},
   "status": "success", "errors": [], "processing_time": 12.3, "timings": {}}
  ```

  `status` 取 docling `ConversionStatus`：`pending | started | failure | success | partial_success | skipped`。`errors[]` = `{component_type, module_name, error_message, category, page_no?}`。`TaskStatusResponse` = `{task_id, task_type, task_status, task_position?, task_meta?, error_message?, failure?}`，`task_status` 同一個 enum（usage.md 列 `pending|started|success|failure`）。`/v1/result/{task_id}` 回 `ConvertDocumentResponse`（或 zip／presigned／`TaskFailureResult {kind: "TaskFailureResult", failure}`）。
- **Options**（`ConvertDocumentsOptions`）：`to_formats` 預設 `["md"]`（md, json, yaml, html, html_split_page, text, doctags, vtt, doclang, dclx, chunks, latex）；`from_formats` 預設全部；`image_export_mode` 預設 **`placeholder`**（placeholder|embedded|referenced）；`do_ocr=true`、`force_ocr=false`、`ocr_engine="auto"`（棄用 → `ocr_preset`）、`ocr_lang: list[str]|None`（BCP-47）、`pdf_backend` 預設 `THREADED_DOCLING_PARSE`、`table_mode`、`abort_on_error=false`、`do_table_structure=true`、`include_images=true`、`images_scale=2.0`、**`md_page_break_placeholder: str = ""`**（「Add this placeholder between pages in the markdown output」；預設空 = 不插）、`do_picture_description=false`、`document_timeout`、`page_range`、`pipeline=standard`、`do_pdf_heading_hierarchy`、`md_compact_tables`、`chunking_options`。**`return_as_file` 已不存在**（改 `target`／`target_type=zip`）。
- **認證**：`DOCLING_SERVE_API_KEY`（空 = 關）；`auth.py` `APIKeyAuth(APIKeyHeader)`，`header_name = "X-Api-Key"`（HTTP header 名稱不分大小寫）；WebSocket 用 `?api_key=`。
- **設定**：`UVICORN_PORT=5001`、`DOCLING_SERVE_ENG_LOC_NUM_WORKERS=2`、`MAX_SYNC_WAIT=120` 秒、`MAX_FILE_SIZE` 預設無上限、`RESULT_REMOVAL_DELAY=300` 秒、`ENABLE_UI=false`、`ENABLE_API_DOCS=true`、CORS `*`。
- **映像**：`ghcr.io/docling-project/docling-serve`（預設 CUDA）、`-cpu`、`-cu128`、`-cu130`。

### 3.2 Open WebUI（v0.11.4，2026-09-21）

來源：`backend/open_webui/retrieval/loaders/main.py`（`DoclingLoader`）、`config.py`、`loaders/external_document.py`、`loaders/mineru.py`、`src/lib/components/chat/Messages/Citations/CitationModal.svelte`、issue #14011 與其 timeline、docs `features/chat-conversations/rag/document-extraction/docling/`。

- **它實際送的請求**（節錄原始碼）：

  ```python
  page_break_marker = "\f"
  headers["X-Api-Key"] = f"{self.api_key}"            # 只在設定了 DOCLING_API_KEY 時
  r = requests.post(f"{self.url}/v1/convert/file",
      files={"files": (self.file_path, f, self.mime_type or "application/octet-stream")},
      data={"image_export_mode": "placeholder", "md_page_break_placeholder": page_break_marker, **self.params},
      headers=headers, verify=AIOHTTP_CLIENT_SESSION_SSL)
  ```

  → `POST {DOCLING_SERVER_URL}/v1/convert/file`（不是 v1alpha）、multipart 欄位 `files`；form 欄位 `image_export_mode=placeholder`、`md_page_break_placeholder=\f`，再加 `DOCLING_PARAMS`（JSON dict）裡的**每一個鍵**原樣塞進 `data=`（巢狀 dict 要先轉成 JSON 字串，issue #27572）。**沒有設 timeout**（`requests` 預設無限等）。
- **它怎麼讀回應**：`status in ["failure", "skipped"]` → 以 `errors[].error_message` 拋錯（PR #30107，v0.11.4）；否則讀 `document.md_content`；**若內容含 `\f`，就以 `\f` 切成一頁一個 Document，`metadata = {"Content-Type": mime, "page": idx}`（0 起算）**；不含則整份一個 Document。
- **設定鍵**（`config.py`）：`CONTENT_EXTRACTION_ENGINE`（`""`、`external`、`tika`、`datalab_marker`、`docling`、`document_intelligence`、`mineru`、`mistral_ocr`、`paddleocr_vl`）、`DOCLING_SERVER_URL`（預設 `http://docling:5001`）、`DOCLING_API_KEY`、`DOCLING_PARAMS`（`{}`）。舊的 `DOCLING_OCR_ENGINE`／`DOCLING_OCR_LANG`／`DOCLING_DO_PICTURE_DESCRIPTION`／`DOCLING_PICTURE_DESCRIPTION_*` **已不存在**，全部併入 `DOCLING_PARAMS`。純文字檔不經 Docling（`TextLoader`）。文件建議 `DOCLING_SERVE_MAX_SYNC_WAIT=600`。
- **頁碼 issue #14011**「Docling and Tika are not adding page numbers to vector store document metadata」（2025-05-18）：當天被維護者以「Intended behaviour, however PR welcome」關閉。後續：Discussion #20446、PR #20447／#20809／#20811（都未合併）。但**現行 `main` 已依 `\f` 切頁並寫入 `page` metadata**（來自一個 `refac` commit；哪一個 release 開始**（未驗證）**）。預設 PDF 路徑 `PDFLoader(mode="page")` 一向有 `page`。
- **引用 UI**：`CitationModal.svelte` 以 `Number.isInteger(document?.metadata?.page)` 判斷，顯示 `(page {page+1})` 並連到 `#page=${page+1}`。送給 LLM 的 `<source>` 標籤只帶 `RAG_SOURCE_METADATA_KEYS`（預設空）列出的 metadata。
- **結論**：Open WebUI 的頁碼完全靠「把 `md_content` 以它自己送的 placeholder 切開」；我們只要**正確地在每頁之間放 `\f`**（第一頁前面不放、缺頁留空段保位），新版就有頁碼；舊版（整份一個 Document）則靠保留 `<!-- page: N -->` 讓 LLM 自己引用（§4.4 `doc4ai_keep_page_markers`）。
- **External loader**（對照用）：`PUT {EXTERNAL_DOCUMENT_LOADER_URL}/process`，body = 檔案原始 bytes，headers `Content-Type`、`Authorization: Bearer <key>`、`X-Filename`（URL 編碼）；回應 2xx JSON，**單一物件或 list** 的 `{page_content, metadata}`，list 會變成多個 Document（所以可帶頁碼）。URL 與 key 都要設才啟用；無 timeout；對所有檔案類型生效。
- **MinerU loader**（對照用）：`POST {MINERU_API_URL}/file_parse` multipart `files` + `return_md=true`…，timeout 300，讀 `results[<filename>].md_content`，單一 Document、無頁碼。

### 3.3 Kotaemon

`libs/kotaemon/kotaemon/loaders/docling_loader.py` 的 `DoclingReader` **在行程內** `from docling.document_converter import DocumentConverter`，唯一的 HTTP 是選用的 `vlm_endpoint`（圖說）。輸出每頁一個 Document（`page_label`、`file_name`、`file_path`）加表格／圖片 Document。loader 清單（`loaders/__init__.py`）全是特定廠商（Adobe、Azure DI、Mathpix、Unstructured、PaddleOCR-VL、OCRReader…），**沒有通用的 HTTP／docling-serve loader**（在匯出清單之外的可能性**未驗證**）。

### 3.4 RAGFlow

repo 已以 **Go** 為主（1.0.0-rc1）。`ragflow.io/docs/select_pdf_parser` 今日 404；權威來源是 `docs/faq.mdx`（「How do I use an external Docling Serve server」）與 `internal/parser/parser/pdf_parser_docling.go`：

- 環境變數 `DOCLING_SERVER_URL`、`DOCLING_API_KEY`（或每個 parser 的 `docling_server_url`／`docling_api_key`）；**只走 HTTP**；認證 header 是 **`Authorization: Bearer <key>`**（不是 `X-Api-Key`——與 docling-serve 自己的認證不相容，但我們兩種都收）。
- 依序嘗試：`/v1/convert/source`（chunked）→ `/v1alpha/convert/source`（chunked）→ `/v1/convert/source` → `/v1alpha/convert/source`。v1 body：`{"options": {"from_formats": ["pdf"], "to_formats": ["json", "md", "text"]}, "sources": [{"kind": "file", "filename": …, "base64_string": …}]}`；chunked 變體加 `do_chunking: true, chunking_options: {max_tokens: 512, overlap: 50, tokenizer: "sentencepiece"}`。回應解析接受 `document`、`documents[]` 或 `results[]`，讀 `md_content`／`json_content`。
- MinerU parser：`MINERU_APISERVER`（必填）、`MINERU_API_KEY`、`MINERU_BACKEND`、`MINERU_SERVER_URL`，呼叫 `file_parse` 與 `tasks`，輪詢。parser 種類：deepdoc（預設）、naive、mineru、paddleocr、docling、tcadp；**沒有通用的「外部 HTTP parser」**。

### 3.5 Dify

- `ETL_TYPE=dify|Unstructured`、`UNSTRUCTURED_API_URL`、`UNSTRUCTURED_API_KEY`。`extract_processor.py`：`ETL_TYPE=Unstructured` 時只有 `.doc .msg .eml .ppt .pptx .xml .epub`（自動模式再加 `.md`）走 Unstructured；**PDF 永遠用內建 `PdfExtractor`（pypdfium2，metadata `{source, page}`）**。Unstructured extractor 用 SDK 的 `partition_via_api`（POST multipart `files` 到 `{api_url}/general/v0/general`，SDK 行為**未在此驗證**），依 `metadata.page_number` 分頁。
- MinerU 外掛 `langgenius/mineru` v0.5.8（`dify-official-plugins/tools/mineru`）：credentials `base_url`、`token`、`server_type`（official／local）；local 模式 `POST {base_url}/file_parse` multipart（`parse_method`、`return_md`、`backend`、`server_url`），以 `GET /docs` 驗證連線；official 模式用 `api/v4/file-urls/batch` + `extract-results/batch/{id}`。需要 Dify 的 `FILES_URL`。
- Docling 外掛 `s20ss/docling` v0.0.1（2025-06，6 ★）：`requirements.txt` 含 `docling` → **in-process**，沒有 server URL 設定。
- 結論：Dify 的現實路徑是**寫一個 Dify tool plugin**（檔案進、Markdown／JSON 出，仿 MinerU plugin 的 local 模式）；Unstructured 相容 API 只能攔到非 PDF 的 Office 檔。

### 3.6 其他會說 docling-serve 的 client

Haystack `docling-serve-haystack`（`/v1/convert/file` 與 `/v1/convert/source`）、n8n 社群節點 `n8n-nodes-docling-serve`。AnythingLLM／LibreChat／Onyx 未發現 docling-serve 整合（**未驗證不存在**）。

## 4. 端點設計

所有路徑掛在既有 FastAPI app、同一個 port，**不在** `/api` 之下、**不**加 `workspace` envelope（`EnvelopeMiddleware` 排除 `/v1/` 與 `/health`，同 `/mcp` 的作法），在 SPA catch-all 之前註冊。回應一律 `application/json`；錯誤用 FastAPI／docling-serve 風格 `{"detail": "..."}`（§10）。

### 4.1 總表

| 方法 + 路徑 | 認證 | scope | 用途 |
|---|---|---|---|
| `GET /health` | 無 | — | `{"status": "ok"}`；也給 Docker healthcheck 用（Spec 4 §3.1） |
| `GET /v1/capabilities` | 無 | — | `{"from_formats": [...], "to_formats": ["md", "text"], "ocr_langs": ["zh-Hant", "en"], "engines": {...ready...}, "doc4ai": {"version", "max_file_mb", "sync_timeout_s"}}`；**不**洩漏路徑、token、主機資訊 |
| `POST /v1/convert/file` | PAT | `doc4ai:convert` | multipart 同步轉換 |
| `POST /v1/convert/source` | PAT | `doc4ai:convert` | JSON（base64）同步轉換 |
| `POST /v1/convert/file/async`、`POST /v1/convert/source/async` | PAT | `doc4ai:convert` | 回 `TaskStatusResponse` |
| `GET /v1/status/poll/{task_id}?wait=` | PAT | `doc4ai:convert` 或 `doc4ai:read` | 只看得到**同一把 token** 建立的 task（否則 404） |
| `GET /v1/result/{task_id}` | PAT | 同上 | 完成後的 `ConvertDocumentResponse` |
| `GET /v1/doc4ai/documents/{doc_id}/assets/{name}` | PAT | `doc4ai:read` | `image_export_mode=referenced` 時圖片的下載位置（Doc4AI 擴充） |
| `POST /v1alpha/convert/source`（別名） | PAT | `doc4ai:convert` | 接受舊 `file_sources` 形狀（RAGFlow 退回路徑）；回應同 v1 |

不實作：`/v1/convert/source/batch`、`/v1/chunk/*`、WebSocket、`/v1/clear/*`、`/v1/callback/*`、`target.kind != inbody`（→ 422 `unsupported_target`）、`sources[].kind` 非 `file`（→ 422 `unsupported_source`）。`/v1/capabilities` 以 `to_formats` 誠實列出我們支援的格式。

### 4.2 Request 解析

**`/v1/convert/file`**：`files` 可多個（docling-serve 同一回應回多份時用 `documents[]`？——**不**：`ConvertDocumentResponse` 只有單一 `document`；多檔時 docling-serve 走 zip）。我們：**一個請求一個檔案**；第二個以上 → 422 `one_file_per_request`（Open WebUI、Haystack、RAGFlow 都是一檔一請求）。檔名取自 multipart `filename`（經 `uploads.display_name()` 去路徑）；大小上限 `compat.max_file_mb`（預設 200）→ 413；串流寫入 `UploadManager.part_path()`，**不**整檔進記憶體；magic bytes 檢查沿用 `tools_convert.check_magic()`（A-M10）→ 不符 → 200 + `status: "failure"` + `errors[0].error_message = "unsupported_file: …"`（docling-serve 對無法轉的檔也是回 failure，而不是 4xx；Open WebUI 讀 `errors[].error_message`）。

**`/v1/convert/source`**：JSON body 上限 `compat.max_source_mb`（預設 50，base64 膨脹後）；`sources` 恰好 1 筆 `kind: "file"`；`base64_string` 解碼沿用 `tools_convert.decode_content()`（容忍 data: 前綴、空白、URL-safe）。**`/v1alpha` 別名**另接 `file_sources: [{base64_string, filename}]`。

**options**（JSON 的 `options` 物件，或 multipart 的個別欄位；multipart 的 list 欄位如 `to_formats` 可重複出現或以逗號分隔）：

| docling 選項 | Doc4AI 對應 | 說明 |
|---|---|---|
| `to_formats` | `md`（預設）、`text` 支援；`json`／`html`／其他 → 對應欄位 `null`，列入 `doc4ai.unsupported_formats` | 不假造 `DoclingDocument` JSON。RAGFlow 同時要 `json, md, text`，是否會因 `json_content: null` 失敗由計畫 spike 決定（§11） |
| `from_formats` | 忽略（我們依探測路由） | 列入 `doc4ai.ignored_options` |
| `md_page_break_placeholder` | 非空 → 以此字串**接在頁與頁之間**（第一頁前不放），`<!-- page: N -->` 移除；空（預設）→ 保留 `<!-- page: N -->` | §4.4 |
| `image_export_mode` | `placeholder`（預設）→ 圖片改為 `<!-- image -->`；`referenced` → 連結改為 `/v1/doc4ai/documents/{doc_id}/assets/{name}` 的絕對 URL（以請求的 scheme/host 組成）；`embedded` → `data:` URI 內嵌，單檔總量超過 `compat.embed_images_max_mb`（預設 20）時退回 placeholder 並記 `doc4ai.warnings` | docling-serve 預設也是 placeholder |
| `ocr_lang` | 含 `zh-Hant`／`zh-TW`／`zh`／`cht` → `lang=cht`；只有 `en` → `lang=en`；其他／缺 → `general.lang` | 我們只有兩個語言值（index §0） |
| `force_ocr=true` | MinerU 就緒 → `engine="mineru"`（強制、無 fallback）；否則忽略 + warning | 使用者明確要「全頁 OCR」 |
| `do_ocr=false` | 忽略 + warning | 我們的路由自己決定 |
| `page_range=[a,b]` | 忽略 + warning（Phase 1 不做部分轉換） | 列入 §11 |
| `document_timeout` | 轉成 `ConvertOptions.timeout_s`（上限 `compat.sync_timeout_s`） | |
| `abort_on_error`、`pdf_backend`、`ocr_engine`、`table_mode`、`do_table_structure`、`include_images`、`images_scale`、`do_picture_description`、`pipeline`、`chunking_*` 等 | 忽略 | 全部列入 `doc4ai.ignored_options`（讓除錯看得到） |
| `doc4ai_engine`（擴充） | `markitdown|docling|mineru` → `ConvertOptions.engine` | Open WebUI 的 `DOCLING_PARAMS` 會把任何鍵原樣送來，所以擴充鍵對它可用 |
| `doc4ai_lang`（擴充） | `cht|en`，優先於 `ocr_lang` | |
| `doc4ai_force`（擴充） | `true` → `ConvertOptions.force=True`（略過快取） | |
| `doc4ai_keep_page_markers`（擴充） | `true` → 有 placeholder 時**同時**保留 `<!-- page: N -->` | 給舊版 Open WebUI（不切頁）用 |

未知欄位一律忽略並列入 `doc4ai.ignored_options`（不 422；docling-serve 的 `FormDepends` 也會忽略多餘欄位）。`target`／`target_type` 只接受 `inbody`。

### 4.3 同步流程

1. 認證（§5）→ 限流（同一個 `RateLimiter`，計入該 token 的 `tools/call` bucket）→ 解析 options。
2. 檔案落地 → `sha256` → 若同 sha 的文件已存在且非 `force` → **快取命中**：直接組回應（`status: "success"`，`doc4ai.cached: true`），記錄一筆呼叫，不建 job。
3. 建 job：`tools_convert.reserve_job(ctx, opts, token_id)` 的變體 `reserve_job_waiting()`——同步請求遇到「同時進行中的工作數已達 `compat.max_concurrent_jobs_per_token`」時**等待空位**（每 0.5 s 重試，含在逾時內），而不是像 MCP 立刻回錯：docling-serve 的 client 預期的是「排隊」而不是「拒絕」。job `origin = "compat"`。
4. `wait_for_task()` 等到終態或 `compat.sync_timeout_s`（預設 600；docling-serve 預設 `MAX_SYNC_WAIT=120`，Open WebUI 文件建議 600）。
5. 終態 `done|low|skipped` → 200 + `status: "success"`（`low` 也算 success，但 `doc4ai.quality.level = "low"`、`status` 改為 **`partial_success`**；Open WebUI 只拒 `failure|skipped`，所以 `partial_success` 照常入庫）；`failed` → 200 + `status: "failure"` + `errors[0] = {component_type: "doc4ai", module_name: "pipeline", error_message: "<error_kind>: <error_msg>"}`；`cancelled` → 同 failure，訊息 `cancelled`。
6. 逾時 → **504** `{"detail": "conversion still running", "task_id": "<job_id>", "status_url": "/v1/status/poll/<job_id>", "result_url": "/v1/result/<job_id>"}`；工作**繼續**跑，client 之後可用非同步端點取回（docling-serve 逾時直接 504 無 task_id；我們多給 id）。
7. 每一步的呼叫紀錄見 §6。

### 4.4 Markdown 組裝與頁碼

輸入是 `<stem>.md`（含 `<!-- page: N -->`）。以 `pagemap.split_pages()` 得 `(pre, {page: text})`：

- **placeholder 模式**（`md_page_break_placeholder` 非空）：`pre`（第一個標記之前的內容，通常是空或標題）接到第 1 頁前；輸出 = `page_1_text + P + page_2_text + P + … + page_N_text`，N = 文件頁數（`documents.pages`）；**沒有標記的頁放空字串**，讓切開後的索引仍對應真實頁碼；`<!-- page: N -->` 移除（除非 `doc4ai_keep_page_markers`）。Open WebUI 以 `\f` split 後 `page = idx`（0 起算）→ UI 顯示 `idx+1` = 真實頁碼。
- **預設模式**（空 placeholder）：原樣輸出（含 `<!-- page: N -->`）。這與 docling-serve 預設（完全沒有頁界）不同，但 HTML 註解在任何 Markdown 渲染都不可見，而且是 README 既有的輸出契約。
- 無頁碼的文件（docx 等）：placeholder 模式下輸出整份、不插 placeholder；`doc4ai.pages = null`。
- `text_content`（`to_formats` 含 `text`）：以 `quality.strip_markup()` 去掉 Markdown 語法後的純文字；placeholder 規則同上。

`document.doc4ai`：

```json
{"doc_id": "f52860b8…", "engine": "mineru", "tried": ["docling", "mineru"], "lang": "cht",
 "quality": {"level": "warn", "score": 0.93, "reasons": []},
 "pages": 1192, "page_map": {"coverage": 1.0, "method": "mineru_render_plan"},
 "page_warnings": [{"page": 61, "reason": "broken_text_layer", "repaired": true}],
 "untrusted_pages": [],                      // Spec 4 §9 完成後才有值
 "cached": false, "elapsed_s": 412.7,
 "ignored_options": ["pdf_backend", "table_mode"], "unsupported_formats": ["json"], "warnings": []}
```

top-level 另複製 `doc4ai: {doc_id, quality.level, cached}`（RAGFlow 讀 `document`，Open WebUI 讀 `document.md_content`；頂層給 curl／n8n 使用者好找）。

### 4.5 非同步流程

- `…/async` 做 §4.3 的 1–3，立刻回 `202`？——docling-serve 回 **200** + `TaskStatusResponse {task_id, task_type: "convert", task_status: "pending", task_position: n}`；我們照抄（200）。`task_id` = `job_id`。快取命中 → `task_status: "success"` 直接可 `result`。
- `status/poll/{task_id}?wait=W`：`W` 上限 60 秒（docling-serve 無上限；我們避免長掛連線）；狀態對映：`queued → pending`、`probing|converting|checking → started`、`done|low|skipped → success`、`failed|cancelled → failure`（`error_message` 帶 `<error_kind>: <error_msg>`）；`task_position` = 佇列中排在它前面的 task 數（`Store.count_queued_tasks()` 之前的序）；`task_meta = {"pages_done", "pages_total"}`（`serialize_task().progress`）。
- `result/{task_id}`：未完成 → 404 `{"detail": "task not finished"}`（docling-serve 同）；完成 → `ConvertDocumentResponse`（options 從 `compat_tasks.options_json` 取回）；失敗 → `TaskFailureResult {kind: "TaskFailureResult", failure: {...}}`。
- 可見性：只有建立該 task 的 token 看得到；其他 token → 404。結果不設 5 分鐘刪除（docling-serve `RESULT_REMOVAL_DELAY=300`）：文件本來就長存；`compat_tasks` 列 30 天後由 maintenance 清掉（之後 `result` 回 404）。

## 5. 認證

- 取 token：先 `X-Api-Key`（docling-serve／Open WebUI），再 `Authorization: Bearer`（RAGFlow、curl）；兩者都有且不同 → 401。**不**接受 `?api_key=`（docling-serve 只在 WebSocket 用它；我們沒有 WebSocket；URL 帶 token 違反 MCP 設計 §4.2 的原則）→ 400 `{"detail": "send the token in X-Api-Key or Authorization"}`，記 `auth_error/token_in_query`。
- 驗證：`PatVerifier.verify()` 原樣（格式、CRC、HMAC 查表、撤銷、到期）；`AuthFailureGuard` 原樣（每 IP 10 次/分鐘後 429）。失敗 → `401 {"detail": "<FAILURE_TEXT>"}` + `WWW-Authenticate: Bearer realm="doc4ai", error="invalid_token"`（與 `/mcp` 同一個 `gate.www_authenticate()`，無 `resource_metadata`）。docling-serve 自己對缺 key 回 FastAPI `APIKeyHeader` 的 403 `{"detail": "Not authenticated"}`；Open WebUI 兩者都當例外處理，差異無害，401 語意較正確。
- scope：convert 端點需 `doc4ai:convert`；poll／result 需 `doc4ai:convert` 或 `doc4ai:read`；assets 需 `doc4ai:read`。不足 → `403 {"detail": "token lacks doc4ai:convert"}`，記 `forbidden_scope`。
- **隔離不變**：PAT 對 `/api/*` 仍 401（既有測試 `test_pat_rejected_on_api` 擴成也涵蓋 server token 對 `/v1/*` 無效——server token 不是 PAT，格式檢查就擋掉）。
- Origin：請求帶 `Origin` 且與 `Host` 不同 → 403（同 `McpGate.same_origin`）；非瀏覽器 client 不帶 Origin，不受影響。不開 CORS。
- Host：綁非 loopback 時由 SDK 之外的 `allowed_hosts` 規則？——`/v1` 不經 MCP SDK，沿用 `auth.host_ok()` 的 DNS-rebinding 守則**只在無 server token 時**（P4 規則：非 `/api` 路由也檢查 Host）。

## 6. 呼叫紀錄與後台

- 每個相容請求一筆 `mcp_calls`：`method = "compat/docling"`，`tool_name ∈ {convert_file, convert_source, convert_file_async, convert_source_async, status_poll, result, asset, capabilities}`，`protocol_version = "docling-serve-v1"`（v1alpha 別名記 `docling-serve-v1alpha`），`status` 同 MCP（`ok | tool_error | auth_error | forbidden_scope | rate_limited | protocol_error`），`error_code`（`conversion_failed`、`unsupported_file`、`sync_timeout`、`file_too_large`…），`http_status`、`duration_ms`、`response_bytes`、`response_tokens_est`（md_content 的估計）、`job_id`、`args_summary`（`{filename, size, sha256[:8], to_formats, placeholder: bool, lang, engine}`——**不含內容**）。
- `mcp_clients`：`client_name` = User-Agent 的第一個 product token（`python-requests`、`Go-http-client`、`curl`…），`client_version` = 其版本，`protocol_version = "docling-serve-v1"`；活躍／閒置規則不變。
- `api_tokens.last_used_*`／`last_client` 由同一個 `CallRecorder.touch_token()` 更新。
- `mcp_jobs` 記 `(job_id, token_id)`（既有，`reserve_job` 寫入）；新表 `compat_tasks(job_id PK, token_id, options_json, created_at)` 存 options 供 `result` 重組（schema **v5**，只新增）。
- Jobs：`jobs.origin = "compat"`；Web Jobs 頁的來源徽章多一個「相容 API」；`JobIn.origin`（`POST /api/jobs`）**不**開放 `compat`。
- 後台（MCP 頁）：
  - 總覽：狀態卡多一行「docling-serve 相容 API：`http://<host>:<port>/v1`（Open WebUI 填 `http://<host>:<port>`）」+ 24 小時相容呼叫數；安全提示沿用（HTTP 明文）。
  - 呼叫紀錄：method 篩選器多 `compat/docling`；列展開顯示 `args_summary`。
  - Tokens：建立 token 對話框的 scope 說明加一句「`doc4ai:convert` 也允許 docling-serve 相容 API」；一次性視窗的片段多一個分頁「Open WebUI」（§8.1 的環境變數，token 已填）。
- SSE：沿用 `mcp.call`／`mcp.client`／`mcp.token`；無新事件。

## 7. 設定（`aidoc.toml` 新增 `[compat]`）

```toml
[compat]
enabled = true
sync_timeout_s = 600               # 同步端點最長等待；超過回 504 並附 task_id
max_file_mb = 200                  # /v1/convert/file 單檔上限（串流落地）
max_source_mb = 50                 # /v1/convert/source 的 JSON body 上限
embed_images_max_mb = 20           # image_export_mode=embedded 的內嵌總量上限
max_concurrent_jobs_per_token = 8  # 同步請求超過時等待，非同步請求回 429
poll_max_wait_s = 60
task_retention_days = 30           # compat_tasks 保留；之後 /v1/result 回 404
```

`PUT /api/settings` 白名單加入全部鍵（ints ≥ 1；`sync_timeout_s` 10–3600；`poll_max_wait_s` 1–300；`enabled` bool）。`mcp.enabled = false` **不**影響 `/v1`（兩個入口各有開關）；`compat.enabled = false` → `/v1/*` 回 404 `{"detail": "compat API disabled"}`，`/health` 仍回 ok。

## 8. 整合食譜（文件放 `docs/integrations/<platform>.md`，README 連過去）

可行性分級：**A** 改設定即可用（本規格驗收）；**B** 可用但有限制或需要額外元件；**C** 需要另寫外掛（另案）；**？** 未驗證。

### 8.1 Open WebUI — A

```env
CONTENT_EXTRACTION_ENGINE=docling
DOCLING_SERVER_URL=http://<doc4ai-host>:8765        # 不要加 /v1；Open WebUI 自己接 /v1/convert/file
DOCLING_API_KEY=doc4ai_pat_…                        # 需要 doc4ai:convert 的 PAT（MCP → Tokens 建立）
DOCLING_PARAMS={"doc4ai_lang": "cht"}               # 選用：{"doc4ai_engine": "mineru"}、{"doc4ai_keep_page_markers": true}
```

- 也可在 Admin Panel → Settings → Documents 的 UI 設定同樣的值。
- 頁碼：v0.11.x 的 `DoclingLoader` 送 `md_page_break_placeholder=\f` 並依 `\f` 切頁、寫 `metadata.page`，引用視窗顯示 `(page N)` 並連到 PDF 的該頁——Doc4AI 每頁之間放 `\f`，缺頁留空段保位，所以頁碼對得上真實頁。較舊版本（不切頁）：設 `DOCLING_PARAMS={"doc4ai_keep_page_markers": true}` 讓 `<!-- page: N -->` 留在文字裡，LLM 仍能引用頁碼。
- 逾時：Open WebUI 不設 timeout；Doc4AI 最多等 `compat.sync_timeout_s`（600）後回 504，Open WebUI 會把該檔標為失敗；大檔（> 300 頁掃描）請先在 Doc4AI 轉好（Library），Open WebUI 再上傳同一檔會**快取命中**秒回。
- 失敗語意：`status: "failure"` + `errors[0].error_message`（例 `input: encrypted PDF`）會原樣出現在 Open WebUI 的錯誤提示。
- HTTPS／自簽：Open WebUI 的 `verify=AIOHTTP_CLIENT_SESSION_SSL`；自簽憑證要設 `AIOHTTP_CLIENT_SESSION_SSL=false`（其文件）。
- **未驗證**：Open WebUI 批次上傳多檔時的並行度（決定 `compat.max_concurrent_jobs_per_token` 是否要再放大）；哪個 release 開始切頁。

### 8.2 RAGFlow — B

```env
DOCLING_SERVER_URL=http://<doc4ai-host>:8765
DOCLING_API_KEY=doc4ai_pat_…           # RAGFlow 用 Authorization: Bearer 送，Doc4AI 兩種 header 都收
```

然後在知識庫的 PDF parser 選 **Docling**（`docs/faq.mdx`「How do I use an external Docling Serve server」）。限制：

- RAGFlow 先打 `/v1/convert/source`（chunked：`do_chunking: true, chunking_options{…}`），我們不做 docling 切段 → **忽略** `do_chunking`（列入 `ignored_options`）並回 `document.md_content`；RAGFlow 的回應解析接受 `document`，之後用自己的切段模板切 Markdown——**未驗證**它在拿到沒有 chunks 的回應後是否退回 standard 路徑或直接用 md。
- 它要 `to_formats: ["json", "md", "text"]`，我們 `json_content: null`——**未驗證** RAGFlow 是否只在 `json_content` 存在時才用它。計畫的 spike 以 RAGFlow 1.0 的 Go client 原始碼（`pdf_parser_docling.go`）確認後，決定是否需要回一個最小的 `json_content`。
- 頁碼：RAGFlow 的 docling parser 從 `md_content` 讀不到頁碼（Go 端不處理 placeholder）；`<!-- page: N -->` 保留在文字中（RAGFlow 不送 placeholder）供 LLM 引用。

### 8.3 Kotaemon — C

`DoclingReader` 在行程內跑 docling，沒有 server URL；要接 Doc4AI 得寫一個 `BaseReader` 子類（`kotaemon/loaders/doc4ai_loader.py`，~60 行：`requests.post(f"{url}/v1/convert/file", files=…, headers={"X-Api-Key": key})`，以 `\f` 切頁回傳 `Document(text, metadata={"page_label": i+1, "file_name": …})`）並在 `flowsettings.py` 的 loader 對照表登記。**另案**：本規格只提供這段參考實作與測試（§9 的「Kotaemon 形狀」就是它送出的請求），不送 PR 到 Kotaemon。

### 8.4 Dify — C

- PDF 永遠走 Dify 內建 `PdfExtractor`；`ETL_TYPE=Unstructured` 只影響 Office／eml／epub。因此「Unstructured 相容 API」對 PDF 無效，不做。
- 可行路徑：**Dify tool plugin**（仿 `langgenius/mineru` local 模式）：credentials `base_url`、`api_key`；tool `convert_document(file)` → `POST {base_url}/v1/convert/file` → 回 Markdown（含 `<!-- page: N -->`）與 `doc4ai` metadata；驗證連線用 `GET /health`。放進 Knowledge Pipeline 的解析節點。這是一個獨立的 Python 套件（`dify_plugin` SDK），**另案**規格；本文件只保證 `/v1/convert/file`、`/health` 是它需要的兩個端點。

### 8.5 其他

- **Haystack** `DoclingServeConverter`（`/v1/convert/file`、`/v1/convert/source`）：預期直接可用（**未驗證**）；列入 §9 的 request 形狀。
- **n8n** `n8n-nodes-docling-serve`：預期可用（**未驗證**）。
- **curl**：

  ```bash
  curl -s -H "X-Api-Key: $PAT" -F "files=@report.pdf" -F "md_page_break_placeholder=<!-- page -->" \
       http://127.0.0.1:8765/v1/convert/file | jq -r .document.md_content
  ```

## 9. 測試

### 9.1 相容測試（重放真實 request 形狀）

`tests/compat/shapes/` 放每個 client **逐字**的 request 形狀（從其原始碼抄下，附來源 URL 與版本），測試以 `httpx` 對 in-process app（`TestClient`）或 live server 重放：

| 形狀檔 | 來源 | 斷言 |
|---|---|---|
| `openwebui_0_11_4.json` | `DoclingLoader`：`POST /v1/convert/file`，multipart `files=(name, bytes, mime)`，form `image_export_mode=placeholder`、`md_page_break_placeholder=\f`、`X-Api-Key` | 200；`status == "success"`；`md_content.count("\f") == pages - 1`；以 `\f` split 後第 k 段含第 k+1 頁的字；不含 `<!-- page:`；多餘 form 欄位（`DOCLING_PARAMS` 傳來的 `doc4ai_lang`）生效 |
| `openwebui_old_single_doc.json` | 同上但模擬舊版（不切頁）+ `doc4ai_keep_page_markers=true` | `md_content` 同時含 `\f` 與 `<!-- page: N -->` |
| `ragflow_1_0_v1_chunked.json` | `pdf_parser_docling.go` 第一次嘗試：`POST /v1/convert/source`，`Authorization: Bearer`，`options.to_formats=[json,md,text]`、`do_chunking`、`chunking_options` | 200；`document.md_content` 有內容；`text_content` 有內容；`json_content is None`；`doc4ai.unsupported_formats == ["json"]`；`ignored_options` 含 `do_chunking` |
| `ragflow_1_0_v1alpha.json` | 第二次嘗試：`/v1alpha/convert/source` + `file_sources` | 200；同上 |
| `haystack_docling_serve.json` | `DoclingServeConverter` 的 `/v1/convert/file` 與 `/v1/convert/source` | 200 |
| `kotaemon_doc4ai_loader.json` | §8.3 的參考 loader | 200；`\f` 切頁數正確 |
| `curl_async.json` | `…/file/async` → `status/poll?wait=5` → `result` | `task_status` 依序 `pending|started` → `success`；`result` 形狀同同步 |
| `docling_serve_openapi_1_36_0.json` | docling-serve `/openapi.json` 快照（檔案進 repo，附版本） | 我們每個回應都通過快照裡 `ConvertDocumentResponse`／`TaskStatusResponse` 的 schema（`jsonschema` 驗證；多出的 `doc4ai` 鍵允許，因為 docling 模型 `extra` 預設 ignore——**spike 確認**）；`status` 值在 enum 內 |

形狀檔裡的檔案內容用 `tests/fixtures/` 的小 PDF／docx（fake engine 產生 3 頁、有表格、有圖）。

### 9.2 單元

- options 轉譯表（§4.2 每一列一個案例；未知欄位進 `ignored_options`；`ocr_lang` 的 BCP-47 對映；`force_ocr` 在 MinerU 未就緒時的 warning）。
- Markdown 組裝：placeholder 模式的頁數、缺頁空段、第一頁前無 placeholder、`pre` 歸第 1 頁；`image_export_mode` 三種；`text_content` 去標記；無頁碼文件。
- 認證：`X-Api-Key` vs `Bearer` 優先序、兩者不同 → 401、`?api_key=` → 400、撤銷／到期 → 401 + `WWW-Authenticate`、scope 不足 → 403、auth-flood 429、PAT 對 `/api/*` 401、server token 對 `/v1/*` 401。
- 狀態對映（task status → docling enum）；`low` → `partial_success`；`failed` → `failure` + `errors[0]`。
- 快取命中不建 job、記錄一筆 `ok`。
- 大小上限：multipart 串流超過 `max_file_mb` → 413 且 part 檔被刪；`source` body 超過 → 413；magic 不符 → `failure`。
- 同步等待空位：cap=1 時兩個並行同步請求都成功（第二個等待），非同步第二個 → 429 + `Retry-After`。
- 逾時 → 504 + `task_id`，之後 `result` 可取；job 不被取消。
- 可見性：另一把 token 的 `task_id` → 404。
- 呼叫紀錄：每個端點一筆、`method/tool_name/protocol_version/job_id/args_summary` 正確、`args_summary` 不含內容與 token；`mcp_clients` 的 UA 解析。
- Envelope／壓縮：`/v1/*` 回應**沒有** `workspace` 鍵；`/health` 無認證可達；`compat.enabled=false` → 404。

### 9.3 慢測試（真實引擎，`-m slow`）

- 繁中掃描 PDF（`tests/manual.py` 樣本）經 `/v1/convert/file`：`doc4ai.engine == "mineru"`、`\f` 數 = 頁數 − 1。
- Head First Android（壞字型）：`doc4ai.page_warnings` 含 `broken_text_layer` 且 `repaired: true`；`status == "success"`。

### 9.4 真實 client 驗收（手動清單，結果記在計畫 deviations）

1. Open WebUI（最新 release，Docker）接 Doc4AI：上傳掃描 PDF、壞字型 PDF、docx；問答引用顯示 `(page N)`；Doc4AI Jobs 顯示 `origin: compat`。
2. RAGFlow 1.0（Docker）選 Docling parser 接 Doc4AI：建知識庫、解析成功；記錄它實際打的端點順序與回應處理（填回 §8.2 的未驗證項）。
3. `curl` 非同步流程。

## 10. 錯誤與邊界情況

| 情況 | 回應 |
|---|---|
| 缺 token／格式錯／撤銷／到期 | `401 {"detail": "<文字>"}` + `WWW-Authenticate: Bearer realm="doc4ai", error="invalid_token"`；記 `auth_error` |
| token 在 URL | `400 {"detail": "send the token in X-Api-Key or Authorization"}` |
| scope 不足 | `403 {"detail": "token lacks doc4ai:convert"}`；記 `forbidden_scope` |
| 限流（token bucket）／並行上限（非同步） | `429 {"detail": "rate limited", "retry_after": n}` + `Retry-After` |
| auth flood | `429 too_many_auth_failures`（同 `/mcp`） |
| 多個檔案 | `422 {"detail": "one file per request"}` |
| 無 `files`／`sources` 空／base64 壞 | `422 {"detail": "..."}`（FastAPI 驗證錯誤的 `detail` 形狀照舊） |
| `sources[].kind != file`、`target != inbody` | `422 {"detail": "unsupported_source: http"}` |
| 檔案過大 | `413 {"detail": "file exceeds 200 MB"}` |
| 副檔名／magic 不符、加密、損毀、音訊停用 | `200`，`status: "failure"`，`errors[0].error_message = "input: …"` |
| 引擎全部失敗 | `200`，`status: "failure"`，`errors[0].error_message = "engine: …"`，`doc4ai.tried` |
| 品質 `low` | `200`，`status: "partial_success"`，`doc4ai.quality.level = "low"` |
| 同步逾時 | `504 {"detail": "conversion still running", "task_id", "status_url", "result_url"}` |
| 未完成就要 `result` | `404 {"detail": "task not finished"}` |
| 不存在／別人的 task | `404 {"detail": "task not found"}` |
| 轉換中 server 重啟 | job 續跑（既有 recovery）；同步 client 得到連線中斷，重送 → `already_converting`？——**不**：重送同檔命中 `busy_task_id` → 以**等待該 task** 取代建新 job（同步）／回該 task 的 `task_id`（非同步） |
| `compat.enabled = false` | `404 {"detail": "compat API disabled"}` |
| 瀏覽器跨來源 POST | `403 {"detail": "cross-origin request"}` |

## 11. 待決與未驗證

| # | 項目 | 處理 |
|---|---|---|
| U1 | RAGFlow 對 `json_content: null` 與無 chunks 回應的行為 | 計畫 spike S1：讀 `pdf_parser_docling.go` 的回應處理；若需要，回最小 `json_content = {"schema_name": "doc4ai", "pages": [...]}`，並在 §4.2 修訂 |
| U2 | docling `ConvertDocumentResponse` 對多餘鍵的容忍（`extra`） | spike S2：以 docling 套件的 pydantic 模型 `model_validate` 我們的回應 |
| U3 | Open WebUI 哪個 release 開始依 `\f` 切頁；批次上傳並行度 | 驗收時記錄 |
| U4 | `page_range` 部分轉換 | 不做；若使用者需要再加（需 pipeline 支援頁範圍） |
| U5 | `sources[].kind = "http"` | 另案（SSRF 防護：阻擋私網／loopback／link-local、重導向重檢、大小與時間上限） |
| U6 | 是否也提供 Open WebUI「External」loader 協定（`PUT /process`） | 不做：Docling 路徑已覆蓋 Open WebUI；External 只多一個入口 |
| U7 | Dify tool plugin、Kotaemon loader PR | 另案 |
| U8 | 公開 HTTPS 下的 600 s 同步等待（Funnel／反向代理的 idle timeout） | 由 Spec 4 §4 的部署文件記錄實測值；超過代理上限時建議 client 用非同步端點 |

### 需要使用者決定

- **是否開放 `/v1` 給 tailnet 以外的網路**：本規格沿用 `/mcp` 的規則（HTTP 明文時後台顯示提示）；公開曝露走 Spec 4。
- **`compat.max_concurrent_jobs_per_token` 預設 8** 是否合適（GPU 一次只跑一個 task，其餘排隊；數字只影響「等多久才 429」）。

