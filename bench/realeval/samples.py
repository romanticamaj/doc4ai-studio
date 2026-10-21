"""Sample registry (spec 2026-10-22 §2, §4.1–4.3): the manifest (identities only, no text), sha256 verification,
the "never tracked by git" guard and the image-only rendering of the two English controls.

The real folder is opened only by the registered run (spec §8.2). `fake=True` refuses outright; harness development
uses stand-in folders (`stand_in=True`, a manifest of their own, never the real folder)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bench.realeval.rules import CONTROL_RENDER_DPI

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = HERE / "MANIFEST.json"
REAL_ROOT = ROOT / "tests" / "fixtures" / "manual" / "realeval"


@dataclass
class RealSample:
    name: str
    group: str
    lang: str
    pdf: Path
    gt_dir: Path
    gt_pages: list[int]
    gt_level: int | str
    html: Path | None
    sha256: str
    ocr_text_layer: bool
    entry: dict = field(default_factory=dict)

    def gt_text(self, page: int) -> str:
        return (self.gt_dir / f"page-{page:03d}.txt").read_text(encoding="utf-8")

    def scoring_pdf(self) -> Path:
        """The PDF the engines and the VLM see: the original, or the image-only rendering for group C (§4.3)."""
        return self.pdf.parent / "derived" / "image_only.pdf" if self.group == "C" else self.pdf

    @property
    def formulas_json(self) -> Path | None:
        g = self.entry.get("gt")
        return self.pdf.parent / g if g else None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def guard_untracked(folder: Path) -> None:
    """The sample folder must be ignored by git and hold no tracked file (spec §2, §13 #3); else SystemExit."""
    folder = Path(folder)
    ign = subprocess.run(["git", "check-ignore", "-q", str(folder)], cwd=ROOT, capture_output=True, text=True,
                         check=False)
    if ign.returncode != 0:
        raise SystemExit(f"{folder} is not ignored by git: refusing to read real samples")
    ls = subprocess.run(["git", "ls-files", "--", str(folder)], cwd=ROOT, capture_output=True, text=True,
                        check=False)
    if ls.stdout.strip():
        raise SystemExit(f"{folder} has files tracked by git: refusing to read real samples")


def _inside(p: Path, parent: Path) -> bool:
    try:
        p.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load(root: Path, *, fake: bool = False, manifest: Path | None = None,
         stand_in: bool = False) -> tuple[list[RealSample], list[dict]]:
    """(samples, missing). A document whose sha256 differs or whose GT files are absent goes to `missing`."""
    if fake and not stand_in:
        raise SystemExit("real samples are never loaded in --fake mode (pass --stand-in <dir>)")
    root = Path(root)
    if stand_in:
        if _inside(root, REAL_ROOT) or manifest is None or Path(manifest).resolve() == MANIFEST.resolve():
            raise SystemExit("stand-in mode needs its own folder and manifest, never the real samples")
    else:
        guard_untracked(root)
    entries = json.loads(Path(manifest or MANIFEST).read_text(encoding="utf-8"))
    out: list[RealSample] = []
    missing: list[dict] = []
    for e in entries:
        d = root / e["name"]
        pdf = d / e["file"]
        if not pdf.is_file():
            missing.append({"name": e["name"], "why": f"{e['file']} not found"})
            continue
        got = sha256_file(pdf)
        if got != e["sha256"]:
            missing.append({"name": e["name"], "why": f"sha256 mismatch ({got[:12]}…)"})
            continue
        gt_pages = list(e.get("gt_pages") or [])
        if e["group"] == "B":
            need = [d / e["gt"], d / e["html"]]
        else:
            need = [d / "gt" / f"page-{p:03d}.txt" for p in gt_pages]
        absent = [str(p.relative_to(d)) for p in need if not p.is_file()]
        if absent:
            missing.append({"name": e["name"], "why": "GT missing: " + ", ".join(absent[:5])})
            continue
        out.append(RealSample(e["name"], e["group"], e["lang"], pdf, d / "gt", gt_pages, e["gt_level"],
                              d / e["html"] if e.get("html") else None, e["sha256"], bool(e["ocr_text_layer"]), e))
    return out, missing


def render_image_only(src: Path, dst: Path, dpi: int = CONTROL_RENDER_DPI) -> dict:
    """Each page rendered to PNG and placed on a page of the same size (as make_fixtures.make_scanned_pdf), so the
    OCR text layer cannot leak the answer (§4.3). {"sha256", "pages", "text_chars"}; text_chars must be 0."""
    import pymupdf
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(src) as a, pymupdf.open() as b:
        for page in a:
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            np = b.new_page(width=page.rect.width, height=page.rect.height)
            np.insert_image(np.rect, stream=png)
        b.save(dst, garbage=4, deflate=True)
    with pymupdf.open(dst) as c:
        pages = c.page_count
        chars = sum(len(p.get_text().strip()) for p in c)
    if chars != 0:
        raise AssertionError(f"{dst}: {chars} characters left in the text layer")
    return {"sha256": sha256_file(dst), "pages": pages, "text_chars": chars}
