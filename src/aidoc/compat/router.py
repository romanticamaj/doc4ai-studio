"""Routes of the docling-serve compatible API (compat spec §4.1). Mounted at the app root before the SPA catch-all;
never under /api, never enveloped."""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aidoc.compat.auth import CompatError, compat_principal, new_call, require_enabled
from aidoc.mcp.principal import Principal

router = APIRouter()

TO_FORMATS = ["md", "text"]
OCR_LANGS = ["zh-Hant", "en"]
FROM_FORMATS = ["pdf", "docx", "pptx", "xlsx", "doc", "ppt", "xls", "html", "md", "csv", "image", "epub", "xml_jats", "json_docling"]


@router.get("/v1/capabilities", dependencies=[Depends(require_enabled)])
def capabilities(request: Request):
    """Unauthenticated like docling-serve's; static apart from engine readiness booleans (plan A4)."""
    from aidoc import __version__
    from aidoc.engines.registry import get_engines
    ctx = request.app.state.ctx
    call = new_call(request)
    call.tool_name = "capabilities"
    cfg = ctx.config
    return JSONResponse({
        "from_formats": FROM_FORMATS, "to_formats": TO_FORMATS, "ocr_langs": OCR_LANGS,
        "engines": {name: bool(e.available()) for name, e in get_engines(cfg).items()},
        "doc4ai": {"version": __version__, "max_file_mb": cfg.compat.max_file_mb,
                   "max_source_mb": cfg.compat.max_source_mb, "sync_timeout_s": cfg.compat.sync_timeout_s}},
        headers={"Cache-Control": "no-store"})


def _whoami(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    request.state.compat.tool_name = "_whoami"
    return {"token_id": principal.token_id}


def install(app) -> None:
    """Mount the compat routes on the app (before the SPA catch-all): the router, the tests-only `/v1/_whoami`
    (AIDOC_TEST_ROUTES=1, read when the app is built) and docling-style 404s for unknown /v1, /v1alpha paths."""
    app.include_router(router)
    if os.environ.get("AIDOC_TEST_ROUTES") == "1":
        app.add_api_route("/v1/_whoami", _whoami, methods=["GET"], include_in_schema=False)

    async def not_found(request: Request, path: str):
        raise CompatError(404, "Not Found")
    for prefix in ("/v1", "/v1alpha"):
        app.add_api_route(prefix + "/{path:path}", not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                          include_in_schema=False)
