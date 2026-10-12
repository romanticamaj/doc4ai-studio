from tests.compat.conftest import last_call
from tests.mcp.conftest import TINY_PNG


def test_asset_download_needs_read_scope_and_is_safe(compat_env, make_doc):
    doc = make_doc("imgdoc", pages=1)
    raw, _ = compat_env.issue(scopes=("doc4ai:read",))
    r = compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/p1_1.png", headers={"X-Api-Key": raw})
    assert r.status_code == 200 and r.content == TINY_PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    row = last_call(compat_env.ctx)
    assert row["tool_name"] == "asset" and row["status"] == "ok"
    assert compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/..%2F{doc['id']}.md", headers={"X-Api-Key": raw}).status_code == 400
    assert compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/nope.png", headers={"X-Api-Key": raw}).status_code == 404
    assert compat_env.client.get("/v1/doc4ai/documents/zzz/assets/p1_1.png", headers={"X-Api-Key": raw}).status_code == 404
    assert compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/p1_1.png").status_code == 401


def test_asset_needs_the_read_scope(compat_env, make_doc):
    doc = make_doc("imgdoc2", pages=1)
    raw, _ = compat_env.issue(scopes=("doc4ai:convert",))
    r = compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/p1_1.png", headers={"X-Api-Key": raw})
    assert r.status_code == 403 and r.json() == {"detail": "token lacks doc4ai:read"}


def test_referenced_mode_links_resolve(workers):
    """A referenced-mode response links to this endpoint with the request's scheme/host."""
    import io

    from tests.compat.test_sync import PDF
    raw, _ = workers.issue()
    r = workers.client.post("/v1/convert/file", files={"files": ("r.pdf", io.BytesIO(PDF), "application/pdf")},
                            data={"image_export_mode": "referenced"}, headers={"X-Api-Key": raw, "Host": "doc4ai.lan:8765"})
    md = r.json()["document"]["md_content"]
    assert "](http://doc4ai.lan:8765/v1/doc4ai/documents/" in md
    url = md.split("](http://doc4ai.lan:8765")[1].split(")")[0]
    assert workers.client.get(url, headers={"X-Api-Key": raw}).status_code == 200


def _junction(link, target):
    import os
    import sys

    import pytest
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pytest.skip("cannot create a directory link here")


def test_asset_route_refuses_links_out_of_assets(compat_env, make_doc):
    """Verifier item 3: a junction / symlink inside assets/ must not lead outside it."""
    from pathlib import Path
    doc = make_doc("linkdoc", pages=1)
    outside = Path(doc["output_dir"]).parent / "elsewhere"
    outside.mkdir()
    (outside / "secret.png").write_bytes(TINY_PNG)
    _junction(Path(doc["output_dir"]) / "assets" / "j", outside)
    raw, _ = compat_env.issue(scopes=("doc4ai:read",))
    r = compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/j/secret.png", headers={"X-Api-Key": raw})
    assert r.status_code in (400, 404) and r.content != TINY_PNG


def test_embedded_images_refuse_links_out_of_assets(tmp_path):
    from pathlib import Path

    from aidoc.compat.render import asset_file
    assets = tmp_path / "assets"
    assets.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.png").write_bytes(TINY_PNG)
    (assets / "ok.png").write_bytes(TINY_PNG)
    _junction(assets / "j", outside)
    assert asset_file(assets, "ok.png") == (assets / "ok.png").resolve()
    assert asset_file(assets, "j/x.png") is None and asset_file(assets, "../outside/x.png") is None
    assert isinstance(asset_file(assets, "nope.png"), Path)          # missing but safe: the caller reports it
