# Open WebUI → Doc4AI Studio（docling-serve 相容 API）

**可行性：A** — 改設定即可用。Open WebUI 的「Docling」文件抽取引擎把每份上傳檔送到 docling-serve 的
`/v1/convert/file`；Doc4AI Studio 在同一個路徑說同一種話，背後跑的是 Doc4AI 的路由、逐頁品質檢查與修復。

依據：Open WebUI v0.11.4（2026-09-21）`backend/open_webui/retrieval/loaders/main.py` 的 `DoclingLoader`
（查證日 2026-10-05／2026-10-10；規格 `docs/superpowers/specs/2026-10-05-docling-compat-api-design.md` §3.2、§8.1）。

## 1. 在 Doc4AI 建一把 token

Web 介面 → **MCP** → **Tokens** → 建立 token，勾 `doc4ai:convert`（`doc4ai:read` 一定有）。token 只顯示一次；
一次性視窗裡的「Open WebUI（環境變數）」分頁已經把 token 填好。

## 2. 設定 Open WebUI

環境變數（docker run `-e …` 或 compose `environment:`）：

```bash
CONTENT_EXTRACTION_ENGINE=docling
DOCLING_SERVER_URL=http://<doc4ai-host>:<port>      # 不要加 /v1；Open WebUI 自己接 /v1/convert/file
DOCLING_API_KEY=doc4ai_pat_…                        # 第 1 步的 token（以 X-Api-Key 送出）
DOCLING_PARAMS={"doc4ai_lang": "cht"}               # 選用，見下表
```

也可以在 **Admin Panel → Settings → Documents** 把「Content Extraction Engine」設為 Docling，填同樣的 URL 與
API key。這些設定會存在 Open WebUI 的資料庫裡，之後以 UI 為準。

Docker 裡的 Open WebUI 連本機的 Doc4AI：URL 用 `http://host.docker.internal:<port>`（Docker Desktop），並以
`aidoc serve --host 0.0.0.0` 啟動 Doc4AI。

`DOCLING_PARAMS` 的每個鍵都會原樣成為 form 欄位，所以 Doc4AI 的擴充選項可以從這裡給：

| 鍵 | 值 | 作用 |
|---|---|---|
| `doc4ai_lang` | `cht` / `en` | OCR 語言（優先於 `ocr_lang`） |
| `doc4ai_engine` | `markitdown` / `docling` / `mineru` | 強制只用這個引擎（不 fallback） |
| `doc4ai_force` | `true` | 不用快取，重新轉換 |
| `doc4ai_keep_page_markers` | `true` | 切頁之外也保留 `<!-- page: N -->`（給不切頁的舊版 Open WebUI） |

## 3. 頁碼

Open WebUI 送 `md_page_break_placeholder=\f`，回來的 `md_content` 以 `\f` 切成一頁一段，第 k 段（0 起算）的
`metadata.page = k`，引用視窗顯示 `(page k+1)`、連到 PDF 的那一頁。Doc4AI 在**每兩頁之間**放一個 `\f`
（第一頁前不放），沒有內容的頁留空段，所以段落序號永遠等於真實頁碼。Open WebUI 會丟掉空段，但編號在丟之前就
決定了，不會錯位。

不切頁的舊版 Open WebUI（整份一個 Document）：設 `DOCLING_PARAMS={"doc4ai_keep_page_markers": true}`，
`<!-- page: N -->` 會留在文字裡，LLM 仍能引用頁碼。

## 4. 大檔、逾時與快取

- Open WebUI 對 Docling 的請求**沒有 timeout**；Doc4AI 最多等 `compat.sync_timeout_s`（預設 600 秒）後回
  `504` 並附 `task_id`，工作在 Doc4AI 繼續跑完。Open WebUI 會把這個檔標成失敗。
- 大檔（數百頁的掃描 PDF）請先在 Doc4AI 轉好（Web 介面上傳或 `aidoc convert`），之後在 Open WebUI 上傳**同一個
  檔案**會**快取命中**秒回——快取看的是內容 sha256，不看檔名（Open WebUI 存檔時會在檔名前加 UUID，Doc4AI
  會去掉它）。
- 同一把 token 同時進行中的轉換上限 `compat.max_concurrent_jobs_per_token`（預設 8）；同步請求超過時會**排隊等**
  空位，不會被拒。Open WebUI 一次上傳多個檔時每個檔是一個請求（spike S5：只受它自己 40 條 worker thread 限制）。

## 5. 失敗與品質

- 轉不了的檔（不支援的類型、加密、損毀、引擎全失敗）回 HTTP 200 + `status: "failure"`，`errors[0].error_message`
  例如 `unsupported_file: …`、`engine: …`；Open WebUI 會把這段文字顯示在錯誤提示裡。
- 品質評為 `low` 的文件回 `status: "partial_success"`（內容照常入庫），原因在 `errors[0]`
  （`module_name: "doc4ai.quality"`）。
- 回應的 `document.doc4ai` 有 `doc_id`、使用的引擎、品質分數、有問題的頁（`page_warnings`）；Open WebUI 不讀它，
  但用 curl 除錯時很有用。

## 6. 在 Doc4AI 看得到什麼

- **Jobs**：來源徽章「相容 API」。
- **MCP → 呼叫紀錄**：`compat/docling`、tool `convert_file`、client `python-requests`（篩選來源「相容 API」）。
- **MCP → 連線**：Open WebUI 以 `python-requests/<版本>` 出現，協定欄 `docling-serve-v1`。
- token 撤銷後 Open WebUI 的下一個請求得到 `401`。

## 7. HTTPS

Open WebUI 以 `verify=AIOHTTP_CLIENT_SESSION_SSL` 送請求；Doc4AI 走自簽憑證時要在 Open WebUI 設
`AIOHTTP_CLIENT_SESSION_SSL=false`。Doc4AI 目前是明文 HTTP（MCP 頁的安全提示），請只在 tailnet / 區網內使用。

## 未驗證

- 哪一個 Open WebUI release 開始依 `\f` 切頁並寫入 `metadata.page`（`main` 已經如此；v0.11.4 原始碼如此）。
- 實際的 Open WebUI 容器對 Doc4AI 跑一輪上傳 + 問答（本 repo 的自動測試重放的是它送出的請求形狀：
  `tests/compat/shapes/openwebui_0_11_4.json`）。驗收結果記在
  `docs/superpowers/plans/2026-10-05-docling-compat-api.md` 最後一節。
