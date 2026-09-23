from __future__ import annotations

from fastapi import APIRouter, Request

from aidoc import __version__

router = APIRouter()


@router.get("/system")
def get_system(request: Request) -> dict:
    return {"version": __version__}
