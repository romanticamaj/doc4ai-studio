"""RAG chunks of one document (index §8, spec §5 / §6 page 5)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from aidoc import chunk as chunking
from aidoc.output import read_sidecar
from aidoc.server.api.documents import output_available
from aidoc.server.auth import ApiError

router = APIRouter()


class ChunksIn(BaseModel):
    document_id: str
    max_tokens: int = Field(default=800, ge=1, le=100000)


@router.post("/chunks")
def make_chunks(body: ChunksIn, request: Request) -> Response:
    doc = request.app.state.ctx.store.get_document(body.document_id)
    if doc is None:
        raise ApiError(404, "not_found")
    if not output_available(doc):
        raise ApiError(409, "output_missing")
    out = Path(doc["output_dir"])
    sc = read_sidecar(out) or {}
    source = chunking.source_label(sc.get("source") or doc["source_path"])
    try:
        chunks = chunking.chunk_markdown((out / f"{out.name}.md").read_text(encoding="utf-8"), source,
                                         body.max_tokens, lambda t: chunking.count_tokens(t), stem=out.name)
    except chunking.TokenizerUnavailable as e:
        raise ApiError(503, "tokenizer_unavailable", message=str(e)) from None
    lines = "".join(json.dumps(c.to_json(), ensure_ascii=False) + "\n" for c in chunks)
    return Response(lines.encode("utf-8"), media_type="application/x-ndjson; charset=utf-8",
                    headers={"X-Chunk-Count": str(len(chunks))})
