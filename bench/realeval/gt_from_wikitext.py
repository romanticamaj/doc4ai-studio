"""Rebuild the A/C ground truth (`gt/page-NNN.txt`) from the Wikisource wikitext in `gt_raw/` — reproducibly, and
failing loudly on anything it does not know (spec 2026-10-22 §4.4 / §6.1 preprocessing; verifier Task 9 finding:
the original extractor dropped or garbled template content).

    uv run python bench/realeval/gt_from_wikitext.py --fetch          # once: user templates / foreign Page: pages
    uv run python bench/realeval/gt_from_wikitext.py --rebuild        # gt_raw → gt (old gt kept once as gt_v1/)
    uv run python bench/realeval/gt_from_wikitext.py --rebase-errata  # gt_errata.json against the rebuilt GT

Policy (what a reader of the printed page sees, body only):
- page furniture inside <noinclude> (running headers, page numbers, catchwords, table header rows) is not GT, as
  in the original extraction; only wrapper openings / closings that span into the body are kept;
- printed-text templates keep their text: 專 / 書 / 多行合一 / lsp (after the spacing) keep every positional
  argument; UnO / sic / SIC / 校 keep the first (the printed form); formatting wrappers keep their content;
- notes and spacing are dropped: <ref>, {{*|…}} (transcriber's note), {{?}}, gap / Gap, redact, ts (table style);
- links show their display text; {{Page:…}} transclusions and user templates are expanded from local copies
  (gt_raw/page-NNN.wikitext of the same index, else gt_raw/_external/ or gt_raw/_templates/, filled by --fetch);
- any other template, or markup left over, raises.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from bench.realeval.normalize import norm_gt
from bench.realeval.samples import MANIFEST, REAL_ROOT

NEAR_EMPTY = 20                       # = bench.metrics.DROPPED_SECTION_MAX: a scored page needs real text
PIPE = "\x00"                         # {{!}} until the end of template expansion
T_OPEN, T_CLOSE = "\x01", "\x02"      # table {| and |} so they never meet the brace matcher
UA = "Doc4AI-realeval-GT/1.0 (romanticamaj@gmail.com; one-off ground-truth rebuild)"
API = "https://zh.wikisource.org/w/api.php"


class UnknownTemplate(ValueError):
    pass


JOIN = {"專", "書", "多行合一", "3"}                                     # printed text, every positional argument
FIRST = {"uno", "sic", "校"}                                           # the printed form
WRAP = {"c", "center", "right", "big", "larger", "x-larger", "xxl", "xxxl", "sm", "hi", "sp", "l", "pre2",
        "indent pars", "++", "di"}
DROP = {"gap", "redact", "*", "?", "nop", "nopt", "dhr", "rule", "bar", "ts", "runningheader", "continues"}
LITERAL = {"ls": "ſ", "ls+t": "ſt", "ss": "ſſ"}                         # long s (GT encodes U+017F; NFKC → s)
_NAMED = re.compile(r"^\s*([A-Za-z0-9_\- ]{1,30})\s*=")


def _key(name: str) -> str:
    return re.sub(r"[\s_]+", " ", name.strip()).lower()


def _split(body: str) -> list[str]:
    return body.split("|")


def _positional(args: list[str], *, any_name: bool = False) -> tuple[list[str], dict[str, str]]:
    pos, named = [], {}
    for a in args:
        m = re.match(r"^\s*([^=\n]{1,30}?)\s*=", a) if any_name else _NAMED.match(a)
        if m:
            named[m.group(1).strip()] = a[m.end():].strip()
        else:
            pos.append(a)
    return pos, named


def _ppoem(text: str) -> str:
    return "\n".join(re.sub(r"^\s*(?:<>|>|:)+", "", ln) for ln in text.split("\n"))


def _subst_params(text: str, params: dict[str, str]) -> str:
    prev = None
    while prev != text:
        prev = text

        def one(m):
            name, _, default = m.group(1).partition("|")
            return params.get(name.strip(), default if "|" in m.group(1) else "")
        text = re.sub(r"\{\{\{([^{}]*)\}\}\}", one, text)
    return text


# ---------------------------------------------------------------- page structure

_TPL_INNER = re.compile(r"\{\{(?!\{)([^{}]*)\}\}")


def _prepare(s: str) -> str:
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)
    s = re.sub(r"<pagequality[^>]*/?>", "", s)
    s = re.sub(r"(?m)^\s*\{\|[^<\n]*", T_OPEN, s)          # attributes only: never past a tag on the line
    s = re.sub(r"(?m)^\s*\|\}", T_CLOSE, s)
    return s


def _furniture(region: str) -> str:
    """A <noinclude> region: complete templates and text go; unbalanced openers / closers and table markers stay."""
    prev = None
    while prev != region:
        prev, region = region, _TPL_INNER.sub("", region)
    keep = re.findall(r"\{\{[^{}|]*\||\}\}|" + T_OPEN + "|" + T_CLOSE, region)
    return "".join(keep)


def _strip_noinclude(s: str) -> str:
    out, i = [], 0
    for m in re.finditer(r"<noinclude>(.*?)(?:</noinclude>|$)", s, flags=re.DOTALL):
        out.append(s[i:m.start()])
        out.append(_furniture(m.group(1)))
        i = m.end()
    out.append(s[i:])
    return "".join(out)


def _links(s: str) -> str:
    def one(m):
        target, _, label = m.group(1).partition("|")
        if label:
            return label
        return re.sub(r"^(?:w|wikt|zh|en):", "", target.strip())
    prev = None
    while prev != s:
        prev, s = s, re.sub(r"\[\[([^\[\]{}]*)\]\]", one, s)
    return s


def _tags(s: str) -> str:
    s = re.sub(r"<ref[^>/]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.DOTALL)
    s = re.sub(r"<references[^>]*/?>", "", s)
    s = re.sub(r"<section[^>]*/?>", "", s)
    s = re.sub(r"__[A-Z]+__", "", s)
    return s


def _table_and_html(s: str) -> str:
    out = []
    for ln in s.split("\n"):
        t = ln.strip()
        if t in (T_OPEN, T_CLOSE) or t.startswith("|-"):
            continue
        t = t.replace(T_OPEN, "").replace(T_CLOSE, "")
        if t[:1] in ("|", "!"):
            t = t[2:] if t.startswith("|+") else t[1:]
            cells = re.split(r"\|\||!!", t)
            t = " ".join(c.split("|", 1)[1] if "|" in c and "=" in c.split("|", 1)[0] else c for c in cells)
        out.append(t)
    s = "\n".join(out)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(?:div|span|u|small|big|pre|center|sup|sub|poem|font|b|i|s)\b[^>]*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"'{2,}", "", s)
    return s


# ---------------------------------------------------------------- templates

def _expand_one(body: str, resolve) -> str:
    name, sep, rest = body.partition("|")
    args = _split(rest) if sep else []
    raw_name = name.strip()
    if raw_name.startswith("#if:"):
        cond = raw_name[4:].strip()
        return (args[0] if args else "") if cond else (args[1] if len(args) > 1 else "")
    if raw_name == "!":
        return PIPE
    k = _key(raw_name)
    if k in LITERAL:
        return LITERAL[k]
    if k in DROP:
        return ""
    pos, _named = _positional(args)
    if k in JOIN:
        return "".join(pos)
    if k == "lsp":
        return "".join(pos[1:])
    if k in FIRST:
        return pos[0] if pos else ""
    if k in WRAP:
        return "\n".join(pos)
    if k == "ppoem":
        return _ppoem("\n".join(pos))
    if raw_name.startswith(("Page:", "User:")):
        kind = "page" if raw_name.startswith("Page:") else "template"
        text = resolve(kind, raw_name) if resolve else None
        if text is None:
            raise UnknownTemplate(f"{raw_name}: no local copy (run --fetch)")
        pos, named = _positional(args, any_name=True)
        params = {str(i + 1): v for i, v in enumerate(pos)} | named
        text = _subst_params(_prepare(text), params)
        return _links(_tags(_strip_noinclude(text) if kind == "page" else text))
    raise UnknownTemplate(f"unknown template {{{{{raw_name}}}}}")


def render(wikitext: str, *, resolve=None) -> str:
    """One Wikisource page (header / body / footer) → plain GT text."""
    s = _links(_tags(_strip_noinclude(_prepare(wikitext))))
    for _ in range(10000):
        nxt = _subst_params(s, {})
        nxt = _TPL_INNER.sub(lambda m: _expand_one(m.group(1), resolve), nxt, count=1)
        nxt = _links(nxt)
        if nxt == s:
            break
        s = nxt
    s = _table_and_html(s.replace(PIPE, "|"))
    left = [t for t in ("{{", "}}", "[[", "]]", "<ref") if t in s]
    if left:
        raise UnknownTemplate(f"markup left after conversion: {left}")
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip() + "\n"


def check_text(text: str) -> list[str]:
    """Problems of a scored GT page (shared validity check): near-empty, or markup left in it."""
    out = []
    if len(norm_gt(text)) < NEAR_EMPTY:
        out.append("near_empty")
    if any(t in text for t in ("{{", "}}", "[[", "<ref")):
        out.append("markup")
    return out


# ---------------------------------------------------------------- local copies and the network

def _safe(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title)


def make_resolver(doc_dir: Path):
    meta = doc_dir / "_meta.json"
    index = json.loads(meta.read_text(encoding="utf-8")).get("file") if meta.is_file() else None

    def resolve(kind: str, title: str) -> str | None:
        if kind == "page" and index:
            m = re.fullmatch(r"Page:(.+)/(\d+)", title)
            if m and m.group(1) == index:
                p = doc_dir / "gt_raw" / f"page-{int(m.group(2)):03d}.wikitext"
                return p.read_text(encoding="utf-8") if p.is_file() else None
        p = doc_dir / "gt_raw" / ("_external" if kind == "page" else "_templates") / f"{_safe(title)}.wikitext"
        return p.read_text(encoding="utf-8") if p.is_file() else None
    return resolve


def needed_titles(doc_dir: Path) -> list[tuple[str, str]]:
    found = []
    for f in sorted((doc_dir / "gt_raw").glob("page-*.wikitext")):
        for m in re.finditer(r"\{\{\s*((?:Page|User):[^|{}]+?)\s*[|}]", f.read_text(encoding="utf-8")):
            t = m.group(1).strip()
            kind = "page" if t.startswith("Page:") else "template"
            if (kind, t) not in found and make_resolver(doc_dir)(kind, t) is None:
                found.append((kind, t))
    return found


def fetch(doc_dir: Path, titles: list[tuple[str, str]], *, sleep_s: float = 1.0, opener=None) -> list[dict]:
    """Polite MediaWiki API reads (User-Agent with contact, maxlag, one title per second, redirects followed);
    each copy is stored with its revision id next to the raw pages. Public wikitext only; no page image or
    document content is sent anywhere."""
    import urllib.parse
    import urllib.request
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler())
    done = []
    for kind, title in titles:
        q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content|ids|timestamp",
                                    "rvslots": "main", "titles": title, "redirects": "1", "format": "json",
                                    "formatversion": "2", "maxlag": "5"})
        req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
        data = json.loads(opener.open(req, timeout=30).read().decode("utf-8"))
        page = data["query"]["pages"][0]
        rev = (page.get("revisions") or [{}])[0]
        text = (rev.get("slots") or {}).get("main", {}).get("content")
        if page.get("missing") or text is None:
            raise UnknownTemplate(f"{title}: not found on Wikisource")
        d = doc_dir / "gt_raw" / ("_external" if kind == "page" else "_templates")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{_safe(title)}.wikitext").write_text(text, encoding="utf-8")
        info = {"title": title, "resolved": page["title"], "revid": rev.get("revid"),
                "timestamp": rev.get("timestamp"), "fetched": time.strftime("%Y-%m-%dT%H:%M:%S")}
        (d / f"{_safe(title)}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        done.append(info)
        time.sleep(sleep_s)
    return done


# ---------------------------------------------------------------- rebuild and errata

def rebuild_doc(doc_dir: Path, pages: list[int]) -> list[dict]:
    gt, old = doc_dir / "gt", doc_dir / "gt_v1"
    if not old.exists():
        shutil.copytree(gt, old)
    resolve = make_resolver(doc_dir)
    rep = []
    for p in pages:
        raw = doc_dir / "gt_raw" / f"page-{p:03d}.wikitext"
        if not raw.is_file():
            raise UnknownTemplate(f"{doc_dir.name}: {raw.name} missing")
        try:
            text = render(raw.read_text(encoding="utf-8"), resolve=resolve)
        except UnknownTemplate as e:
            raise UnknownTemplate(f"{doc_dir.name} p{p}: {e}") from e
        prev = old / f"page-{p:03d}.txt"
        before = len(norm_gt(prev.read_text(encoding="utf-8"))) if prev.is_file() else None
        (gt / f"page-{p:03d}.txt").write_text(text, encoding="utf-8")
        rep.append({"page": p, "before": before, "after": len(norm_gt(text)), "problems": check_text(text)})
    return rep


TRANSCRIBED = {("zh-roc1912-gazette02", 17), ("zh-ssid-zhuyin-primer", 11)}   # coordinator policy 2026-10-22


def rebase_errata(errata: list[dict], gt: dict[str, dict[int, str]], *, transcribed=TRANSCRIBED) -> list[dict]:
    """Each erratum against the rebuilt GT: `kept` (its `wrong` occurs exactly once), `superseded` (the rebuild
    already prints `right`), `replace_page` (the verifier's transcription from the scan wins over the rebuild) or
    `unresolved` (needs the verifier)."""
    out = []
    for e in errata:
        page = gt.get(e["doc"], {}).get(int(e["page"]), "")
        if (e["doc"], int(e["page"])) in transcribed:
            out.append({"status": "replace_page",
                        "erratum": {**e, "wrong": "", "replace_page": True,
                                    "source": e.get("source", "") + "; whole page (takes precedence over the rebuild)"}})
        elif page.count(e["wrong"]) == 1:
            out.append({"status": "kept", "erratum": e})
        elif e["right"] in page and e["wrong"] not in page:
            out.append({"status": "superseded", "erratum": e})
        elif (w := _realign(page, e["right"])) is not None:
            out.append({"status": "realigned",
                        "erratum": {**e, "wrong": w, "source": e.get("source", "") + "; realigned to the rebuilt GT"}})
        else:
            out.append({"status": "unresolved", "erratum": e})
    return out


REALIGN_MAX = 2


def _realign(page: str, right: str) -> str | None:
    """The one window of `page` within edit distance REALIGN_MAX (> 0) of the verifier's scan reading `right`."""
    from rapidfuzz.distance import Levenshtein
    hits = set()
    for n in range(max(1, len(right) - REALIGN_MAX), len(right) + REALIGN_MAX + 1):
        for i in range(len(page) - n + 1):
            w = page[i:i + n]
            if 0 < Levenshtein.distance(w, right) <= REALIGN_MAX:
                hits.add((Levenshtein.distance(w, right), i, w))
    if not hits:
        return None
    best = min(d for d, _, _ in hits)
    top = {w for d, _, w in hits if d == best}
    if len(top) != 1:
        return None
    w = top.pop()
    return w if page.count(w) == 1 else None


# ---------------------------------------------------------------- CLI

def _docs(manifest: Path) -> list[dict]:
    return [e for e in json.loads(manifest.read_text(encoding="utf-8")) if e["group"] in ("A", "C")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--rebuild", action="store_true")
    g.add_argument("--rebase-errata", action="store_true")
    ap.add_argument("--root", default=str(REAL_ROOT))
    ap.add_argument("--manifest", default=str(MANIFEST))
    a = ap.parse_args(argv)
    root = Path(a.root)
    docs = _docs(Path(a.manifest))
    if a.fetch:
        for e in docs:
            need = needed_titles(root / e["name"])
            for info in fetch(root / e["name"], need):
                print(f"{e['name']}: fetched {info['title']} -> {info['resolved']} rev {info['revid']}")
        return 0
    if a.rebuild:
        report = {e["name"]: rebuild_doc(root / e["name"], e["gt_pages"]) for e in docs}
        (root / "gt_rebuild_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        for name, rows in report.items():
            b = sum(r["before"] or 0 for r in rows)
            n = sum(r["after"] for r in rows)
            bad = [f"p{r['page']}:{','.join(r['problems'])}" for r in rows if r["problems"]]
            print(f"{name}: {len(rows)} pages, chars {b} -> {n}; problems {bad or 'none'}")
        return 0
    path = root / "gt_errata.json"
    errata = json.loads(path.read_text(encoding="utf-8"))
    gt = {e["name"]: {p: (root / e["name"] / "gt" / f"page-{p:03d}.txt").read_text(encoding="utf-8")
                      for p in e["gt_pages"]} for e in docs}
    res = rebase_errata(errata, gt)
    for x in res:
        e = x["erratum"]
        print(f"{x['status']:>12}  {e['doc']} p{e['page']}  ({len(e['wrong'])} -> {len(e['right'])} chars)")
    if any(x["status"] == "unresolved" for x in res):
        print("unresolved errata: nothing written; the verifier decides")
        return 1
    v1 = root / "gt_errata.v1.json"
    if not v1.exists():
        shutil.copy2(path, v1)
    path.write_text(json.dumps([x["erratum"] for x in res if x["status"] != "superseded"], ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
