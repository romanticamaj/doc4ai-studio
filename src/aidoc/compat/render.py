"""docling-serve response shapes and Markdown assembly (compat spec §4.4, plan D6–D8, spike S2).

`md_content` is the document's `<stem>.md`. With `md_page_break_placeholder` the placeholder goes **between** pages
(none before page 1), a page without a marker becomes an empty segment so the k-th segment is always page k+1, and
the `<!-- page: N -->` markers are removed unless `doc4ai_keep_page_markers`. Without a placeholder the markers stay.
Enum values follow docling's own models (spike S2), so docling clients that validate responses accept ours."""
from __future__ import annotations

import base64
import mimetypes
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from aidoc.compat.options import CompatOptions
from aidoc.mcp import docs
from aidoc.mcp.errors import ToolFailure
from aidoc.pagemap import split_pages
from aidoc.quality import strip_markup

MIB = 1024 * 1024
IMAGE_PLACEHOLDER = "<!-- image -->"
STATUS = Literal["success", "partial_success", "failure", "pending", "started", "skipped"]
COMPONENT = Literal["document_backend", "model", "doc_assembler", "user_input", "pipeline"]
CATEGORY = Literal["policy", "capacity", "source_unavailable", "target_unavailable", "timeout", "internal",
                   "backend_failure", "inference_failure", "unknown"]
PHASE = Literal["admission", "source_enumeration", "execution", "orchestration"]
_EMBED_TOO_BIG = "embedded images exceed embed_images_max_mb; remaining images are placeholders"


class ErrorItem(BaseModel):
    component_type: COMPONENT = "pipeline"
    module_name: str = "doc4ai.pipeline"
    error_message: str
    category: CATEGORY = "unknown"
    page_no: int | None = None


class ExportDocument(BaseModel):
    filename: str
    md_content: str | None = None
    json_content: dict | None = None
    html_content: str | None = None
    text_content: str | None = None
    doctags_content: str | None = None
    doclang_content: str | None = None
    doc4ai: dict | None = None


class ConvertDocumentResponse(BaseModel):
    document: ExportDocument
    status: STATUS
    errors: list[ErrorItem] = Field(default_factory=list)
    processing_time: float
    timings: dict = Field(default_factory=dict)
    doc4ai: dict | None = None


class PublicFailureInfo(BaseModel):
    category: CATEGORY
    message: str
    retryable: bool = False
    phase: PHASE = "execution"
    details: dict[str, str] = Field(default_factory=dict)


class TaskStatusResponse(BaseModel):
    task_id: str
    task_type: Literal["convert", "chunk"] = "convert"
    task_status: STATUS
    task_position: int | None = None
    task_meta: dict | None = None
    error_message: str | None = None
    failure: PublicFailureInfo | None = None


class TaskFailureResult(BaseModel):
    kind: Literal["TaskFailureResult"] = "TaskFailureResult"
    failure: PublicFailureInfo


# error kind (the text before ":") -> (component_type, category)
_KINDS: dict[str, tuple[str, str]] = {
    "unsupported_file": ("user_input", "policy"), "input": ("user_input", "policy"),
    "invalid_base64": ("user_input", "policy"), "file_too_large": ("user_input", "policy"),
    "engine": ("pipeline", "backend_failure"), "timeout": ("pipeline", "timeout"),
    "cancelled": ("pipeline", "internal"), "output_missing": ("pipeline", "internal"),
}


def error_kind(message: str) -> str:
    head = message.split(":", 1)[0].strip()
    return head if re.fullmatch(r"[a-z_]{1,40}", head) else "pipeline"


def error_item(message: str) -> ErrorItem:
    kind = error_kind(message)
    component, category = _KINDS.get(kind, ("pipeline", "unknown"))
    return ErrorItem(component_type=component, module_name=f"doc4ai.{kind}", error_message=message, category=category)


def failure_info(message: str, task_row: dict | None = None) -> PublicFailureInfo:
    kind = error_kind(message)
    tried = [a.get("engine") for a in (task_row or {}).get("tried") or [] if isinstance(a, dict)]
    return PublicFailureInfo(category=_KINDS.get(kind, ("pipeline", "unknown"))[1], message=message,
                             retryable=kind in ("timeout", "cancelled"),
                             details={"error_kind": kind, "tried": ",".join(t for t in tried if t)})


# ---- Markdown

def _images(md: str, opts: CompatOptions, *, doc_id: str, asset_base: str | None, assets_dir: Path | None,
            cfg) -> tuple[str, list[str]]:
    refs = docs.asset_refs(md)
    if not refs:
        return md, []
    warnings: list[str] = []
    budget = (cfg.compat.embed_images_max_mb * MIB) if cfg is not None else 20 * MIB
    used = 0
    over = False
    out, last = [], 0
    for (a, b), (ua, ub), name in refs:
        if a < last:                                    # overlapping constructs (an <img> inside alt text): skip
            continue
        out.append(md[last:a])
        if opts.image_mode == "referenced" and asset_base:
            out.append(md[a:ua] + f"{asset_base}/v1/doc4ai/documents/{doc_id}/assets/{name}" + md[ub:b])
        elif opts.image_mode == "embedded" and assets_dir is not None and not over:
            path = Path(assets_dir) / name
            try:
                data = path.read_bytes()
            except OSError:
                warnings.append(f"image {name} is missing from the output; left as a placeholder")
                out.append(IMAGE_PLACEHOLDER)
                last = b
                continue
            if used + len(data) > budget:
                over = True
                warnings.append(_EMBED_TOO_BIG)
                out.append(IMAGE_PLACEHOLDER)
            else:
                used += len(data)
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                out.append(md[a:ua] + f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}" + md[ub:b])
        else:
            out.append(IMAGE_PLACEHOLDER)
        last = b
    out.append(md[last:])
    return "".join(out), warnings


def assemble_markdown(md: str, pages: int | None, opts: CompatOptions, *, doc_id: str, asset_base: str | None,
                      assets_dir: Path | None, cfg) -> tuple[str, list[str]]:
    md, warnings = _images(md, opts, doc_id=doc_id, asset_base=asset_base, assets_dir=assets_dir, cfg=cfg)
    if not opts.placeholder:
        return md, warnings
    pre, sections = split_pages(md)
    if not pages or not sections:                       # page-less (docx ...): the whole text, no placeholder
        return md, warnings
    n = max(pages, max(sections))                       # a marker past documents.pages still gets its segment
    units = []
    for p in range(1, n + 1):
        text = sections.get(p, "")                      # a page without a marker: an empty segment keeps the count
        text = text.removeprefix("\n")
        units.append(f"<!-- page: {p} -->\n{text}" if opts.keep_page_markers else text)
    if pre.strip():
        units[0] = pre + units[0]                       # the text before the first marker belongs to page 1
    return opts.placeholder.join(units), warnings


def text_from_markdown(md: str) -> str:
    s = strip_markup(md)
    return re.sub(r"\n{3,}", "\n\n", s).strip(" \t\r\n")


def assemble_text(md_out: str, opts: CompatOptions) -> str:
    """text_content from the assembled md_content: the same segments, each stripped of Markdown."""
    if opts.placeholder and opts.placeholder in md_out:
        return opts.placeholder.join(text_from_markdown(u) for u in md_out.split(opts.placeholder))
    return text_from_markdown(md_out)


# ---- doc4ai block and responses

def asset_base_url(request, ctx) -> str:
    """Where referenced images are served: the request's own scheme and Host (Spec 4's public_url will win here)."""
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port}"
    return f"{request.url.scheme}://{host}"


def doc4ai_block(doc_row: dict, task_row: dict | None, opts: CompatOptions, *, cached: bool, attached: bool,
                 elapsed_s: float, warnings: list[str]) -> dict:
    q = doc_row.get("quality") or {}
    pm = q.get("page_map") or None
    tried = [a.get("engine") for a in (task_row or {}).get("tried") or [] if isinstance(a, dict)]
    return {
        "doc_id": doc_row["id"], "job_id": task_row["job_id"] if task_row else None, "engine": doc_row.get("engine"),
        "tried": tried or ([doc_row["engine"]] if doc_row.get("engine") else []), "lang": doc_row.get("lang"),
        "quality": {"level": q.get("level") or doc_row.get("status"), "score": q.get("score"),
                    "reasons": list(q.get("reasons") or [])},
        "pages": doc_row.get("pages"),
        "page_map": {"coverage": pm.get("coverage"), "method": pm.get("method")} if isinstance(pm, dict) else None,
        "page_warnings": [{"page": w.page, "reason": w.reason, "repaired": w.repaired} for w in docs.page_warnings(doc_row)],
        "untrusted_pages": list((q.get("untrusted") or {}).get("pages") or []),
        "cached": cached, "attached": attached, "elapsed_s": round(elapsed_s, 3),
        "ignored_options": list(opts.ignored_options), "unsupported_formats": list(opts.unsupported_formats),
        "warnings": list(opts.warnings) + list(warnings),
    }


def build_response(ctx, request, doc_row: dict, task_row: dict | None, opts: CompatOptions, *, cached: bool,
                   attached: bool, started: float, filename: str | None = None) -> ConvertDocumentResponse:
    name = filename or Path(str(doc_row["source_path"]).replace("\\", "/")).name
    try:
        view = docs.load_doc(ctx, doc_row["id"])
    except ToolFailure as f:
        return failure_response(name, task_row, f"{f.code}: {f.message}", started)
    out = Path(doc_row["output_dir"])
    md, warns = assemble_markdown(view.markdown, doc_row.get("pages"), opts, doc_id=doc_row["id"],
                                  asset_base=asset_base_url(request, ctx), assets_dir=out / "assets", cfg=ctx.config)
    elapsed = max(0.0, time.time() - started) if started else 0.0
    block = doc4ai_block(view.row, task_row, opts, cached=cached, attached=attached, elapsed_s=elapsed, warnings=warns)
    level = block["quality"]["level"]
    errors: list[ErrorItem] = []
    status = "success"
    if view.row.get("status") == "low" or level == "low":
        status = "partial_success"                       # A2: honest, but not a failure (Open WebUI still indexes it)
        reasons = ", ".join(block["quality"]["reasons"]) or "low text quality"
        errors.append(ErrorItem(component_type="pipeline", module_name="doc4ai.quality",
                                error_message=f"quality: low ({reasons})", category="unknown"))
    document = ExportDocument(filename=name, md_content=md,
                              text_content=assemble_text(md, opts) if "text" in opts.to_formats else None, doc4ai=block)
    return ConvertDocumentResponse(document=document, status=status, errors=errors, processing_time=elapsed,
                                   doc4ai={"doc_id": doc_row["id"], "quality_level": level, "cached": cached})


def failure_response(filename: str, task_row: dict | None, message: str, started: float) -> ConvertDocumentResponse:
    tried = [a.get("engine") for a in (task_row or {}).get("tried") or [] if isinstance(a, dict)]
    elapsed = max(0.0, time.time() - started) if started else 0.0
    block = {"doc_id": None, "job_id": task_row["job_id"] if task_row else None, "tried": tried,
             "error_kind": error_kind(message), "cached": False, "elapsed_s": round(elapsed, 3)}
    return ConvertDocumentResponse(document=ExportDocument(filename=filename, doc4ai=block), status="failure",
                                   errors=[error_item(message)], processing_time=elapsed,
                                   doc4ai={"doc_id": None, "quality_level": None, "cached": False})
