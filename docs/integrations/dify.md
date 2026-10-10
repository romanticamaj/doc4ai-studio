# Dify → Doc4AI Studio

**可行性：C** — 需要另寫 Dify tool plugin（另案）；改設定接不上。

依據：Dify `api/core/rag/extractor/extract_processor.py`、外掛 `langgenius/mineru` v0.5.8 與 `s20ss/docling`
v0.0.1（查證日 2026-10-05，規格 §3.5、§8.4）。

## 為什麼改設定接不上

- Dify 的 `ETL_TYPE=Unstructured` 只讓 `.doc .msg .eml .ppt .pptx .xml .epub`（自動模式再加 `.md`）走外部服務；
  **PDF 永遠由內建的 `PdfExtractor`（pypdfium2）處理**。所以就算 Doc4AI 提供 Unstructured 相容 API，也攔不到
  PDF——本規格不做。
- Dify 市集上的 Docling 外掛（`s20ss/docling`）在 Dify 行程內跑 docling，沒有 server URL 可填。

## 可行路徑：Dify tool plugin

仿 MinerU 官方外掛的 local 模式寫一個 Dify tool plugin（獨立的 Python 套件，`dify_plugin` SDK）：

- credentials：`base_url`、`api_key`（Doc4AI 的 PAT，`doc4ai:convert`）；驗證連線用 `GET {base_url}/health`
  （公開、回 `{"status": "ok"}`）。
- tool `convert_document(file)`：`POST {base_url}/v1/convert/file`，multipart `files`、header
  `X-Api-Key`；回 `document.md_content`（含 `<!-- page: N -->`）與 `document.doc4ai`（doc_id、引擎、品質）。
- 放進 Knowledge Pipeline 的解析節點，取代內建 PDF 解析。

Doc4AI 這邊需要的兩個端點（`/v1/convert/file`、`/health`）已經提供，形狀與 docling-serve 相同；外掛本身另案。

## 未驗證

- Dify Knowledge Pipeline 的解析節點能否以 tool plugin 的輸出完全取代內建 `PdfExtractor`，以及頁碼 metadata
  如何傳遞。
- 本 repo 沒有 Dify 外掛的程式與測試。
