# T-003 Docling 對超大頁面輸出空白（Pillow decompression-bomb 上限）

- 狀態：**已修正**（2026-10-22）。`DoclingEngine.build_request()` 對頁面超過上限的 PDF 改送縮放後的副本。
- 建立：2026-10-22
- 回報：真實樣本評估（`docs/superpowers/evals/2026-10-22-real-sample-eval-record.md`）。驗收時由協調者指出。
- 嚴重度：中。整份文件沒有任何文字，但品質層級是 `low`，使用者會被提醒；`auto` 走 MinerU 優先，所以預設路由不會中，只有 `--engine docling` 或 Docling 當修復引擎時會中。

## 現象

`zh-supreme-court-1972`：4 頁，每頁是 6253×4428 pt 的雙頁跨頁掃描。以 `--engine docling` 轉換時，4 個頁標記都在，但沒有任何文字（頁均 NED 0.000、狀態 `low`）。sidecar 的每頁錯誤是：

```
Image size (249221124 pixels) exceeds limit of 178956970 pixels, could be decompression bomb DOS attack.
```

## 根因

- Docling 的 OCR 階段以 scale 3（216 dpi）渲染整頁：6253 × 4428 × 3² ≈ 249.2 M px。
- Pillow 的預設 `Image.MAX_IMAGE_PIXELS` 是 89,478,485。超過它的 2 倍（178,956,970）就丟出 `DecompressionBombError`，Docling 把每頁記為失敗（PARTIAL_SUCCESS），頁內容是空的。
- `docling_runner.conversion_errors()` 有把失敗頁帶回來，品質層級因此是 `low`：錯誤沒有被吞掉，但頁面也沒有被救回。

## 流程缺口

1. 合成 fixture 與 bench 語料全都是 A4／Letter 尺寸，沒有任何「頁面盒子很大」的樣本，渲染上限這條路徑從沒被跑過。
2. 引擎 runner 的輸入沒有前置檢查。頁面尺寸是 probe 階段就知道的事，卻沒有任何一層對照下游渲染器的上限。

## 修正

- `src/aidoc/engines/docling.py`：
  - 新增 `cap_page_size(src, workdir)`：任何頁面在 scale 3 下超過 `RENDER_PX_CAP`（Pillow 錯誤門檻的 90 %）時，就在工作目錄寫一份副本。
  - 副本只把那些頁以 `show_pdf_page` 等比縮小（向量內容與長寬比保留，等於降低有效 DPI），其他頁原樣複製。
  - `DoclingEngine.build_request()` 對每個 PDF 請求（包含逐頁修復）都經過這一步，`req["render_cap"]` 記下縮放了哪些頁與倍率。
- **沒有**在任何地方關閉 Pillow 的 bomb 檢查（`MAX_IMAGE_PIXELS` 不動）。
- 6000×4400 pt 的頁縮成約 0.82 倍，OCR 有效解析度約 178 dpi。

## 驗證

- 單元測試 `tests/unit/test_docling_render_cap.py`：
  - 合成 6000×4400 pt 頁縮到上限內，長寬比與內容保留；
  - 正常頁不複製；
  - `build_request` 送出副本；
  - 圖片輸入不受影響。
- 慢速測試 `tests/integration/test_engine_docling.py::test_a_huge_scanned_page_is_not_rejected_as_a_decompression_bomb`：真實 Docling env，在 6000×4400 pt 的掃描頁上：
  - 修正前（把 `cap_page_size` 換成恆等函式）：失敗頁訊息 `Image size (237600000 pixels) exceeds limit of 178956970 pixels`，沒有文字；
  - 修正後：OCR 出文字，沒有失敗頁。
- 真實樣本只作回歸用（spec 2026-10-22 §8.2），不作任何決策。
