"""A12 (spec 2026-10-29 §13-§14): a real PDF under a deep Chinese path is converted by the real engines, its source
copy is verified, then the original is moved away and the work copy expires — and the Document view, reassess,
verify and reconvert are all served by the copy. Run: AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow <this file>"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest

from aidoc.cli import main
from tests.manual import manual_sample, require_engine

pytestmark = pytest.mark.slow


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def test_real_pdf_served_verified_reassessed_and_reconverted_from_its_copy(tmp_root, capsys):
    for engine in ("docling", "mineru"):            # auto routing of a text PDF: docling first, mineru as fallback
        require_engine(engine)
    sample = manual_sample("ortho_p300-340.pdf")
    deep = tmp_root / "深層" / "資料夾" / ("b" * 120)
    deep.mkdir(parents=True)
    src = deep / "骨科 第300-340頁（測試）.pdf"
    shutil.copy(sample, src)
    sha = _sha(src)

    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "done" and payload["source_copy_status"]["status"] == "copied"
    copy = tmp_root / "source" / "骨科 第300-340頁（測試）" / "骨科 第300-340頁（測試）.pdf"
    assert copy.is_file() and _sha(copy) == sha                                   # verified copy
    assert payload["source_copy"]["path"] == "source/骨科 第300-340頁（測試）/骨科 第300-340頁（測試）.pdf"

    # the original goes away and the work copy expires
    src.unlink()
    from aidoc.config import load_config
    from aidoc.sources import purge_expired_work_copies, resolve_source
    from aidoc.store import Store
    store = Store(tmp_root / "data" / "aidoc.db")
    doc = store.list_documents()[0]
    store.update_document(doc["id"], work_copy_expires_at=time.time() - 1)
    assert purge_expired_work_copies(store, time.time()) == 1
    doc = store.get_document(doc["id"])
    assert doc["work_copy_path"] is None
    store.close()

    from fastapi.testclient import TestClient

    from aidoc.reassess import reassess_document
    from aidoc.server.app import build_context, create_app
    ctx = build_context(load_config(), token=None, start_workers=False)
    served = []
    try:
        with TestClient(create_app(ctx), client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8765") as c:
            # 1/4 Document view
            d = c.get(f"/api/documents/{doc['id']}").json()
            assert d["source_kind"] == "source_copy" and d["source_available"] is True
            r = c.get(f"/api/documents/{doc['id']}/source")
            assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == sha
            served.append("view")
            # 2/4 reassess reads the copy
            row = ctx.store.get_document(doc["id"])
            assert resolve_source(row, pdf_only=True).kind == "source_copy"
            q = reassess_document(ctx.store, row, force=True)
            assert q is not None and q["page_check"] >= 1
            served.append("reassess")
            # 3/4 verify
            v = c.post("/api/verify", json={"doc_id": doc["id"]})
            assert v.status_code == 200, v.text
            rep = v.json()
            assert rep["verifiable"] is True and rep["pdf"]["sha256"] == sha
            served.append("verify")
            # 4/4 reconvert through the real queue
            job = c.post("/api/documents/reconvert", json={"ids": [doc["id"]]})
            assert job.status_code == 201, job.text
            task = c.get(f"/api/jobs/{job.json()['job']['id']}").json()["tasks"][0]
            assert Path(task["work_path"]).is_file() and Path(task["work_path"]).stat().st_nlink == 1   # a real copy
            ctx.queue.process_next()
            task = c.get(f"/api/jobs/{job.json()['job']['id']}").json()["tasks"][0]
            assert task["status"] == "done", task
            assert task["quality"]["level"] in ("ok", "warn")
            served.append("reconvert")
            after = ctx.store.get_document(doc["id"])
            assert after["source_copy_path"] == str(copy) and _sha(copy) == sha     # the copy was reused
    finally:
        ctx.close()
    assert served == ["view", "reassess", "verify", "reconvert"]                    # A12: 4/4 from the copy
