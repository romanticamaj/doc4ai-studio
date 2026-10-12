# Doc4AI Studio MCP Server — 設計文件

- 日期：2026-10-01
- 狀態：待審核
- 依據：`docs/superpowers/research/2026-10-01-mcp-best-practices.md`（MCP 規格 2026-07-28、官方 Python SDK `mcp` 2.2）
- 相關：主設計 `2026-09-24-ai-friendly-doc-design.md`、逐頁品質 `2026-10-01-per-page-quality-and-page-map.md`、契約 `plans/2026-09-24-index.md`

## 1. 目標與範圍

讓其他 AI（Claude Code、Cursor、VS Code、Claude Desktop…）透過 MCP 使用 Doc4AI Studio：搜尋 Library、分頁讀取文件、取得切段、送檔轉換、查進度。管理者在網頁後台產生／撤銷 token，並看到連線與呼叫狀況。

### 分期

| Phase | 內容 |
|---|---|
| **1（本文件實作範圍）** | 本機／tailnet／內網 client；Bearer PAT；`/mcp` Streamable HTTP；read + convert + convert:local + manage（除 `delete_document`）；後台（token、連線、呼叫紀錄、設定範例） |
| 1.5 | `delete_document`、prompts（`summarize_document`、`ask_document`） |
| 2 | 雲端 client（claude.ai connector、ChatGPT）：公開 HTTPS、OAuth 2.1（外部 IdP 優先）、JWT 驗證分支 |

### 成功標準

- Claude Code 以 `claude mcp add --transport http … --header "Authorization: Bearer …"` 接上後，能完成「搜尋 → 看目錄 → 讀指定頁」，對 1192 頁的書單次回應 ≤ 8,000 tokens。
- 2026-07-28 與 2025-11-25 兩代 client 都能用。
- 撤銷 token 後，下一個請求即被拒（401），後台 5 秒內看到該失敗紀錄。
- PAT 永遠無法呼叫 `/api/*` 管理端點；權限不足的 tool 不出現在 `tools/list`。
- 每一次 MCP 請求（含驗證失敗、被限流）都有一筆紀錄；不記錄 token 原文與完整參數。

## 2. 核心概念

### 2.1 Principal（身分抽象）

所有驗證方式最後都產生同一個物件，tools、權限、紀錄只看它：

```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["pat", "oauth", "admin"]
    subject: str            # pat: token 擁有者（Phase 1 固定 "owner"）；oauth: sub
    token_id: str | None    # pat: api_tokens.id
    client_id: str | None   # oauth: client_id（Phase 2）
    scopes: frozenset[str]
    expires_at: float | None
```

- Phase 1：`PatVerifier` 產生 `kind="pat"`。
- Phase 2：同一個驗證器加 JWT/JWKS 分支產生 `kind="oauth"`（驗 `aud` = 本伺服器 canonical `/mcp` URL，RFC 8707），呼叫端程式不變。
- **隔離原則**：PAT／OAuth token 只對 `/mcp` 有效；`/api/*` 仍只接受既有的 server token（`--token`）或本機 loopback。AI 拿到 PAT 也無法產生新 token、改設定。

### 2.2 權限範圍（scopes）

| scope | 允許 | 風險 | 建立 token 時預設 |
|---|---|---|---|
| `doc4ai:read` | 搜尋、列出、讀取、切段、查工作 | 低 | 勾選 |
| `doc4ai:convert` | 以檔案內容（base64）送轉換 | 中 | 不勾 |
| `doc4ai:convert:local` | 以主機路徑送轉換（限白名單根目錄） | 高 | 不勾，UI 顯示紅色警示 |
| `doc4ai:manage` | 取消工作、重新轉換（Phase 1.5：刪除） | 高 | 不勾 |

- 每把 token 至少要有 `doc4ai:read`。
- scope 檢查在 tool 層以 decorator 強制；`tools/list` 依 scope 過濾（規格允許）。
- 硬呼叫沒有權限的 tool → `isError: true`，`code = "forbidden_scope"`，並記錄。

### 2.3 Token 格式與保存

- 格式：`doc4ai_pat_` + 43 字元 base62（256 bits 隨機）+ 6 字元 base62 CRC32 → 例 `doc4ai_pat_3kX…9Qa_4fZ1bC`。
- 前綴讓外洩時能被掃描工具辨識；CRC 讓伺服器在查資料庫前先擋掉格式錯誤的字串。
- 只在建立時顯示一次；資料庫存 `HMAC-SHA256(server_secret, token)`，`server_secret` 為 `data/secret.key`（32 bytes，首次啟動產生，權限盡量只限本人）。
- 顯示用前綴：`doc4ai_pat_` + 前 4 字元（例 `doc4ai_pat_3kX9…`）。
- 必有到期日：預設 90 天、上限 365 天（設定可調）；可選「永不過期」須在設定中明確開啟 `allow_no_expiry`（預設關）。
- 輪替：`rotate` 產生新 token、舊 token 立即失效（可選 24 小時寬限，預設 0）。

## 3. 資料表（SQLite schema v4）

只新增，不修改既有資料表。`schema_version` 由目前版本 +1。

```sql
CREATE TABLE api_tokens (
  id               TEXT PRIMARY KEY,          -- uuid4 hex
  workspace        TEXT NOT NULL DEFAULT 'default',
  name             TEXT NOT NULL,             -- 使用者命名，例 "Claude Code @ laptop"
  prefix           TEXT NOT NULL,             -- 顯示用，例 doc4ai_pat_3kX9
  token_hash       TEXT NOT NULL UNIQUE,      -- HMAC-SHA256 hex
  scopes_json      TEXT NOT NULL,             -- ["doc4ai:read", ...]
  note             TEXT,
  created_at       REAL NOT NULL,
  expires_at       REAL,                      -- NULL 只在 allow_no_expiry 時允許
  revoked_at       REAL,
  revoked_reason   TEXT,
  rotated_from     TEXT,                      -- 前一把 token id
  rate_limit_per_min INTEGER,                 -- NULL = 用全域設定
  last_used_at     REAL,
  last_used_ip     TEXT,
  last_client      TEXT                       -- "claude-code/2.3.1"
);

CREATE TABLE mcp_clients (                    -- 觀測到的 client（顯示用，非安全邊界）
  id               TEXT PRIMARY KEY,
  token_id         TEXT,                      -- Phase 2 可為 NULL（OAuth 用 oauth_client_id）
  oauth_client_id  TEXT,
  client_name      TEXT NOT NULL,             -- clientInfo.name（自報）
  client_version   TEXT,
  protocol_version TEXT,                      -- 2026-07-28 / 2025-11-25 ...
  user_agent       TEXT,
  first_seen       REAL NOT NULL,
  last_seen        REAL NOT NULL,
  last_ip          TEXT,
  request_count    INTEGER NOT NULL DEFAULT 0,
  UNIQUE (token_id, client_name, client_version)
);

CREATE TABLE mcp_calls (                      -- 每一個 MCP HTTP 請求一筆（含驗證失敗、限流）
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts               REAL NOT NULL,
  token_id         TEXT,                      -- 驗證失敗時 NULL
  token_prefix_seen TEXT,                     -- 失敗時記錄看到的前綴（不記全文）
  client_id        TEXT,                      -- mcp_clients.id
  method           TEXT,                      -- tools/call, tools/list, resources/read, server/discover ...
  tool_name        TEXT,                      -- tools/call 時
  resource_uri     TEXT,                      -- resources/read 時（截斷 200 字）
  args_summary     TEXT,                      -- 去敏、截斷 500 字的 JSON（base64 內容只記長度與 sha256 前 8 碼）
  status           TEXT NOT NULL,             -- ok | tool_error | protocol_error | auth_error | forbidden_scope | rate_limited
  error_code       TEXT,
  http_status      INTEGER,
  duration_ms      INTEGER,
  response_bytes   INTEGER,
  response_tokens_est INTEGER,
  ip               TEXT,
  protocol_version TEXT,
  job_id           TEXT                       -- convert 類 tool 產生的工作
);
CREATE INDEX mcp_calls_ts ON mcp_calls(ts);
CREATE INDEX mcp_calls_token_ts ON mcp_calls(token_id, ts);

CREATE VIRTUAL TABLE page_index USING fts5(   -- search_library 用；逐頁全文索引
  doc_id UNINDEXED, page UNINDEXED, text,
  tokenize = 'trigram'                        -- 中英文混排都能做子字串搜尋
);

CREATE TABLE oauth_clients (                  -- Phase 2 預留：建表不使用
  client_id        TEXT PRIMARY KEY,
  metadata_url     TEXT,                      -- CIMD
  name             TEXT,
  redirect_uris_json TEXT,
  created_at       REAL NOT NULL,
  last_seen        REAL
);
```

- `page_index`：轉換完成（finalize）時以 `<!-- page: N -->` 切頁寫入，重轉時先刪後寫；啟動時對沒有索引的文件背景補建。沒有頁碼的文件（例如 docx）以整份為 `page = NULL` 一列。
- `mcp_calls` 保留 `call_log_retention_days`（預設 30 天），並有總筆數上限（預設 200,000），由既有 maintenance 週期清理。
- `mcp_clients` 的「活躍」定義：`last_seen` 在 5 分鐘內（新版協定無 session，無法以連線數計算）。

## 4. MCP 端點與協定

### 4.1 掛載

- 官方 SDK `mcp>=2.2,<2.3`，`MCPServer(name="Doc4AI Studio", instructions=…)`。
- Streamable HTTP，路徑 `/mcp`，掛在既有 FastAPI app（同一個 port），**在** web SPA catch-all 之前。
- `stateless_http=True`、`json_response=False`（讓 progress 通知可串流）。
- FastAPI lifespan 需進入 `mcp.session_manager.run()`（掛載的子 app 不會跑自己的 lifespan）。
- `EnvelopeMiddleware` 排除 `/mcp` 與 `/.well-known/`（協定回應不能被加上 `workspace` 欄位）。
- `/mcp` **不接受** `?token=`（規格禁止 token 放在 URL）。
- 不實作已淘汰的 HTTP+SSE transport。
- `server/discover` 的 `instructions` 簡述建議流程：「先 `search_library` 找頁 → `get_document_info` 看目錄與 token 估計 → `read_document` 讀指定頁」。

### 4.2 傳輸安全

- 預設綁 `127.0.0.1`。綁其他介面（tailnet／內網）時：
  - `TransportSecuritySettings.allowed_hosts` 自動填入綁定的 IP 與主機名（含 `:*` port 變體），可在設定補充（例 MagicDNS 名稱）；
  - `allowed_origins` 只允許網頁 UI 自己的 origin（非瀏覽器的 MCP client 不送 Origin）；
  - 後台顯示「此連線為 HTTP，token 在網路上以明文傳輸；tailnet 由 WireGuard 加密，一般內網則否」提示。
- `/mcp` **一律要 token**，即使來自 loopback（規格對本機 HTTP server 的要求）。

### 4.3 驗證流程

1. ASGI 外層 wrapper 取出 `Authorization: Bearer …`；沒有／格式錯（前綴或 CRC 不符）→ 401，記錄 `auth_error`。
2. `PatVerifier`：HMAC 查表 → 檢查 `revoked_at`、`expires_at` → 回傳 `Principal`。
3. 限流：每把 token token-bucket（`rate_limit_per_min`，預設 60 次 tools/call 每分鐘；`tools/list`、`server/discover` 不計）→ 超過回 429 + `Retry-After`，記錄 `rate_limited`。
4. 更新 `api_tokens.last_used_*`（節流：同一把 token 每 10 秒最多寫一次）與 `mcp_clients`。

**401 的回應方式（Phase 1）**：回 `401` + `WWW-Authenticate: Bearer realm="doc4ai", error="invalid_token"`，**不帶** `resource_metadata`，避免 client 誤啟動 OAuth 流程。Phase 2 才加上 `resource_metadata="…/.well-known/oauth-protected-resource"`。因此 Phase 1 用自己的 ASGI 驗證層（同一個 verifier 介面），而不是 SDK 的 `AuthSettings`；Phase 2 再評估切換。實作時須以 Claude Code、Cursor、VS Code 實測 401 行為（見 §9）。

## 5. Tools

### 5.1 共同規則

- 每個 tool：`title`、`annotations`（`readOnlyHint`／`destructiveHint`／`idempotentHint`／`openWorldHint`）、`inputSchema`、`outputSchema`；回傳 `structuredContent` 加一段精簡文字（給舊 client）。
- 回應預算：`response_token_budget`（預設 8,000 tokens，tiktoken 估算）；超過即截斷並回傳 `truncated: true` 與 `next`。
- 清單：opaque cursor（base64 JSON：排序鍵 + 最後一筆 id），`limit` 上限 20（`list_documents` 25）。
- 錯誤：`isError: true`，`structuredContent = {code, message, hint}`；協定層錯誤（JSON 格式、未知方法）才用 JSON-RPC error。
- 相容：名稱穩定；擴充只加選填參數或新 tool。
- 文件識別：`doc_id`（既有 documents.id）。
- 逐頁品質：讀到被標記的頁（`warn`／修復過）時，結果附 `page_warnings: [{page, reason}]`。

### 5.2 Tool 一覽

| Tool | scope | annotations |
|---|---|---|
| `search_library` | read | readOnly, idempotent, closed-world |
| `list_documents` | read | readOnly, idempotent |
| `get_document_info` | read | readOnly, idempotent |
| `read_document` | read | readOnly, idempotent |
| `get_chunks` | read | readOnly, idempotent |
| `get_job` | read | readOnly |
| `convert_document` | convert | idempotent（同內容命中快取）, not destructive |
| `convert_path` | convert:local | idempotent, not destructive |
| `cancel_job` | manage | destructive |
| `reconvert_document` | manage | not idempotent, not destructive |
| `delete_document`（1.5） | manage | destructive |
| `verify_document`（2026-10-05，`docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md`） | read | readOnly, idempotent |
| `verify_markdown`（2026-10-05，同上） | convert | idempotent, not destructive |

### 5.3 定義

**`search_library`**
- 輸入：`query: str`（1–200 字）、`filters?: {engine?, level?: ok|warn|low, flagged?: bool, doc_ids?: [str]}`、`limit?: 1–20 = 10`、`cursor?`
- 輸出：`{hits: [{doc_id, title, page, snippet, score}], next_cursor?}`；snippet 約 300 字，前後以 `…` 標示；每筆附 `resource_link` → `doc4ai://documents/{doc_id}/pages/{page}`。
- 排序：FTS5 bm25；同文件命中多頁時最多回 3 頁，其餘以 `more_in_doc: n` 表示。

**`list_documents`**
- 輸入：`cursor?`、`limit?: 1–25 = 25`、`sort?: updated_desc|title_asc = updated_desc`、`filters?: {engine?, level?, flagged?, q?}`
- 輸出：`{documents: [{doc_id, title, source_name, pages, engine, quality: {level, score}, flagged_pages, updated_at}], next_cursor?}`

**`get_document_info`**
- 輸入：`doc_id`
- 輸出：`{doc_id, title, pages, engine, lang, quality: {level, score, reasons}, page_map: {coverage, alignment}, flagged_pages: [{page, reason, repaired}], outline: [{level, title, page}], token_estimate: {total, per_page_avg, ranges: [{pages, tokens}]}, chunks: n, resources: [...]}`
- `outline` 最多 200 筆，超過截斷並標示。`ranges` 以每 20 頁一組估 token，讓 AI 規劃要讀哪幾段。

**`read_document`**
- 輸入：`doc_id`、`pages?: "12" | "12-15"` 或 `heading?: str`（二擇一；都沒給 = 從第 1 頁）、`max_tokens?: 500–8000 = 6000`
- 輸出：`{doc_id, pages: {start, end}, markdown, truncated, next?: {pages: "16-19"}, page_warnings?}`；markdown 保留 `<!-- page: N -->`。
- 無頁碼的文件：`pages` 改以「段」（chunk 序號）分頁，`next` 回 `{offset}`。
- 圖片：markdown 中的圖改寫為 `doc4ai://documents/{doc_id}/assets/{name}`（client 可用 resources/read 取得），不內嵌 base64。
- 錯誤：`page_range_invalid`（附實際頁數）、`heading_not_found`（附最接近的 3 個標題）。

**`get_chunks`**
- 輸入：`doc_id`、`max_tokens?: 200–2000 = 800`、`cursor?`、`limit?: 1–20 = 10`
- 輸出：`{chunks: [{chunk_id, heading_path, page_start, page_end, text}], next_cursor?}`；切段參數與 `aidoc chunk` 一致，以 `(doc_id, max_tokens)` 快取。

**`get_job`**
- 輸入：`job_id`
- 輸出：`{job_id, status, progress: {pages_done, pages_total}, tasks: [{source_name, status, engine, quality_level, doc_id?}], error?}`

**`convert_document`**
- 輸入：`filename`、`content_base64`（解碼後 ≤ `mcp.max_upload_mb` = 20 MB）、`engine?`、`lang?: cht|en`、`force?`、`wait_seconds?: 0–30 = 0`
- 行為：寫入上傳區 → 建立 job（`origin = "mcp"`、記錄 token_id）→ 立即回傳；`wait_seconds > 0` 時在期限內等待，若 client 提供 `progressToken` 則送 progress 通知。
- 輸出：`{job_id, status, doc_id?}`（同內容命中快取時直接回 `doc_id`）。
- 檢查：副檔名與 magic bytes、磁碟空間（沿用 3× 規則）、單一 token 同時進行中的 job 數上限（預設 3）。

**`convert_path`**
- 輸入：`path`（絕對路徑）、其餘同上
- 限制：`path` 解析後（resolve、拒絕 UNC／`\\?\`、symlink 指出白名單外）必須在 `mcp.local_path_roots` 之內；白名單為空時此 tool 直接不註冊。

**`cancel_job`**：輸入 `job_id`；已結束的 job 回 `{changed: false}`。
**`reconvert_document`**：輸入 `doc_id`、`engine?`；原始檔不在時回 `source_missing`。
**`delete_document`**（1.5）：輸入 `doc_id`、`confirm_title`（必須與標題相同，防誤刪）。

## 6. Resources 與 Prompts

Resource templates（`doc4ai:read`）：

| URI | 內容 | mimeType |
|---|---|---|
| `doc4ai://documents/{doc_id}` | 整份 Markdown | text/markdown |
| `doc4ai://documents/{doc_id}/pages/{range}` | 指定頁 | text/markdown |
| `doc4ai://documents/{doc_id}/metadata` | sidecar JSON | application/json |
| `doc4ai://documents/{doc_id}/assets/{name}` | 圖片 | image/* （blob） |
| `doc4ai://chunks/{doc_id}/{chunk_id}` | 單一切段 | text/markdown |

- `resources/list` 只列文件層（分頁、cursor），附 `ttlMs` 快取提示（60 秒）。
- 整份 Markdown 可能很大；client 自行決定是否讀取。工具結果一律優先給 `resource_link`。
- 不依賴 client 支援 resource link：所有內容都能透過 `read_document` 取得。

Prompts（1.5，先保留名稱）：`summarize_document(doc_id, pages?)`、`ask_document(doc_id, question)`。

## 7. 管理 API（`/api/mcp/*`，只接受既有的 server token 或 loopback；PAT 無效）

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | `/api/mcp/status` | `{enabled, endpoint_urls: [..], bind, allowed_hosts, protocol_versions, sdk_version, active_clients, calls_24h, errors_24h}` |
| GET | `/api/mcp/tokens` | 清單（不含 hash），每筆附 24 小時呼叫數、錯誤數、最後 client |
| POST | `/api/mcp/tokens` | `{name, scopes, expires_in_days?, note?, rate_limit_per_min?}` → **回傳一次** `{token, ...}` |
| PATCH | `/api/mcp/tokens/{id}` | 改 `name`、`note`、`rate_limit_per_min`（scope 不可改，要改請輪替或重建） |
| POST | `/api/mcp/tokens/{id}/revoke` | `{reason?}` |
| POST | `/api/mcp/tokens/{id}/rotate` | 回傳新 token 一次；舊的立即失效 |
| GET | `/api/mcp/clients` | `?active=1` 只看 5 分鐘內；含 protocol_version、last_ip、request_count |
| GET | `/api/mcp/calls` | 篩選 `token_id, client_id, tool, status, since, until`；cursor 分頁；最多 100 筆/頁 |
| GET | `/api/mcp/stats` | `?window=24h|7d`：每個 tool 的呼叫數、錯誤率、p50/p95 延遲、回應 token 中位數；每把 token 的呼叫數 |
| GET | `/api/mcp/config-snippets?token_id=` | 各 client 的設定範例（token 以 `<YOUR_TOKEN>` 占位，建立當下的回應才帶真值） |
| PUT | `/api/settings`（既有） | 新增 `[mcp]` 白名單鍵（見 §10） |

SSE（既有 `/api/events`）新增事件：`mcp.call`（摘要：ts、token 名稱、tool、status、duration）、`mcp.client`（新 client 出現／變為活躍／變為閒置）、`mcp.token`（建立／撤銷／輪替）。

## 8. 後台介面（新頁面「MCP」，側邊欄位於 Settings 之上）

分頁：

1. **總覽**
   - 狀態卡：MCP 啟用中、endpoint URL（可複製；綁 tailnet 時同時列 MagicDNS 與 IP）、支援的協定版本、SDK 版本。
   - 指標：活躍 client 數、24 小時呼叫數、錯誤率、p95 延遲（小型 sparkline）。
   - 安全提示：HTTP 明文、`convert:local` 白名單狀態、即將到期的 token。
   - **連線設定**：分頁切換 Claude Code／Cursor／VS Code／Claude Desktop（經 `mcp-remote` 橋接）的設定片段，一鍵複製。
2. **Tokens**
   - 表格：名稱、前綴、scopes（徽章）、建立、到期（剩餘天數；14 天內顯示警示）、最後使用（時間 + client + IP）、24h 呼叫、狀態（有效／已過期／已撤銷）。
   - 「建立 token」對話框：名稱、scopes 勾選（`convert:local`、`manage` 紅色警示）、到期（30／90／180／365 天）、備註、限流。
   - **建立後的一次性視窗**：token 原文 + 複製按鈕 + 「關閉後無法再次查看」提示 + 依剛建立的 token 預填好的各 client 設定片段。
   - 每列動作：輪替、撤銷（確認對話框，可填原因）、編輯名稱／備註。
3. **連線**
   - 活躍 client（5 分鐘內）卡片：client 名稱與版本、所屬 token、協定版本、IP、最後請求時間、本次活躍期間的請求數；即時更新（SSE）。
   - 歷史 client 清單（可依 token 篩選）。
4. **呼叫紀錄**
   - 即時串流的表格：時間、token、client、method／tool、狀態徽章、延遲、回應大小、job 連結。
   - 篩選：token、client、tool、狀態、時間範圍；點一列展開 args_summary 與錯誤訊息。
   - 統計：每個 tool 的呼叫數、錯誤率、p95 延遲。

設計風格沿用既有網頁（Linear／Vercel 等級、深淺色、窄螢幕可用）。

### 8.1 Client 設定範例（後台產生）

```bash
# Claude Code
claude mcp add --transport http doc4ai http://ulove-arrangement.tail74077f.ts.net:3333/mcp \
  --header "Authorization: Bearer <YOUR_TOKEN>"
```

```json
// Cursor  (~/.cursor/mcp.json)
{ "mcpServers": { "doc4ai": { "url": "http://<host>:<port>/mcp",
  "headers": { "Authorization": "Bearer <YOUR_TOKEN>" } } } }
```

```json
// VS Code  (.vscode/mcp.json)
{ "servers": { "doc4ai": { "type": "http", "url": "http://<host>:<port>/mcp",
  "headers": { "Authorization": "Bearer ${input:doc4ai-token}" } } },
  "inputs": [ { "id": "doc4ai-token", "type": "promptString", "password": true,
  "description": "Doc4AI Studio token" } ] }
```

```json
// Claude Desktop（custom connector 連不到本機／內網，改用 mcp-remote 橋接）
{ "mcpServers": { "doc4ai": { "command": "npx", "args": ["-y", "mcp-remote",
  "http://<host>:<port>/mcp", "--header", "Authorization:${DOC4AI_TOKEN}", "--allow-http"],
  "env": { "DOC4AI_TOKEN": "Bearer <YOUR_TOKEN>" } } } }
```

確切語法以實作時各 client 的官方文件為準（研究文件 §6 有來源），由 `/api/mcp/config-snippets` 單一來源產生，UI 與 README 共用。

## 9. 錯誤處理與邊界情況

| 情況 | 行為 |
|---|---|
| token 過期／撤銷 | 401（`invalid_token`），記錄 `auth_error`，`token_prefix_seen` |
| scope 不足 | tool 不在清單；硬呼叫 → `isError` + `forbidden_scope` |
| 限流 | 429 + `Retry-After`；`rate_limited` |
| 文件不存在／已刪 | `document_not_found` |
| 文件正在重轉 | 讀舊版（輸出在 finalize 前不變），結果附 `stale: true, job_id` |
| 頁碼不完整的文件 | `read_document` 以 chunk 分頁並在結果說明；`get_document_info` 顯示 coverage |
| 回應超出預算 | 截斷於段落／頁邊界，`truncated: true` + `next` |
| 上傳過大 | `file_too_large`（附上限） |
| `convert_path` 不在白名單 | `path_not_allowed`（不透露白名單內容） |
| MCP 停用（`mcp.enabled = false`） | `/mcp` 回 404；後台顯示停用 |
| 伺服器重啟 | 無 session 狀態可失；client 下一次請求照常 |

**實作時必須實測**（研究文件標註的不確定點）：
1. Claude Code、Cursor、VS Code 收到 401（撤銷的 token）時的行為：是否誤啟動 OAuth？
2. 各 client 使用的協定版本（後台顯示，用來確認兩代都正常）。
3. Claude Desktop 經 `mcp-remote` 連 HTTP（非 HTTPS）tailnet 位址是否需要 `--allow-http`。

## 10. 設定（`aidoc.toml` 新增 `[mcp]`）

```toml
[mcp]
enabled = true
allowed_hosts = []                 # 額外允許的 Host（綁定介面會自動加入）
local_path_roots = []              # convert_path 白名單；空 = 不註冊 convert_path
default_token_ttl_days = 90
max_token_ttl_days = 365
allow_no_expiry = false
rate_limit_per_min = 60
max_concurrent_jobs_per_token = 3
max_upload_mb = 20
response_token_budget = 8000
call_log_retention_days = 30
call_log_max_rows = 200000
```

`PUT /api/settings` 白名單加入以上鍵（`local_path_roots` 拒絕 UNC 與不存在的路徑）。

## 11. 測試

- **單元**：token 產生／CRC／HMAC 驗證、到期、撤銷、輪替；scope decorator；cursor 編解碼；回應預算截斷；`convert_path` 白名單（UNC、`..`、symlink、大小寫）；`page_index` 切頁與查詢（中英混排、trigram）。
- **協定**：以 SDK 的 client 對 in-process app 跑兩代協定（2026-07-28 無握手；2025-11-25 `initialize`）：`tools/list` 依 scope 過濾、每個 tool 的 `outputSchema` 驗證 `structuredContent`、progress 通知、錯誤格式。
- **安全**：PAT 呼叫 `/api/*` → 401/403；`?token=` 在 `/mcp` 無效；錯誤 Host／Origin → 拒絕；token 原文不出現在任何 log、`mcp_calls`、SSE。
- **後台**：Vitest（token 對話框一次性顯示、狀態徽章、即時串流合併）；Playwright e2e：建立 token → 用 SDK client 呼叫 → 後台「連線」與「呼叫紀錄」即時出現 → 撤銷 → 下一次呼叫 401 且紀錄出現。
- **真實 client 驗收**（手動清單，記錄於計畫）：Claude Code 對 1192 頁的書完成「搜尋 → 目錄 → 讀 3 頁」、每次回應 ≤ 8,000 tokens；Cursor、VS Code 各至少 `tools/list` + 一次 `read_document`；Claude Desktop 經 `mcp-remote`。

## 12. Phase 2 預留（不在本次實作）

- 公開 HTTPS（反向代理或 tunnel）；canonical resource URL。
- `/.well-known/oauth-protected-resource`（RFC 9728）與 401 的 `resource_metadata`。
- 授權伺服器：外部 IdP（Keycloak／Authentik／Auth0／Entra）優先；若自建須支援 RFC 8414、PKCE S256、CIMD、DCR、RFC 9207 `iss`、refresh token。
- 驗證器加 JWT/JWKS 分支並驗 `aud`；`Principal(kind="oauth")`；`oauth_clients` 開始使用。
- redirect URI 白名單：`https://claude.ai/api/mcp/auth_callback` 與 ChatGPT 的 redirect。
- 後台：OAuth client 清單與授權紀錄（沿用「連線」「呼叫紀錄」頁）。

## 13. 不在範圍內

- 多使用者帳號與工作區（`workspace` 欄位持續保留 `default`）。
- 已淘汰的 HTTP+SSE transport、sampling、roots、logging 能力。
- tasks 擴充（長任務改以 `get_job` 輪詢）。
- `url` 來源的轉換（SSRF 風險，另案評估）。
