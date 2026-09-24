"""Library / Document endpoints (index §8, spec §6 pages 3-4, §8.5 orphaned)."""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from aidoc import fsops
from aidoc.output import read_sidecar, sidecar_path
from aidoc.server.auth import ApiError
from aidoc.server.serialize import serialize_document

router = APIRouter()


def _doc(request: Request, doc_id: str) -> dict:
    doc = request.app.state.ctx.store.get_document(doc_id)
    if doc is None:
        raise ApiError(404, "not_found")
    return doc


def source_file(doc: dict) -> Path | None:
    """The staged work copy (src.*) while retained, else the original when it is a local path that still exists."""
    wc = doc.get("work_copy_path")
    if wc:
        w = Path(wc)
        if w.is_file():
            return w
        if w.is_dir():
            for f in sorted(w.glob("src.*")):
                if f.is_file():
                    return f
    src = Path(doc["source_path"])
    if src.is_absolute() and src.is_file():            # upload display names are never paths
        return src
    return None


def output_available(doc: dict) -> bool:
    out = Path(doc["output_dir"])
    return sidecar_path(out).is_file() and (out / f"{out.name}.md").is_file()


@router.get("/documents")
def list_documents(request: Request, status: str | None = None, engine: str | None = None,
                   q: str | None = None) -> dict:
    store = request.app.state.ctx.store
    docs = store.list_documents(status=status or None, engine=engine or None, q=q or None)
    return {"documents": [serialize_document(d) for d in docs]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, request: Request) -> dict:
    doc = _doc(request, doc_id)
    return {"document": serialize_document(doc), "source_available": source_file(doc) is not None,
            "output_available": output_available(doc), "sidecar": read_sidecar(Path(doc["output_dir"]))}


@router.get("/documents/{doc_id}/markdown")
def get_markdown(doc_id: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    out = Path(doc["output_dir"])
    md = out / f"{out.name}.md"
    if not md.is_file():
        raise ApiError(410, "output_missing")
    return Response(md.read_bytes(), media_type="text/markdown; charset=utf-8")


@router.get("/documents/{doc_id}/assets/{path:path}")
def get_asset(doc_id: str, path: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    base = (Path(doc["output_dir"]) / "assets").resolve()
    rel = path.replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel or "\x00" in rel or any(part in ("..", "") for part in rel.split("/")):
        raise ApiError(400, "bad_path")
    target = (base / rel).resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise ApiError(400, "bad_path")
    if not target.is_file():
        raise ApiError(404, "not_found")
    return FileResponse(target)


@router.get("/documents/{doc_id}/source")
def get_source(doc_id: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    f = source_file(doc)
    if f is None:
        raise ApiError(410, "source_missing")
    name = Path(doc["source_path"]).name or f.name
    return FileResponse(f, filename=name, content_disposition_type="inline")


@router.get("/documents/{doc_id}/download.zip")
def download_zip(doc_id: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    out = Path(doc["output_dir"])
    if not out.is_dir():
        raise ApiError(410, "output_missing")
    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)   # noqa: SIM115  closed by the iterator
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _plain_files(out):
            z.write(f, f.relative_to(out).as_posix())
    spool.seek(0)

    def chunks():
        try:
            while block := spool.read(1024 * 1024):
                yield block
        finally:
            spool.close()
    return StreamingResponse(chunks(), media_type="application/zip",
                             headers={"Content-Disposition": content_disposition(f"{out.name}.zip")})


def content_disposition(filename: str, kind: str = "attachment") -> str:
    """RFC 6266 / 5987: an ASCII fallback plus `filename*` in UTF-8 (a raw non-Latin-1 name made a 500)."""
    fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename) or "download"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _plain_files(root: Path) -> list[Path]:
    """Regular files under `root`, never following symlinks or junctions out of it (P3 verifier #6)."""
    base = root.resolve()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if not _is_link(d / n))
        for name in sorted(filenames):
            f = d / name
            if _is_link(f) or not f.is_file():
                continue
            if os.path.commonpath([str(base), str(f.resolve())]) != str(base):
                continue
            out.append(f)
    return out


def _is_link(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        is_junction = getattr(os.path, "isjunction", None)
        return bool(is_junction and is_junction(p))
    except OSError:
        return True


@router.delete("/documents/orphaned")
def delete_orphaned(request: Request) -> dict:
    store = request.app.state.ctx.store
    for doc in store.list_documents(status="orphaned"):
        wc = doc.get("work_copy_path")
        if wc:
            try:
                fsops.remove_tree(Path(wc))
            except OSError:
                pass
    return {"deleted": store.delete_documents("orphaned")}


@router.post("/documents/rescan")
def rescan(request: Request) -> dict:
    """Mark documents whose output (sidecar) is gone as orphaned (index A17); restore ones whose output is back."""
    ctx = request.app.state.ctx
    store = ctx.store
    n = 0
    for doc in store.list_documents():
        present = sidecar_path(Path(doc["output_dir"])).is_file()
        if doc["status"] in ("ok", "low") and not present:
            store.set_document_status(doc["id"], "orphaned")
            n += 1
        elif doc["status"] == "orphaned" and present:
            level = (doc.get("quality") or {}).get("level")
            store.set_document_status(doc["id"], level if level in ("ok", "low") else "ok")
    ctx.bus.publish("system.updated", None, {"documents_rescanned": True, "orphaned": n})
    return {"orphaned": n}
