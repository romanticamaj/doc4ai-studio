"""MCP tools `verify_document` (read) and `verify_markdown` (convert) — spec 2026-10-05 verify §2.6. Thin shells
around `aidoc.verify.verify_markdown`; neither writes the store."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Literal

import anyio
from pydantic import Field

from aidoc.mcp import docs as D
from aidoc.mcp.budget import estimate_tokens
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import SCOPE_CONVERT, SCOPE_READ
from aidoc.mcp.registry import doc4ai_tool, render_text
from aidoc.mcp.schemas import VerifyOut, VerifyPage, VerifyPageMap, VerifySummary
from aidoc.mcp.tools_convert import decode_content

PAGES_MAX = 50
Format = Literal["auto", "aidoc", "mineru", "docling", "markitdown", "pdfmux", "none"]


def _hint(j: dict) -> str | None:
    if j["verifiable"] is False:
        if (j.get("pdf") or {}).get("text_layer_pages"):
            return ("unverifiable: the Markdown has no page markers and almost none of its text matches the PDF text "
                    "layer (an empty file, or not this PDF?); only document-level checks ran")
        return ("unverifiable: the PDF has no text layer to anchor on and the Markdown has no page markers "
                "(a scanned PDF); only document-level checks ran")
    if j["verifiable"] == "partial":
        return "partial: no source PDF was available, so only marker coverage and garbage were checked"
    flagged = [e["page"] for e in j["pages"] if e.get("reasons")]
    if flagged:
        return f"{len(flagged)} flagged page(s); read them with read_document (first: {flagged[0]})"
    return None


def to_out(j: dict, doc_id: str | None) -> VerifyOut:
    pm = j["page_map"]
    vpm = None
    if pm is not None:
        al = pm.get("alignment")
        vpm = VerifyPageMap(method=pm.get("method"), expected=pm.get("expected"), found=pm.get("found"),
                            coverage=pm.get("coverage"), alignment=al.get("ratio") if isinstance(al, dict) else None,
                            anchored=pm.get("anchored"), decidable=pm.get("decidable"),
                            confidence=pm.get("confidence"))
    pages = [VerifyPage(page=e["page"], reasons=list(e.get("reasons") or []),
                        advisories=list(e.get("advisories") or [])) for e in j["pages"]]
    return VerifyOut(doc_id=doc_id, verifiable=j["verifiable"], summary=VerifySummary(**j["summary"]), page_map=vpm,
                     inferred=bool(pm and pm.get("method") == "inferred"), pages=pages[:PAGES_MAX],
                     pages_truncated=len(pages) > PAGES_MAX, hint=_hint(j))


def fit(out: VerifyOut, budget: int) -> VerifyOut:
    """Every response ≤ the token budget: drop the tail of `pages` first (marking it truncated)."""
    while estimate_tokens(render_text(out))[0] > budget and out.pages:
        out.pages, out.pages_truncated = out.pages[: len(out.pages) // 2], True
    return out


def _verify(md: str, pdf: Path | None, *, pages, fmt, previous, name) -> dict:
    from aidoc.verify import verify_markdown
    try:
        return verify_markdown(md, pdf, pages=pages, fmt=fmt, previous=previous, input_name=name).to_json()
    except ValueError as e:
        if str(e).startswith("pdf_unreadable"):
            raise ToolFailure("unsupported_file", str(e), hint="send an unencrypted, readable PDF") from None
        raise ToolFailure("invalid_arguments", str(e)) from None


def _doc_inputs(ctx, doc_id: str):
    from aidoc.verify import OutputMissing, document_inputs
    row = D.doc_row(ctx, doc_id)
    try:
        return document_inputs(row)
    except OutputMissing:
        raise ToolFailure("output_missing", "the converted output is no longer on disk",
                          hint="reconvert_document can rebuild it when the source is still available") from None


def register_verify_tools(mcp, ctx) -> None:
    @doc4ai_tool(mcp, ctx, name="verify_document", title="Verify a document page by page",
                 description="Re-check a Library document's Markdown against its source PDF: page-marker coverage and "
                             "alignment, dropped pages, broken text layers, garbage, missing tables/formulas (advisory). "
                             "Structural checks only — it cannot see OCR misreadings. Returns the level, flagged pages "
                             "(max 50) and a hint. Writes nothing.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    async def verify_document(doc_id: Annotated[str, Field(min_length=1, max_length=64)]) -> VerifyOut:
        md, pdf, previous, pages, name = _doc_inputs(ctx, doc_id)
        j = await anyio.to_thread.run_sync(lambda: _verify(md, pdf, pages=pages if pdf is None else None,
                                                           fmt="auto", previous=previous, name=name))
        return fit(to_out(j, doc_id), ctx.config.mcp.response_token_budget)

    @doc4ai_tool(mcp, ctx, name="verify_markdown", title="Verify Markdown from another tool",
                 description="Score Markdown made by any tool (MinerU, Docling, MarkItDown, …) against a PDF: either the "
                             "source PDF of a Library document (doc_id) or an uploaded PDF (pdf_base64) — exactly one. "
                             "Understands MinerU/Docling/pdfmux page markers and infers page boundaries when there are "
                             "none. Max size per file: mcp.max_upload_mb. Writes nothing.",
                 scope=SCOPE_CONVERT, idempotent=True)
    async def verify_markdown(markdown_base64: Annotated[str, Field(min_length=4)],
                              doc_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None,
                              pdf_base64: Annotated[str, Field(min_length=4)] | None = None,
                              format: Format = "auto") -> VerifyOut:
        if (doc_id is None) == (pdf_base64 is None):
            raise ToolFailure("invalid_arguments", "give exactly one of doc_id or pdf_base64",
                              hint="doc_id = compare with a Library document's source PDF; pdf_base64 = your own PDF")
        limit = ctx.config.mcp.max_upload_mb * 1024 * 1024
        try:
            md = decode_content(markdown_base64, limit).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ToolFailure("invalid_arguments", "the Markdown must be UTF-8") from None
        if doc_id is not None:
            _, pdf, _, pages, _ = _doc_inputs(ctx, doc_id)
            j = await anyio.to_thread.run_sync(lambda: _verify(md, pdf, pages=pages if pdf is None else None,
                                                               fmt=format, previous=None, name="markdown.md"))
            return fit(to_out(j, doc_id), ctx.config.mcp.response_token_budget)
        data = decode_content(pdf_base64, limit)
        with tempfile.TemporaryDirectory(prefix="aidoc-verify-") as tmp:
            pdf = Path(tmp) / "source.pdf"
            pdf.write_bytes(data)
            j = await anyio.to_thread.run_sync(lambda: _verify(md, pdf, pages=None, fmt=format, previous=None,
                                                               name="markdown.md"))
        return fit(to_out(j, None), ctx.config.mcp.response_token_budget)
