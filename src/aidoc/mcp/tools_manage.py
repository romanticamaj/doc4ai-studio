"""Manage-scope tools (MCP spec §5.3): cancel_job, reconvert_document. Phase 1.5 adds delete_document here."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from aidoc.mcp import docs as D
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import SCOPE_MANAGE
from aidoc.mcp.registry import doc4ai_tool
from aidoc.mcp.schemas import ChangedOut, ConvertOut
from aidoc.server.api.documents import start_reconvert
from aidoc.server.auth import ApiError

_MAP = {"not_found": "document_not_found", "source_missing": "source_missing", "already_converting": "already_converting"}


def register_manage_tools(mcp, ctx) -> None:
    store = ctx.store

    @doc4ai_tool(mcp, ctx, name="cancel_job", title="Cancel a conversion job",
                 description="Cancel a queued or running conversion job. Finished jobs are left alone (changed=false).",
                 scope=SCOPE_MANAGE, destructive=True)
    def cancel_job(job_id: Annotated[str, Field(min_length=1, max_length=64)]) -> ChangedOut:
        job = store.get_job(job_id)
        if job is None:
            raise ToolFailure("job_not_found", f"no job {job_id!r}")
        before = job["status"]
        ctx.queue.cancel_job(job_id)
        after = store.get_job(job_id)["status"]
        return ChangedOut(changed=before not in ("done", "cancelled"), status=after)

    @doc4ai_tool(mcp, ctx, name="reconvert_document", title="Reconvert a document",
                 description="Convert a library document again from its retained source (optionally forcing an engine). "
                             "Returns a job_id; the document stays readable (stale=true) until the job finishes.",
                 scope=SCOPE_MANAGE, idempotent=False, destructive=False)
    def reconvert_document(doc_id: Annotated[str, Field(min_length=1, max_length=64)],
                           engine: Literal["markitdown", "docling", "mineru"] | None = None) -> ConvertOut:
        D.doc_row(ctx, doc_id)
        try:
            job = start_reconvert(ctx, [doc_id], origin="mcp", engine=engine)
        except ApiError as e:
            code = _MAP.get(e.error, "already_converting")
            raise ToolFailure(code, e.error.replace("_", " "), **{k: v for k, v in e.extra.items() if k != "id"}) from None
        return ConvertOut(job_id=job["id"], status="queued", doc_id=None)
