"""Pinned inputs of the synthetic corpus (spec 2026-10-05 verify §3.2 Tier A): public-domain Project Gutenberg texts
and the SIL OFL Noto Sans CJK TC font. Downloaded once into `bench/corpus/.cache/` (gitignored) and verified by
sha256; never a fallback to a system font or another text."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
SOURCES = json.loads((HERE / "SOURCES.json").read_text(encoding="utf-8"))


class SourceError(RuntimeError):
    pass


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(urls: list[str], dest: Path, sha256: str, *, attempts: int = 4, log=print) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for url in urls:
        for k in range(attempts):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "doc4ai-bench/1 (+reproducible corpus)"})
                with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                got = _sha256(tmp)
                if got != sha256:
                    raise SourceError(f"sha256 mismatch for {url}: {got} != {sha256}")
                tmp.replace(dest)
                return
            except SourceError:
                tmp.unlink(missing_ok=True)
                raise
            except OSError as e:                       # Gutenberg answers 503/504 now and then: retry, then mirror
                last = e
                log(f"download {url}: {type(e).__name__}: {e}; retry {k + 1}/{attempts}")
                time.sleep(2 * (k + 1))
    tmp.unlink(missing_ok=True)
    raise SourceError(f"could not download {dest.name}: {last}")


def ensure(key: str, *, offline: bool = False, cache: Path = CACHE, log=print) -> Path:
    """Local path of a pinned source (text or font), downloading it when missing."""
    s = SOURCES[key]
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / s["file"]
    if dest.is_file() and _sha256(dest) == s["sha256"]:
        return dest
    if offline:
        raise SourceError(f"{s['file']} is not cached and --offline was given (needs {s['urls'][0]})")
    log(f"downloading {s['file']} ({s['license']})")
    _download(s["urls"], dest, s["sha256"], log=log)
    return dest


def ensure_noto(*, offline: bool = False, cache: Path = CACHE, log=print) -> dict[str, Path]:
    return {"sans": ensure("noto-sans-cjk-tc", offline=offline, cache=cache, log=log)}


def text_body(key: str, *, offline: bool = False, cache: Path = CACHE) -> str:
    """The book text between the Project Gutenberg START and END lines (header/footer and licence stripped),
    newlines normalised, producer credits removed."""
    raw = ensure(key, offline=offline, cache=cache).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines = raw.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith("*** START OF"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("*** END OF"))
    body = [ln for ln in lines[start + 1:end] if not ln.strip().lower().startswith("produced by")]
    return "\n".join(body).strip("\n")
