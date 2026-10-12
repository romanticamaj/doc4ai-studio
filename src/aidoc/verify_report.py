"""Self-contained HTML report of `aidoc verify` (spec 2026-10-05 verify §2.4): one file, inline CSS (light and dark),
no script, no external resource — it opens offline and can be attached to a PR."""
from __future__ import annotations

import base64
import html as _html
from pathlib import Path

from aidoc.models import VerifyReport

HTML_THUMB_WIDTH = 160
HTML_MAX_THUMBS = 200
HTML_MAX_ROWS = 2000

_T = {
    "en": {"title": "Verify report", "pages": "pages", "level": "Level", "score": "Score", "reasons": "Document reasons",
           "flagged": "Flagged pages", "advisories": "Advisories", "page": "Page", "page_reasons": "Reasons",
           "page_adv": "Advisories", "metrics": "Key metrics", "thumb": "Page", "none": "none",
           "inferred": "Page boundaries inferred (no page markers)", "anchored": "anchored", "decidable": "decidable",
           "confidence": "confidence", "coverage": "Page-marker coverage", "unverifiable":
           "Unverifiable: a scanned PDF without page markers has no text layer to anchor on.",
           "partial": "Partial: no PDF — only marker coverage and garbage were checked.",
           "generated": "Generated", "limits": "verify sees structural losses only (dropped, shifted or missing "
           "pages, broken text layers, garbage, missing tables); it cannot see OCR misreadings or hallucinations.",
           "no_pages": "No flagged pages.", "truncated": "rows not shown"},
    "cht": {"title": "驗證報告", "pages": "頁", "level": "等級", "score": "分數", "reasons": "文件層級理由",
            "flagged": "被標記頁", "advisories": "建議", "page": "頁", "page_reasons": "理由", "page_adv": "建議",
            "metrics": "關鍵指標", "thumb": "縮圖", "none": "無", "inferred": "頁界由推斷而來（沒有頁碼標記）",
            "anchored": "有錨點", "decidable": "可判定", "confidence": "信心", "coverage": "頁碼標記覆蓋率",
            "unverifiable": "無法驗證：掃描 PDF 且沒有頁碼標記，沒有文字層可以對齊。",
            "partial": "部分驗證：沒有 PDF，只檢查了標記覆蓋率與亂碼。", "generated": "產生時間",
            "limits": "verify 只看結構性損失（漏頁、錯頁、缺頁、壞文字層、亂碼、漏表格），看不出 OCR 認錯字或幻覺。",
            "no_pages": "沒有被標記的頁。", "truncated": "列未顯示"},
}

_CSS = """
:root{--bg:#ffffff;--fg:#1d2330;--muted:#5d6675;--line:#d9dee7;--card:#f5f7fa;--ok:#1f7a4a;--warn:#9a6200;
--low:#b42318;--chip:#eef1f6}
@media (prefers-color-scheme: dark){:root{--bg:#14171c;--fg:#e6e9ef;--muted:#9aa3b2;--line:#2c323c;--card:#1b1f26;
--ok:#4cc38a;--warn:#f0b54a;--low:#ff7b72;--chip:#252b34}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,"Segoe UI","Noto Sans TC","Microsoft JhengHei",sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.card{background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:10px 14px;min-width:140px}.card b{display:block;font-size:18px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.low{color:var(--low)}
.chip{display:inline-block;background:var(--chip);border-radius:10px;padding:1px 8px;margin:1px 2px;font-size:12px}
table{border-collapse:collapse;width:100%;margin-top:12px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;
text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}td.num{text-align:right;width:60px}
img{border:1px solid var(--line);border-radius:4px;max-width:100%}code{font-size:12px}
.wrap{overflow-x:auto}footer{margin-top:20px;font-size:12px}
"""


def _e(s) -> str:
    return _html.escape(str(s), quote=True)


def thumbnails(pdf: Path, pages: list[int], width: int = HTML_THUMB_WIDTH,
               max_pages: int = HTML_MAX_THUMBS) -> dict[int, bytes]:
    """PNG thumbnails (`width` px wide) of the first `max_pages` of `pages` (1-based)."""
    import pymupdf
    out: dict[int, bytes] = {}
    with pymupdf.open(pdf) as doc:
        for p in sorted(set(pages)):
            if len(out) >= max_pages:
                break
            if not 1 <= p <= doc.page_count:
                continue
            page = doc[p - 1]
            zoom = width / max(1.0, page.rect.width)
            out[p] = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    return out


def _metrics_text(m: dict | None) -> str:
    if not m:
        return ""
    return " ".join(f"<code>{_e(k)}={_e(v)}</code>" for k, v in m.items())


def render_html(report: VerifyReport, thumbs: dict[int, bytes] | None, lang: str = "cht") -> str:
    t = _T.get(lang, _T["en"])
    j = report.to_json()
    s, pm, pdf = j["summary"], j["page_map"], j["pdf"]
    name = Path(j["input"]["markdown"]).name
    sub = _e(name) + (f" ← {_e(Path(pdf['path']).name)} ({pdf['pages']} {t['pages']})" if pdf else "")
    cards = [(t["level"], f'<b class="{_e(s["level"])}">{_e(s["level"])}</b>'),
             (t["score"], f"<b>{_e(s['score'])}</b>"),
             (t["flagged"], f"<b>{_e(s['pages_flagged'])}</b>"),
             (t["advisories"], f"<b>{_e(s['advisories'])}</b>")]
    if pm is not None and pm.get("method") == "inferred":
        cards.append((t["inferred"], (f"<b>{_e(pm['confidence'])}</b><span class=\"muted\">{t['confidence']} · "
                                      f"{t['anchored']} {_e(pm['anchored'])} / {t['decidable']} "
                                      f"{_e(pm['decidable'])}</span>")))
    elif pm is not None and pm.get("coverage") is not None:
        cards.append((t["coverage"], (f"<b>{_e(pm['coverage'])}</b><span class=\"muted\">{_e(pm.get('found'))} / "
                                      f"{_e(pm.get('expected'))}</span>")))
    parts = ["<!doctype html>",
             f'<html lang="{"zh-Hant" if lang == "cht" else "en"}"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f"<title>{_e(t['title'])}: {_e(name)}</title><style>{_CSS}</style></head><body><main>",
             f"<h1>{_e(t['title'])}</h1><div class=\"muted\">{sub}</div>"]
    if report.verifiable is False:
        parts.append(f'<p class="low">{_e(t["unverifiable"])}</p>')
    elif report.verifiable == "partial":
        parts.append(f'<p class="warn">{_e(t["partial"])}</p>')
    parts.append('<div class="cards">' + "".join(f'<div class="card"><span class="muted">{_e(k)}</span>{v}</div>'
                                                 for k, v in cards) + "</div>")
    reasons = s["reasons"]
    parts.append(f"<p>{_e(t['reasons'])}: " + ("".join(f'<span class="chip">{_e(r)}</span>' for r in reasons)
                                               if reasons else _e(t["none"])) + "</p>")
    rows = j["pages"]
    if rows:
        head = (f"<tr><th>{_e(t['page'])}</th><th>{_e(t['page_reasons'])}</th><th>{_e(t['page_adv'])}</th>"
                f"<th>{_e(t['metrics'])}</th>" + (f"<th>{_e(t['thumb'])}</th>" if thumbs else "") + "</tr>")
        body = []
        for e in rows[:HTML_MAX_ROWS]:
            img = ""
            if thumbs:
                data = thumbs.get(e["page"])
                img = ("<td>" + (f'<img alt="p{e["page"]}" src="data:image/png;base64,'
                                 f'{base64.b64encode(data).decode()}">' if data else "") + "</td>")
            body.append(f'<tr id="p{e["page"]}"><td class="num">{e["page"]}</td>'
                        f"<td>{''.join(f'<span class=chip>{_e(r)}</span>' for r in e.get('reasons') or [])}</td>"
                        f"<td>{''.join(f'<span class=chip>{_e(a)}</span>' for a in e.get('advisories') or [])}</td>"
                        f"<td>{_metrics_text(e.get('metrics'))}</td>{img}</tr>")
        parts.append(f'<div class="wrap"><table>{head}{"".join(body)}</table></div>')
        if len(rows) > HTML_MAX_ROWS:
            parts.append(f'<p class="muted">{len(rows) - HTML_MAX_ROWS} {_e(t["truncated"])}</p>')
    else:
        parts.append(f"<p>{_e(t['no_pages'])}</p>")
    parts.append(f'<footer class="muted"><p>{_e(t["limits"])}</p><p>{_e(t["generated"])} {_e(j["generated_at"])} · '
                 f"aidoc {_e(j['aidoc_version'])} · verify v{_e(j['verify_version'])}</p></footer>")
    parts.append("</main></body></html>")
    return "\n".join(parts)
