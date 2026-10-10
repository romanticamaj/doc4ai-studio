# RAGFlow → Doc4AI Studio（docling-serve 相容 API）

**可行性：B** — 協定相容，但有兩個限制：RAGFlow 只會連**公開可路由**的位址，而且它會先試兩次 docling 切段請求。

依據：RAGFlow `v1.0.0-rc1` 的 `internal/parser/parser/pdf_parser_docling.go`、`internal/common/http.go`
（查證日 2026-10-10，spike `docs/superpowers/spikes/2026-10-compat.md` S1）；`docs/faq.mdx`「How do I use an
external Docling Serve server」。

## 設定

RAGFlow 的 `.env`（或每個 parser 的 `docling_server_url`／`docling_api_key`）：

```bash
DOCLING_SERVER_URL=https://<doc4ai 的公開網址>     # 不要加 /v1
DOCLING_API_KEY=doc4ai_pat_…                     # 需要 doc4ai:convert；RAGFlow 以 Authorization: Bearer 送，Doc4AI 兩種 header 都收
```

然後在知識庫的 PDF parser 選 **Docling**。

## 限制 1：只能連公開位址

RAGFlow 1.0 的 Docling parser 用 `GetSSRFHTTPClient()`，它的 `strictSSRFTransport` 拒絕解析到私有網段、
loopback、link-local、**CGNAT（100.64.0.0/10，也就是所有 Tailscale 位址）**的主機，沒有設定可以關掉（只有
測試用的記憶體旗標）。所以 `http://192.168.x.x:…`、`http://100.x.y.z:…`、`http://<名稱>.ts.net:…`
（解析到 100.x）都會被 RAGFlow 自己擋下，請求根本不會到 Doc4AI。

可行做法：讓 Doc4AI 有一個解析到公開 IP 的 HTTPS 網址——例如 Tailscale Funnel 或反向代理（部署方式見 MCP 散布
規格的 Spec 4）。公開曝露前請先看 MCP 頁的安全提示。

## 限制 2：先試切段、再試標準請求

RAGFlow 依序嘗試：

1. `POST /v1/convert/source`，`do_chunking: true` + `chunking_options`（要 docling 的切段結果）
2. `POST /v1alpha/convert/source`，同上（舊版 `file_sources` 形狀）
3. `POST /v1/convert/source`，標準請求（`to_formats: ["json", "md", "text"]`）
4. `POST /v1alpha/convert/source`，標準請求

Doc4AI 不做 docling 切段：第 1、2 個請求會照常轉換並回 `document.md_content`（`do_chunking` 列在
`document.doc4ai.ignored_options`），RAGFlow 在回應裡找不到 chunks 就換下一個；**第 3 個請求的回應才被採用**，
它讀 `md_content`，再用自己的切段模板切 Markdown。第 1 個請求做完真正的轉換，第 2、3 個是快取命中（秒回）。
`json_content` 永遠是 `null`——RAGFlow 優先讀 `md_content`，不受影響（spike S1）。

RAGFlow 不檢查 `status`：轉換失敗時 `md_content` 是 `null`，RAGFlow 會報「standard response contained no parsed
document」。它的 HTTP client 等回應標頭最多 20 分鐘，長於 Doc4AI 的 600 秒同步上限。

## 頁碼

RAGFlow 不送 `md_page_break_placeholder`，所以 `md_content` 保留 `<!-- page: N -->` 標記；RAGFlow 的 Docling
路徑不從 Markdown 讀頁碼，但標記會留在切出來的段落文字裡，LLM 引用時看得到。

## 未驗證

- 實際的 RAGFlow 1.0 部署對 Doc4AI 的端到端解析（自動測試重放的是它的四種請求形狀：
  `tests/compat/shapes/ragflow_1_0_*.json`）。
- 公開網址（Funnel／反向代理）下 RAGFlow 的 SSRF 檢查與 DNS 釘選是否還有其他限制。
