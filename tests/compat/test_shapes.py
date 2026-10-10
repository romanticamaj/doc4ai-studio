"""Replays the request shapes real docling-serve clients send (copied from their source, see each file's `source`)
and validates every answer against docling-serve 1.36.0's own OpenAPI schema (spec §9.1, plan Task 11)."""
import base64
import io
import json
from pathlib import Path

import jsonschema
import pytest

HERE = Path(__file__).parent
SHAPES = sorted((HERE / "shapes").glob("*.json"))
OPENAPI = json.loads((HERE / "fixtures" / "docling_serve_openapi_1_36_0.json").read_text(encoding="utf-8"))


def pdf3() -> bytes:
    """A 3-page PDF with a text layer (the fake engine emits one section per page)."""
    import pymupdf
    doc = pymupdf.open()
    for n in (1, 2, 3):
        doc.new_page().insert_text((72, 72), f"Shape test page {n}")
    data = doc.tobytes()
    doc.close()
    return data


PDF3 = pdf3()


def _schema(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}


def _validate(body, name: str) -> None:
    jsonschema.validate(body, _schema(name), cls=jsonschema.Draft202012Validator)


def _get(body, path: str):
    cur = body
    for part in path.split("."):
        cur = cur[part]
    return cur


def _check(body: dict, check: dict) -> None:
    val = _get(body, check["path"])
    op = check["op"]
    if op == "count":
        want = check["equals"]
        if want == "pages-1":
            want = body["document"]["doc4ai"]["pages"] - 1
        assert val.count(check["needle"]) == want, check
    elif op == "contains":
        assert check["value"] in val, check
    elif op == "not_contains":
        assert check["needle"] not in val, check
    elif op == "equals":
        assert val == check["value"], (check, val)
    elif op == "is_null":
        assert val is None, check
    elif op == "not_null":
        assert val, check
    elif op == "list_contains":
        assert check["value"] in val, (check, val)
    elif op == "segment_contains":
        assert check["value"] in val.split(check["needle"])[check["index"]], check
    else:
        raise AssertionError(f"unknown op {op}")


def _replay(env, raw, shape: dict):
    headers = {k: v.replace("<TOKEN>", raw) for k, v in shape["headers"].items()}
    if shape.get("file_field"):
        headers.pop("Content-Type", None)
        name = shape.get("file_name", "a.pdf")
        return env.client.request(shape["method"], shape["path"], headers=headers, data=shape.get("form", {}),
                                  files={shape["file_field"]: (name, io.BytesIO(PDF3), "application/pdf")})
    body = json.loads(json.dumps(shape["json"]).replace("<B64>", base64.b64encode(PDF3).decode()))
    return env.client.request(shape["method"], shape["path"], json=body, headers=headers)


def test_every_shape_names_its_source():
    assert len(SHAPES) >= 7
    for p in SHAPES:
        shape = json.loads(p.read_text(encoding="utf-8"))
        assert shape["source"] and shape["path"].startswith(("/v1/", "/v1alpha/")), p.name
        assert "<TOKEN>" in json.dumps(shape["headers"]), p.name


def test_openapi_snapshot_is_docling_serve_1_36_0():
    assert OPENAPI["info"] == {"title": "Docling Serve", "version": "1.36.0"}
    assert OPENAPI["components"]["schemas"]["ConversionStatus"]["enum"] == [
        "pending", "started", "failure", "success", "partial_success", "skipped"]


@pytest.mark.parametrize("shape_path", SHAPES, ids=[p.stem for p in SHAPES])
def test_shape_replays_and_matches_docling_schema(workers, shape_path):
    env = workers
    raw, _ = env.issue()
    shape = json.loads(shape_path.read_text(encoding="utf-8"))
    r = _replay(env, raw, shape)
    assert r.status_code == shape["expect"]["status_code"], (shape_path.name, r.text)
    body = r.json()
    schema_name = shape["expect"].get("schema")
    if schema_name:
        _validate(body, schema_name)                      # extra keys (doc4ai) allowed: spike S2
    for check in shape["expect"].get("checks", []):
        _check(body, check)
    if schema_name == "ConvertDocumentResponse":
        assert body["status"] in OPENAPI["components"]["schemas"]["ConversionStatus"]["enum"]


def test_curl_async_shape(workers):
    """The async shape needs three requests; replayed by hand."""
    env = workers
    raw, _ = env.issue()
    shape = json.loads((HERE / "shapes" / "curl_async.json").read_text(encoding="utf-8"))
    r = _replay(env, raw, shape)
    _validate(r.json(), "TaskStatusResponse")
    tid = r.json()["task_id"]
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()
    _validate(st, "TaskStatusResponse")
    assert st["task_status"] == "success"
    res = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()
    _validate(res, "ConvertDocumentResponse")
    assert res["document"]["md_content"].count("<!-- pagebreak -->") == res["document"]["doc4ai"]["pages"] - 1


def test_failure_shapes_match_docling_schema(workers):
    """status: failure (sync) and TaskFailureResult / failed TaskStatusResponse (async) validate too."""
    from tests.fakes.scenario import write_scenario
    env = workers
    raw, _ = env.issue()
    bad = env.client.post("/v1/convert/file", files={"files": ("x.exe", io.BytesIO(b"MZ" + b"\0" * 64))},
                          headers={"X-Api-Key": raw}).json()
    _validate(bad, "ConvertDocumentResponse")
    write_scenario(env.ctx.config.root / "sc.json", default="error")
    tid = env.client.post("/v1/convert/file/async", files={"files": ("f.pdf", io.BytesIO(PDF3 + b"%f"))},
                          headers={"X-Api-Key": raw}).json()["task_id"]
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()
    _validate(st, "TaskStatusResponse")
    assert st["task_status"] == "failure"
    _validate(env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json(), "TaskFailureResult")


def test_low_quality_and_capabilities_shapes(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="low")
    raw, _ = env.issue()
    body = env.client.post("/v1/convert/file", files={"files": ("l.pdf", io.BytesIO(PDF3 + b"%l"))},
                           headers={"X-Api-Key": raw}).json()
    _validate(body, "ConvertDocumentResponse")
    assert body["status"] == "partial_success"
    _validate(env.client.get("/health").json(), "HealthCheckResponse")


def test_the_schema_check_has_teeth():
    """Guard against a vacuous validator: the plan's original values (spike S2) must fail docling's schema."""
    good = {"document": {"filename": "a.pdf", "md_content": "x"}, "status": "success", "processing_time": 1.0,
            "errors": [{"component_type": "pipeline", "module_name": "m", "error_message": "e", "category": "unknown"}]}
    _validate(good, "ConvertDocumentResponse")
    for bad in ({**good, "status": "done"},
                {**good, "errors": [{**good["errors"][0], "category": "quality"}]},
                {**good, "errors": [{**good["errors"][0], "component_type": "doc4ai"}]}):
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad, "ConvertDocumentResponse")
    with pytest.raises(jsonschema.ValidationError):
        _validate({"task_id": "t", "task_type": "convert", "task_status": "pending", "task_meta": {"pages_done": 1}},
                  "TaskStatusResponse")
