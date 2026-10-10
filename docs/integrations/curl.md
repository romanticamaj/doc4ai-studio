# curl（以及任何會說 docling-serve 的 client）

**可行性：A** — Doc4AI Studio 在同一個 port 提供 docling-serve v1 HTTP API 的子集（規格 §4）。Haystack
`DoclingServeConverter`（檔案上傳）、n8n 的 docling-serve 節點等 client 都用同樣的請求。

token：Web 介面 **MCP → Tokens** 建立，需要 `doc4ai:convert`（只讀結果的 token 用 `doc4ai:read`）。以
`X-Api-Key: <token>` 或 `Authorization: Bearer <token>` 送；**不要**放在 URL（`?api_key=` 會被拒絕，`400`）。

```bash
export DOC4AI=http://127.0.0.1:8765        # 或 tailnet 位址，例如 http://<名稱>.ts.net:3333
export PAT=doc4ai_pat_…
```

## 同步

```bash
# 檔案上傳；頁與頁之間放 <!-- page --> 方便切
curl -s -H "X-Api-Key: $PAT" -F "files=@report.pdf" -F "md_page_break_placeholder=<!-- page -->" \
     $DOC4AI/v1/convert/file | jq -r .document.md_content

# base64（JSON），一次一個 source
curl -s -H "Authorization: Bearer $PAT" -H "Content-Type: application/json" \
     -d "{\"options\": {\"to_formats\": [\"md\", \"text\"]}, \"sources\": [{\"kind\": \"file\", \"filename\": \"report.pdf\", \"base64_string\": \"$(base64 -w0 report.pdf)\"}]}" \
     $DOC4AI/v1/convert/source | jq '.status, .document.doc4ai'
```

最多等 `compat.sync_timeout_s`（600 秒）；超過回 `504`，body 有 `task_id`、`status_url`、`result_url`，工作
繼續跑，用下面的非同步端點取回。

## 非同步

```bash
TASK=$(curl -s -H "X-Api-Key: $PAT" -F "files=@big.pdf" $DOC4AI/v1/convert/file/async | jq -r .task_id)
curl -s -H "X-Api-Key: $PAT" "$DOC4AI/v1/status/poll/$TASK?wait=30" | jq '.task_status, .task_meta'
curl -s -H "X-Api-Key: $PAT" $DOC4AI/v1/result/$TASK | jq -r .document.md_content
```

- `task_status`：`pending` → `started` → `success` | `failure`；`task_meta` 有 docling 的計數加上 Doc4AI 的
  `pages_done`／`pages_total`。`wait` 上限 `compat.poll_max_wait_s`（60 秒）。
- 還沒完成就要 `result` → `404 {"detail": "task not finished"}`；失敗 → `TaskFailureResult`。
- 只有建立 task 的那把 token 看得到它；task 紀錄保留 `compat.task_retention_days`（30 天）。

## 其他端點

| 端點 | 說明 |
|---|---|
| `GET /health` | 不需 token，`{"status": "ok"}` |
| `GET /v1/capabilities` | 不需 token；支援的格式、引擎是否就緒、上限 |
| `GET /v1/doc4ai/documents/{doc_id}/assets/{name}` | `image_export_mode=referenced` 時圖片的位置（需要 `doc4ai:read`） |
| `POST /v1alpha/convert/source` | 舊版 `file_sources` 形狀（RAGFlow 的退路） |

## 選項

docling 的 `ConvertDocumentsOptions` 欄位都收；Doc4AI 用到的是 `to_formats`（`md`、`text`；其他格式回 `null`
並列在 `doc4ai.unsupported_formats`）、`md_page_break_placeholder`、`image_export_mode`（`placeholder` 預設、
`referenced`、`embedded`）、`ocr_lang`、`force_ocr`、`document_timeout`，以及擴充鍵 `doc4ai_engine`、
`doc4ai_lang`、`doc4ai_force`、`doc4ai_keep_page_markers`。其餘選項忽略並列在 `document.doc4ai.ignored_options`。
不支援：`sources[].kind = "http"`（伺服器端抓 URL；`422 unsupported_source: http`）、`target` 非 `inbody`、
一次多個檔案、`/v1/chunk/*`。

## 未驗證

- Haystack `DoclingServeConverter` 的 URL 來源（`/v1/convert/source` + `kind: http`）會得到 `422`，只有檔案／
  ByteStream 來源可用；n8n 節點未實測。
