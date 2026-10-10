# Kotaemon → Doc4AI Studio

**可行性：B** — 需要放一個檔案（參考實作 `kotaemon_doc4ai_loader.py`），不用改 Kotaemon 原始碼。

依據：Kotaemon v0.12.0 `libs/kotaemon/kotaemon/loaders/docling_loader.py`、
`libs/ktem/ktem/index/file/pipelines.py`（查證日 2026-10-10）。

## 為什麼不能直接用 Kotaemon 的 Docling 選項

Kotaemon 的 `DoclingReader` 在 Kotaemon 的行程**內**跑 docling（`from docling.document_converter import
DocumentConverter`），沒有 server URL 可填，所以無法「指到」Doc4AI。但 Kotaemon 的檔案索引讀
`flowsettings.py` 的 `FILE_INDEX_PIPELINE_FILE_EXTRACTORS`（副檔名 → reader 的 dotted path），而且它會蓋過
UI 上選的 reader——這就是接 Doc4AI 的入口。

## 設定

1. 把 [`kotaemon_doc4ai_loader.py`](kotaemon_doc4ai_loader.py) 複製到 Kotaemon 的 PYTHONPATH 上，檔名
   `doc4ai_loader.py`（例如和 `flowsettings.py` 同一層）。
2. `flowsettings.py`：

   ```python
   FILE_INDEX_PIPELINE_FILE_EXTRACTORS = {".pdf": "doc4ai_loader.Doc4AIReader"}
   ```

   要讓 Doc4AI 也處理 Office 檔，就加 `".docx"`、`".pptx"`… 同樣指到 `Doc4AIReader`。
3. 環境變數：

   ```bash
   DOC4AI_URL=http://<doc4ai-host>:<port>
   DOC4AI_API_KEY=doc4ai_pat_…     # 需要 doc4ai:convert
   ```

`Doc4AIReader` 送 `POST {DOC4AI_URL}/v1/convert/file`（multipart `files`、`md_page_break_placeholder=\f`、
`X-Api-Key`），依 `\f` 切成一頁一個 Document，`metadata.page_label` 是真實頁碼（1 起算），另帶
`doc4ai_doc_id`、`doc4ai_engine`、`doc4ai_quality`。轉換失敗（`status: failure`）時丟出例外，訊息是 Doc4AI 的
`errors[].error_message`。

## 與 Kotaemon 的 DoclingReader 的差異

- Kotaemon 的 DoclingReader 另外輸出表格與圖片 Document（含圖說）；Doc4AIReader 只輸出每頁的 Markdown
  （表格在 Markdown 裡，圖片是 `<!-- image -->` 占位）。
- 掃描檔、壞字型 PDF 由 Doc4AI 的路由與 OCR 修復處理，Kotaemon 這邊不需要裝 docling 與它的模型。

## 未驗證

- 在實際的 Kotaemon v0.12 部署中載入 `Doc4AIReader`（本 repo 只驗證它送出的請求形狀：
  `tests/compat/shapes/kotaemon_doc4ai_loader.json`，以及檔案可以編譯）。
- `FILE_INDEX_PIPELINE_FILE_EXTRACTORS` 以無參數建構 reader：URL 與 token 因此從環境變數讀。
