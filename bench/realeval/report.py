"""summary.md and the committed record skeleton of the real-sample evaluation (spec 2026-10-22 §10).

Only numbers, page numbers, file names, sha256 and verdicts are written — never document text (spec §9): this module
reads env.json, results.jsonl, vlm.jsonl, decisions.json and gt_quality.json, and never a GT file or an output."""
from __future__ import annotations

import json
from pathlib import Path

from bench.report import NONCLAIMS

EVAL_NONCLAIMS = ("**本評估專屬**：6 份繁中文件、4 篇論文、2 份英文對照，不是 benchmark；正解等級 3（繁中）／"
                  "4（英文對照）／機器產生（公式，未人工校對）；時間 n = 1；單一機器（Windows、單張 GPU）。")
CONFIGS = ("auto", "docling", "mineru")
TITLE = "# 真實樣本評估（繁中掃描路由、公式路由、本機 VLM）"


def fmt(v) -> str:
    """One formatting rule for every value in the decision tables."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x) for x in v) or "—"
    return str(v)


def _n(v, d: int = 3) -> str:
    return "—" if v is None else f"{v:.{d}f}"


def _jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _table(header: list[str], body: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _by(rows: list[dict], group: str) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r.get("group") == group:
            out.setdefault(r["doc"], {})[r["config"]] = r
    return out


def _worst(r: dict | None) -> str:
    w = (r or {}).get("worst")
    return f"p{w['page']} ({_n(w['ned'])})" if w else "—"


def env_block(env: dict) -> str:
    rows = [["date", fmt(env.get("date"))], ["aidoc_commit", fmt(env.get("aidoc_commit"))],
            ["aidoc_dirty", fmt(env.get("aidoc_dirty"))], ["spec / harness commit", fmt(env.get("spec_commit"))],
            ["gt_quality.json sha256", fmt(env.get("gt_quality_sha256"))],
            ["GPU", fmt((env.get("gpu") or {}).get("name"))], ["offline (declared)", fmt(env.get("offline_declared"))],
            ["fake", fmt(env.get("fake"))]]
    for name, sha in sorted((env.get("samples") or {}).items()):
        rows.append([f"sample {name}", fmt(sha)])
    for name, d in sorted((env.get("derived") or {}).items()):
        rows.append([f"derived {name}", f"{fmt(d.get('sha256'))} · text_chars {fmt(d.get('text_chars'))}"])
    for name, ok in sorted((env.get("vlm_pinned") or {}).items()):
        rows.append([f"pinned {name}", fmt(ok)])
    return _table(["field", "value"], rows)


def gt_block(gq: dict) -> str:
    pages = gq.get("pages") or []
    rows = [["等級", "3（繁中，Wikisource 一次校對）／4（英文對照）／機器（公式）"], ["抽查頁數", str(len(pages))],
            ["字數", fmt(gq.get("n"))], ["k", fmt(gq.get("k"))], ["CER_gt", _n(gq.get("cer"), 4)],
            ["95 % 上界（Wilson）", _n(gq.get("cer_upper"), 4)], ["頁對應", fmt(gq.get("alignment_ok"))],
            ["勘誤", fmt(gq.get("errata_count"))], ["公式 GT 低信心", fmt(gq.get("formula_gt_low_confidence"))],
            ["margin", _n(gq.get("margin"), 2)], ["抽查者 / 日期", f"{fmt(gq.get('author'))} / {fmt(gq.get('date'))}"]]
    return _table(["欄位", "值"], rows)


def table_a(rows: list[dict], dec: dict, group: str = "A") -> str:
    docs = _by(rows, group)
    body = []
    for doc, c in docs.items():
        body.append([doc, *[_n((c.get(k) or {}).get("ned")) for k in CONFIGS],
                     *[_n((c.get(k) or {}).get("cer")) for k in CONFIGS],
                     *[_n((c.get(k) or {}).get("f1")) for k in CONFIGS],
                     *[_worst(c.get(k)) for k in CONFIGS],
                     fmt((c.get("auto") or {}).get("engine_used")),
                     " / ".join(f"{k}:{(c.get(k) or {}).get('status', '—')}" for k in CONFIGS)])
    head = ["文件", *[f"NED {k}" for k in CONFIGS], *[f"CER {k}" for k in CONFIGS], *[f"F1 {k}" for k in CONFIGS],
            *[f"最差頁 {k}" for k in CONFIGS], "auto engine", "狀態"]
    out = _table(head, body)
    if group == "A":
        cond = (dec.get("zh_scan_route") or {}).get("conditions") or {}

        def v(name):
            return fmt((cond.get(name) or {}).get("value"))
        out += (f"\n\nΔ (docling − mineru, 頁加權) = {v('delta')}；95 % CI [{v('ci_lo')}, {v('ci_hi')}]；"
                f"計分頁 {v('n_pages')}；兩邊皆完成 {v('n_docs_both')} 份；wins_D {v('wins_D')}、wins_M {v('wins_M')}；"
                f"big_loss_D {v('big_loss_D')}、big_loss_M {v('big_loss_M')}")
    return out


def table_b(rows: list[dict]) -> str:
    body = []
    for doc, c in _by(rows, "B").items():
        for k in CONFIGS:
            r = c.get(k) or {}
            body.append([doc, k if k != "auto" else f"auto→{fmt(r.get('engine_used'))}", _n(r.get("R")),
                         _n(r.get("S")), _n(r.get("T")), _n(r.get("R_inline")), fmt(r.get("n_display")),
                         fmt(r.get("math_hint")), fmt(r.get("route_reason")), fmt(r.get("status"))])
    return _table(["論文", "設定", "R（行間）", "S", "T（本文護欄）", "R_inline（診斷）", "GT 行間數", "math_hint",
                   "auto 路由理由", "狀態"], body)


def table_v(vlm_rows: list[dict]) -> str:
    summ = next((r for r in vlm_rows if r.get("kind") == "summary"), {})
    body = [[doc, _n(d.get("recall")), fmt(d.get("pages")), _n(d.get("max_sec"), 2), _n(d.get("mean_sec"), 2),
             fmt(d.get("length_pages")), fmt(d.get("complete"))]
            for doc, d in (summ.get("per_doc") or {}).items()]
    out = _table(["文件", "recall（字元加權）", "頁", "最大 s", "平均 s", "length 頁", "完整"], body)
    vram = summ.get("vram") or {}
    out += (f"\n\n封面句：{fmt(summ.get('cover_hit'))}；最大請求 {_n(summ.get('max_sec_overall'), 2)} s；"
            f"顯存基線 {fmt(vram.get('baseline'))} MB、峰值 {fmt(vram.get('peak'))} MB、delta {fmt(vram.get('delta'))} MB；"
            f"模型載入 {_n(summ.get('load_seconds'), 1)} s；server {fmt(summ.get('server'))}；"
            f"端點 {fmt(summ.get('endpoint_host'))}")
    return out


def _cond_rows(conds: dict, prefix: str = "") -> list[list[str]]:
    return [[f"{prefix}{name}", fmt(c.get("value")), c.get("op", ""), fmt(c.get("threshold")), fmt(c.get("ok"))]
            for name, c in conds.items()]


def decisions_block(dec: dict) -> str:
    out = [f"margin {fmt(dec.get('margin'))}（CER_gt 上界 {fmt(dec.get('cer_upper'))}）；"
           f"有效性 {fmt(dec.get('validity_ok'))}：" + ", ".join(f"{k} {fmt(v)}" for k, v in
                                                            (dec.get("validity_checks") or {}).items())
           + "；範圍（2026-10-22 修訂）：" + ", ".join(f"{k} {fmt(v)}" for k, v in
                                               (dec.get("validity_scope") or {}).items())]
    titles = {"zh_scan_route": "(1a) 繁中掃描路由", "formula_route": "(1b) 公式路由", "vlm_local": "(2) 本機 VLM"}
    for key, title in titles.items():
        d = dec.get(key) or {}
        out += ["", f"### {title}：`{d.get('verdict')}`", ""]
        extra = []
        if d.get("change_to"):
            extra.append(f"改為 {fmt(d['change_to'])}")
        if d.get("failed"):
            extra.append("未成立：" + ", ".join(d["failed"]))
        if d.get("reason"):
            extra.append(d["reason"])
        for k in ("detector_note", "detector_finding", "on_pass", "known_limitation"):
            if d.get(k):
                extra.append(f"{k}: {d[k]}")
        if extra:
            out += ["；".join(extra), ""]
        rows = _cond_rows(d.get("conditions") or {})
        for doc, sides in (d.get("papers") or {}).items():
            for side in ("M", "D"):
                rows += _cond_rows(sides[side], f"{doc} {side} ")
        out.append(_table(["條件", "值", "op", "門檻", "成立"], rows))
        if d.get("needed_samples"):
            out += ["", "需要的樣本（§11）：" + "；".join(d["needed_samples"])]
    return "\n".join(out)


def _sections(run: Path) -> list[tuple[str, str]]:
    env = _json(run / "env.json")
    rows = _jsonl(run / "results.jsonl")
    dec = _json(run / "decisions.json")
    gq = _json(run / "gt_quality.json")
    tail = NONCLAIMS + "\n\n" + EVAL_NONCLAIMS
    reruns = env.get("reruns") or []
    rr = "\n".join(f"- {fmt(r.get('date'))} {fmt(r.get('pair'))}: {fmt(r.get('reason'))}" for r in reruns) or "無"
    return [("## 環境與凍結", env_block(env)), ("## GT 信心", gt_block(gq)),
            ("## 表 A — 繁中掃描（頁均 NED／CER／字袋 F1，最差頁）", table_a(rows, dec) + "\n\n" + tail),
            ("## 表 B — 公式論文（文件層級）", table_b(rows) + "\n\n" + tail),
            ("## 表 C — 英文對照（去文字層）", table_a(rows, dec, "C") + "\n\n" + tail),
            ("## 表 V — 本機 PaddleOCR-VL-1.6", table_v(_jsonl(run / "vlm.jsonl")) + "\n\n" + tail),
            ("## 決策（decisions.json，機械判定）", decisions_block(dec)), ("## 重跑", rr)]


def summary(run: Path) -> str:
    run = Path(run)
    parts = [TITLE, ""]
    for h, body in _sections(run):
        parts += [h, "", body, ""]
    parts += ["## 不宣稱", "", NONCLAIMS, "", EVAL_NONCLAIMS, ""]
    return "\n".join(parts)


def record_skeleton(run: Path) -> str:
    """The committed record (docs/superpowers/evals/…-record.md): the summary's sections plus empty Deviations and
    Commentary for the person who ran it."""
    run = Path(run)
    parts = [TITLE + " — 紀錄", "", f"run 目錄：`bench/results/{run.name}/`（不進版控）", ""]
    for h, body in _sections(run):
        parts += [h, "", body, ""]
    parts += ["## 偏差", "", "（執行者填寫：與計畫不同之處與理由）", "", "## 評註", "",
              "（只做解讀；結論以上方 decisions.json 為準）", "", "## 不宣稱", "", NONCLAIMS, "", EVAL_NONCLAIMS, ""]
    return "\n".join(parts)


def write_summary(run: Path) -> Path:
    p = Path(run) / "summary.md"
    p.write_text(summary(run), encoding="utf-8")
    return p
