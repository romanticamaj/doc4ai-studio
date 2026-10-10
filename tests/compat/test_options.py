import pytest

from aidoc.config import AidocConfig


@pytest.fixture
def cfg():
    return AidocConfig()


def test_defaults(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({}, cfg, multipart=False)
    assert o.to_formats == ["md"] and o.placeholder == "" and o.image_mode == "placeholder"
    assert o.lang == "cht" and o.engine is None and o.force is False and o.ignored_options == []


def test_openwebui_form_fields(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({"image_export_mode": "placeholder", "md_page_break_placeholder": "\f", "doc4ai_lang": "en",
                       "pdf_backend": "dlparse_v4", "table_mode": "accurate"}, cfg, multipart=True)
    assert o.placeholder == "\f" and o.lang == "en" and o.ignored_options == ["pdf_backend", "table_mode"]


def test_to_formats_list_forms_and_unsupported(cfg):
    from aidoc.compat.options import parse_options
    assert parse_options({"to_formats": ["json", "md", "text"]}, cfg, multipart=False).unsupported_formats == ["json"]
    assert parse_options({"to_formats": "md,text"}, cfg, multipart=True).to_formats == ["md", "text"]
    assert parse_options({"to_formats": ["md", "html"]}, cfg, multipart=True).unsupported_formats == ["html"]  # repeated field
    assert parse_options({"to_formats": ["text"]}, cfg, multipart=False).to_formats == ["md", "text"]
    with pytest.raises(Exception) as e:
        parse_options({"to_formats": 5}, cfg, multipart=False)
    assert getattr(e.value, "status_code", None) == 422


def test_ocr_lang_and_force_ocr(cfg, tmp_root):
    from aidoc.compat.options import parse_options
    assert parse_options({"ocr_lang": ["zh-Hant", "en"]}, cfg, multipart=False).lang == "cht"
    assert parse_options({"ocr_lang": ["en"]}, cfg, multipart=False).lang == "en"
    assert parse_options({"ocr_lang": "en"}, cfg, multipart=True).lang == "en"
    assert parse_options({"ocr_lang": ["fr"]}, cfg, multipart=False).lang == cfg.general.lang
    assert parse_options({"ocr_lang": ["en"], "doc4ai_lang": "cht"}, cfg, multipart=False).lang == "cht"
    o = parse_options({"force_ocr": "true"}, cfg, multipart=True)          # no mineru .ready under tmp_root
    assert o.engine is None and any("force_ocr" in w for w in o.warnings)


def test_force_ocr_with_mineru_ready(cfg, tmp_root):
    from aidoc import paths
    from aidoc.compat.options import parse_options
    marker = paths.ready_marker("mineru")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok", encoding="utf-8")
    assert parse_options({"force_ocr": True}, cfg, multipart=False).engine == "mineru"
    assert parse_options({"force_ocr": True, "doc4ai_engine": "docling"}, cfg, multipart=False).engine == "docling"


def test_extensions_and_bools(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({"doc4ai_engine": "docling", "doc4ai_force": "1", "doc4ai_keep_page_markers": "yes",
                       "image_export_mode": "embedded", "document_timeout": "120"}, cfg, multipart=True)
    assert o.engine == "docling" and o.force and o.keep_page_markers and o.image_mode == "embedded" and o.timeout_s == 120
    assert parse_options({"document_timeout": 99999}, cfg, multipart=False).timeout_s == cfg.compat.sync_timeout_s
    for bad in ({"doc4ai_engine": "tesseract"}, {"image_export_mode": "svg"}, {"doc4ai_force": "maybe"},
                {"doc4ai_lang": "fr"}, {"document_timeout": "soon"}):
        with pytest.raises(Exception) as e:
            parse_options(bad, cfg, multipart=False)
        assert e.value.status_code == 422, bad


def test_do_ocr_false_and_unknown_keys_are_reported(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({"do_ocr": "false", "zzz_custom": "1", "do_chunking": True}, cfg, multipart=True)
    assert o.ignored_options == ["do_chunking", "do_ocr", "zzz_custom"]
    assert any("do_ocr" in w for w in o.warnings) and any("do_chunking" in w for w in o.warnings)
    assert parse_options({"do_ocr": True}, cfg, multipart=False).ignored_options == ["do_ocr"]


def test_roundtrip_json(cfg):
    from aidoc.compat.options import CompatOptions, parse_options
    o = parse_options({"md_page_break_placeholder": "\f", "to_formats": ["md", "text"], "page_range": [1, 3]}, cfg, multipart=False)
    assert "page_range" in o.ignored_options and any("page_range" in w for w in o.warnings)
    assert CompatOptions.from_json(o.to_json()) == o
