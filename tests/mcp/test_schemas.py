import pytest
from pydantic import ValidationError

from aidoc.mcp import schemas as S
from aidoc.mcp.errors import ERROR_CODES, ToolFailure


def test_tool_failure_payload_and_code_check():
    f = ToolFailure("page_range_invalid", "page 900 is past the end", hint="use get_document_info", pages=412)
    assert f.payload() == {"code": "page_range_invalid", "message": "page 900 is past the end",
                           "hint": "use get_document_info", "pages": 412}
    assert str(f) == "page_range_invalid: page 900 is past the end"
    with pytest.raises(ValueError):
        ToolFailure("made_up_code", "x")
    assert "forbidden_scope" in ERROR_CODES and "path_not_allowed" in ERROR_CODES


def test_output_models_have_json_schema_with_required_fields():
    schema = S.ReadOut.model_json_schema()
    assert schema["type"] == "object"
    for key in ("doc_id", "markdown", "truncated", "unit", "pages", "next", "page_warnings", "tokens_est", "stale"):
        assert key in schema["properties"], key
    hit = S.SearchOut.model_json_schema()["$defs"]["SearchHit"]["properties"]
    assert {"doc_id", "title", "page", "snippet", "score", "more_in_doc", "uri"} <= set(hit)
    info = S.DocInfoOut.model_json_schema()["properties"]
    assert {"outline", "token_estimate", "flagged_pages", "page_map", "resources", "chunks"} <= set(info)


def test_input_filters_forbid_unknown_keys():
    S.SearchFilters(engine="mineru", level="warn", flagged=True, doc_ids=["a"])
    with pytest.raises(ValidationError):
        S.SearchFilters(engine="mineru", bogus=1)
    with pytest.raises(ValidationError):
        S.SearchFilters(level="great")


def test_next_ref_and_convert_out_shapes():
    assert S.NextRef(pages="16-19").model_dump() == {"pages": "16-19", "chunk": None, "offset": None}
    assert S.ConvertOut(job_id=None, status="cached", doc_id="d").model_dump()["status"] == "cached"
    assert S.ChangedOut(changed=False, status="done").changed is False
