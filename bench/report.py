"""Benchmark report (spec 2026-10-05 verify §3.6): fixed tables from `results.jsonl` + `env.json`
(+ `calibration.json` when bench/calibrate.py ran), with the non-claims text under every table group.

    uv run python bench/report.py --run bench/results/<run>/ [--readme-snippet PATH] [--link docs/benchmarks/x.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
NONCLAIMS = (HERE / "NONCLAIMS.md").read_text(encoding="utf-8").strip()
COLUMNS = ["auto", "auto-norepair", "docling", "mineru", "markitdown", "vlm"]
DASH = "—"


def _num(v, digits: int = 2) -> str:
    return DASH if v is None else f"{v:.{digits}f}"


def _agg(rows: list[dict], get: Callable[[dict], float | None], *, higher_better: bool = True,
         digits: int = 2) -> str:
    """Mean over samples (repeats averaged first) with the worst sample in parentheses."""
    per: dict[str, list[float]] = {}
    for r in rows:
        v = get(r)
        if v is not None:
            per.setdefault(r["sample"], []).append(float(v))
    vals = [sum(x) / len(x) for x in per.values()]
    if not vals:
        return DASH
    mean = sum(vals) / len(vals)
    if len(vals) == 1:
        return _num(mean, digits)
    worst = min(vals) if higher_better else max(vals)
    return f"{_num(mean, digits)} ({_num(worst, digits)})"


def _m(key: str):
    return lambda r: (r.get("metrics") or {}).get(key)


def _per_page(r: dict) -> float | None:
    return r["seconds"] / r["pages"] if r.get("pages") and r.get("seconds") is not None else None


def _engines(rows: list[dict]) -> str:
    names = sorted({r.get("engine_used") or "failed" for r in rows})
    return ", ".join(names) if names else DASH


def _vram(rows: list[dict]) -> str:
    vals = [r.get("vram_delta_mb") for r in rows if r.get("vram_delta_mb") is not None]
    return f"+{max(vals)} MB" if vals else DASH


def _verify_levels(rows: list[dict]) -> str:
    levels = sorted({(r.get("verify") or {}).get("level") or DASH for r in rows})
    return ", ".join(levels) if levels else DASH


# category: (label, gt note, predicate on sample name, metric header, cell builder)
def _cats() -> list[tuple]:
    def mk(*parts):
        def cell(rows):
            return " / ".join(p(rows) for p in parts)
        return cell
    ned = lambda rows: _agg(rows, _m("ned"))
    cer = lambda rows: _agg(rows, _m("cer"), higher_better=False)
    cov = lambda rows: _agg(rows, _m("coverage"))
    ali = lambda rows: _agg(rows, _m("alignment"))
    rec = lambda rows: _agg(rows, _m("recovery"))
    drop = lambda rows: _agg(rows, _m("dropped_rate"), higher_better=False)
    sec = lambda rows: _agg(rows, lambda r: r.get("seconds"), higher_better=False, digits=1)
    return [
        ("繁中數位", "L1", lambda n: n.startswith("syn-text-zh"), "NED ↑ / 覆蓋 / 對齊", mk(ned, cov, ali)),
        ("英文數位", "L1", lambda n: n.startswith("syn-text-en"), "NED ↑ / 覆蓋 / 對齊", mk(ned, cov, ali)),
        ("繁中掃描", "L1", lambda n: n.startswith("syn-scan-zh"), "NED ↑ / CER ↓", mk(ned, cer)),
        ("中英混排掃描", "L1", lambda n: n.startswith("syn-scan-mixed"), "NED ↑ / CER ↓", mk(ned, cer)),
        ("壞字型 10%", "L1+缺陷", lambda n: n.startswith(("syn-broken-tounicode-a", "syn-broken-tounicode-d")),
         "NED ↑ / 修復率 ↑ / 漏頁率 ↓", mk(ned, rec, drop)),
        ("壞字型 30%", "L1+缺陷", lambda n: n.startswith("syn-broken-tounicode-b"), "最終引擎 / NED ↑",
         mk(_engines, ned)),
        ("壞字型只在頁尾（對照）", "L1", lambda n: n.startswith("syn-broken-tounicode-c"), "NED ↑ / verify 等級",
         mk(ned, _verify_levels)),
        ("表格", "L1", lambda n: n.startswith("syn-tables"), "TEDS ↑ / TEDS-S ↑",
         mk(lambda rows: _agg(rows, _m("teds")), lambda rows: _agg(rows, _m("teds_s")))),
        ("公式", "L1", lambda n: n.startswith("syn-formula"), "公式 NED ↑",
         mk(lambda rows: _agg(rows, _m("formula_ned")))),
        ("雙欄", "L1", lambda n: n.startswith("syn-twocol"), "NED ↑ / 對齊", mk(ned, ali)),
        ("1200 頁", "L1", lambda n: n == "syn-book-1200", "NED ↑ / 覆蓋 / 秒 ↓ / 顯存增量", mk(ned, cov, sec, _vram)),
    ]


def _columns(rows: list[dict]) -> list[str]:
    present = {r["config"] for r in rows}
    return COLUMNS + sorted(c for c in present if c not in COLUMNS)


def _table(header: list[str], body: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def env_line(env: dict) -> str:
    gpu = env.get("gpu") or {}
    eng = env.get("engines") or {}
    vers = ", ".join(f"{k} {v.get('version') if isinstance(v, dict) else v}" for k, v in sorted(eng.items())
                     if (v.get("version") if isinstance(v, dict) else v))
    parts = [f"GPU {gpu.get('name', DASH)}" + (f"（驅動 {gpu['driver']}）" if gpu.get("driver") else ""),
             f"引擎 {vers or DASH}", f"aidoc {env.get('aidoc_commit') or DASH}", f"日期 {str(env.get('date', DASH))[:10]}"]
    if env.get("vlm"):
        v = env["vlm"]
        parts.append(f"VLM {v.get('model')} @ {v.get('endpoint_host')}（prompt v{v.get('prompt_version')}）")
    if env.get("fake"):
        parts.append("**假引擎（管線測試，數字無意義）**")
    return "環境：" + "；".join(parts)


def tier_a(rows: list[dict], *, compact: bool = False, small: bool = False) -> str:
    rows = [r for r in rows if r["tier"] == "A" and (r.get("corpus") == "A-small") == small]
    cols = _columns(rows)
    body = []
    for label, gtl, pred, metric, cell in _cats():
        sel = [r for r in rows if pred(r["sample"])]
        n = len({r["sample"] for r in sel})
        if not n:
            continue
        line = [f"{label} ×{n}（{gtl}）", metric]
        for c in cols:
            cr = [r for r in sel if r["config"] == c]
            line.append(cell(cr) if cr else DASH)
        body.append(line)
    allp = [r for r in rows]
    if allp:
        line = ["時間／頁（全部）", "秒 ↓"]
        for c in cols:
            cr = [r for r in allp if r["config"] == c]
            line.append(_agg(cr, _per_page, higher_better=False, digits=1) if cr else DASH)
        body.append(line)
    if compact:
        return _table(["類別（樣本數，正解等級）", "指標"] + cols, body)
    note = ("數字 = 該類別所有樣本的平均，括號內為最差樣本；— = 該設定沒有跑（1200 頁書只跑 auto 與 mineru）；"
            "轉換失敗以 NED 0 計入。")
    return _table(["類別（樣本數，正解等級）", "指標"] + cols, body) + "\n\n" + note


def tier_b(rows: list[dict]) -> str | None:
    rows = [r for r in rows if r["tier"] == "B"]
    if not rows:
        return None
    cols = _columns(rows)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        parts = r["sample"].split("-")
        key = "-".join(parts[:2]) if parts[0] in ("ey", "ly") else parts[0]
        groups.setdefault(key, []).append(r)
    labels = {"ey-gazette": "行政院公報（OGDL）", "ly-gazette": "立法院公報（OGDL）", "arxiv": "arXiv（CC BY 4.0）",
              "nist": "NIST（美國政府著作）"}
    body = []
    for key in sorted(groups):
        sel = groups[key]
        n = len({r["sample"] for r in sel})
        line = [f"{labels.get(key, key)} ×{n}（L3）", "verify 分數 ↑ / 覆蓋 / 秒每頁 ↓"]
        for c in cols:
            cr = [r for r in sel if r["config"] == c]
            line.append(" / ".join([_agg(cr, lambda r: (r.get("verify") or {}).get("score")),
                                    _agg(cr, _m("coverage")),
                                    _agg(cr, _per_page, higher_better=False, digits=1)]) if cr else DASH)
        body.append(line)
    return _table(["文件群（樣本數，正解等級）", "指標"] + cols, body) + \
        "\n\nL3 = 只有頁數等結構資訊，沒有逐字正解；分數是 `aidoc verify` 的結構檢查，不是文字正確率。"


def tier_c(rows: list[dict]) -> str | None:
    rows = [r for r in rows if r["tier"] == "C"]
    if not rows:
        return None
    body = []
    for r in sorted(rows, key=lambda x: (x["sample"], x["config"])):
        m, v = r.get("metrics") or {}, r.get("verify") or {}
        body.append([r["sample"], r["config"], str(r.get("pages") or DASH), r.get("engine_used") or DASH,
                     _num(m.get("coverage")), _num(m.get("alignment")), str(m.get("pages_repaired", DASH)),
                     str(m.get("pages_unrepaired", DASH)), f"{v.get('level') or DASH} {_num(v.get('score'))}",
                     _num(r.get("seconds"), 1)])
    return _table(["樣本", "設定", "頁數", "最終引擎", "覆蓋", "對齊", "修復頁", "未修復頁", "verify", "秒"], body)


def calibration(run: Path) -> str:
    p = run / "calibration.json"
    if not p.is_file():
        return "未執行（`bench/calibrate.py --run <run>`）。"
    c = json.loads(p.read_text(encoding="utf-8"))
    body = [[name, d.get("level") or DASH, str(d.get("exit_code")), str(d.get("exit_code_fail_on_warn")),
             ", ".join(map(str, d.get("flagged") or [])) or DASH, ", ".join(map(str, d.get("expected") or [])) or DASH]
            for name, d in sorted((c.get("defects") or {}).items()) if not name.startswith("_")]
    t = _table(["負樣本", "verify 等級", "exit", "exit（--fail-on warn）", "標出的頁", "應標出的頁"], body)
    gate = "通過" if c.get("passed") else "**未通過（不發表）**"
    return (t + f"\n\nSpearman ρ（verify 分數 vs 正解平均 NED，n={c.get('n')}）= {_num(c.get('rho'))}；"
            f"頁級 precision = {_num(c.get('precision'))}、recall = {_num(c.get('recall'))}"
            f"（門檻 ρ ≥ {c.get('min_rho')}、recall ≥ {c.get('min_recall')}）：{gate}。")


def load(run: Path) -> tuple[list[dict], dict]:
    rows = [json.loads(x) for x in (run / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    env = json.loads((run / "env.json").read_text(encoding="utf-8")) if (run / "env.json").is_file() else {}
    return rows, env


def summary(run: Path) -> str:
    rows, env = load(run)
    out = [f"# Doc4AI benchmark — {str(env.get('date', ''))[:10]}", "", env_line(env), ""]
    missing = env.get("missing") or []
    if missing:
        out += ["本次缺少的樣本（未計入）：" + "；".join(f"{m['sample']}（{m['tier']}：{m['why']}）" for m in missing), ""]
    failed = [r for r in rows if r["status"] == "failed"]
    if failed:
        out += [f"轉換失敗 {len(failed)} 筆（以 NED 0 計入）：" +
                "；".join(f"{r['config']}/{r['sample']}" for r in failed[:20]), ""]
    full = any(r["tier"] == "A" and r.get("corpus") != "A-small" for r in rows)
    out += ["## Tier A — 合成語料（可重現）", "", tier_a(rows, small=not full), "", NONCLAIMS, ""]
    if full and any(r["tier"] == "A" and r.get("corpus") == "A-small" for r in rows):
        out += ["## Tier A 小樣本（每類 1 份、3 頁；只在小樣本上跑的設定，例如 vlm）", "", tier_a(rows, small=True), "",
                NONCLAIMS, ""]
    out += ["## verify 校準", "", calibration(run), "", NONCLAIMS, ""]
    b = tier_b(rows)
    if b:
        out += ["## Tier B — 公開文件（可重現，依 MANIFEST 下載）", "", b, "", NONCLAIMS, ""]
    c = tier_c(rows)
    if c:
        out += ["## Tier C — 不可重現：樣本受著作權保護，未隨專案散佈", "", c, "", NONCLAIMS, ""]
    return "\n".join(out).rstrip() + "\n"


def readme_snippet(run: Path, link: str) -> str:
    rows, env = load(run)
    return "\n".join([env_line(env), "", tier_a(rows, compact=True), "",
                      f"完整結果（Tier B、verify 校準、不可重現的 Tier C）：[{link}]({link})", "", NONCLAIMS, ""])


def write_summary(run: Path) -> Path:
    p = Path(run) / "summary.md"
    p.write_text(summary(Path(run)), encoding="utf-8", newline="\n")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--readme-snippet")
    ap.add_argument("--link", default="docs/benchmarks/")
    a = ap.parse_args(argv)
    run = Path(a.run)
    write_summary(run)
    if a.readme_snippet:
        Path(a.readme_snippet).write_text(readme_snippet(run, a.link), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
