"""docling-serve fidelity details (verifier item 7)."""
import base64
import io

from aidoc.compat.options import CompatOptions
from tests.compat.test_sync import PDF
from tests.mcp.conftest import page_md

BASE = {"to_formats": ["md"], "unsupported_formats": [], "placeholder": "", "keep_page_markers": False,
        "image_mode": "placeholder", "lang": "cht", "engine": None, "force": False, "timeout_s": None, "warnings": [],
        "ignored_options": []}


def test_placeholder_mode_replaces_every_picture():
    from aidoc.compat.render import assemble_markdown
    body = ("![a](data:image/png;base64,iVBORw0KGgo=) ![b](https://example.com/x.png) ![c](assets/p1_1.png)\n"
            '<img src="http://example.com/y.png"> <IMG alt="z" src="data:image/gif;base64,R0lGOD">\n[link](https://example.com)\n')
    out, _ = assemble_markdown(page_md(1, body=body), 1, CompatOptions(**BASE), doc_id="d", asset_base=None,
                               assets_dir=None, cfg=None)
    assert out.count("<!-- image -->") == 5 and "data:" not in out and "example.com/x.png" not in out
    assert "[link](https://example.com)" in out                        # plain links stay


def test_top_level_callbacks_are_reported_as_ignored(workers):
    env = workers
    raw, _ = env.issue()
    r = env.client.post("/v1/convert/source", headers={"X-Api-Key": raw}, json={
        "callbacks": [{"url": "http://hook.example/x"}],
        "sources": [{"kind": "file", "filename": "cb.pdf", "base64_string": base64.b64encode(PDF + b"%cb").decode()}]})
    assert r.status_code == 200 and "callbacks" in r.json()["document"]["doc4ai"]["ignored_options"]


def test_document_timeout_bounds_the_job(workers):
    env = workers
    raw, _ = env.issue()
    body = env.client.post("/v1/convert/file", files={"files": ("t.pdf", io.BytesIO(PDF + b"%t"))},
                           data={"document_timeout": "45"}, headers={"X-Api-Key": raw}).json()
    job = env.ctx.store.get_job(body["document"]["doc4ai"]["job_id"])
    assert job["options"]["timeout_s"] == 45


def test_docling_root_endpoints_and_bare_v1(compat_env):
    c = compat_env.client
    assert c.get("/ready").json() == {"status": "ok"}
    v = c.get("/version")
    assert v.status_code == 200 and "doc4ai" in v.json() and "workspace" not in v.json()
    for path in ("/v1", "/v1/", "/v1alpha", "/v1alpha/"):
        r = c.get(path)
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, path
