"""Upload endpoints (index §8, spec §8.7)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from aidoc.server.auth import ApiError
from aidoc.server.uploads import CHUNK_SIZE, ChunkTooLarge, UploadError

router = APIRouter()


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


def _raise(e: UploadError):
    raise ApiError(e.status, e.error, **e.extra) from None


@router.post("/uploads", status_code=201)
def create_upload(body: UploadIn, request: Request) -> dict:
    mgr = request.app.state.ctx.uploads
    try:
        row = mgr.create(body.filename, body.size, body.sha256)
    except UploadError as e:
        _raise(e)
    return {"upload_id": row["id"], "chunk_size": CHUNK_SIZE, "received": row["received"]}


async def _read_body(request: Request) -> bytes:
    """The raw chunk, refusing (without buffering it) anything over CHUNK_SIZE."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > CHUNK_SIZE:
        raise ChunkTooLarge()
    buf = bytearray()
    async for part in request.stream():
        buf += part
        if len(buf) > CHUNK_SIZE:
            raise ChunkTooLarge()
    return bytes(buf)


@router.put("/uploads/{upload_id}")
async def put_chunk(upload_id: str, request: Request, offset: int = Query(ge=0)) -> dict:
    mgr = request.app.state.ctx.uploads
    try:
        if request.app.state.ctx.store.get_upload(upload_id) is None:
            raise UploadError(404, "not_found")
        data = await _read_body(request)
        return await asyncio.to_thread(mgr.append, upload_id, offset, data)
    except UploadError as e:
        _raise(e)


@router.head("/uploads/{upload_id}")
def head_upload(upload_id: str, request: Request) -> Response:
    row = request.app.state.ctx.store.get_upload(upload_id)
    if row is None:
        return Response(status_code=404)
    return Response(status_code=200, headers={"Upload-Offset": str(row["received"]), "Upload-Length": str(row["size"]),
                                              "Upload-Status": row["status"], "Cache-Control": "no-store"})


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str, request: Request) -> dict:
    row = request.app.state.ctx.store.get_upload(upload_id)
    if row is None:
        raise ApiError(404, "not_found")
    return {"upload": {k: row[k] for k in ("id", "filename", "size", "sha256", "received", "status", "created_at")}}
