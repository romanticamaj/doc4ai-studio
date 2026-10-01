from aidoc.repair import repair_order


def test_repair_order_prefers_same_style_engine():
    both = {"docling": True, "mineru": True}
    assert [lbl for _, _, lbl in repair_order("docling", both)] == ["docling:pypdfium_full_page_ocr", "mineru:ocr"]
    assert [lbl for _, _, lbl in repair_order("markitdown", both)][0] == "docling:pypdfium_full_page_ocr"
    assert [lbl for _, _, lbl in repair_order("mineru", both)] == ["mineru:ocr", "docling:pypdfium_full_page_ocr"]
    assert [e for e, _, _ in repair_order("docling", {"docling": False, "mineru": True})] == ["mineru"]
