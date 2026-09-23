"""Self-check for the docling env: GPU facts + OCR of the bundled 繁中 sample. Prints one JSON line."""
from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    from importlib.metadata import version

    import torch
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        EasyOcrOptions,
        OcrMode,
        PdfPipelineOptions,
        RapidOcrOptions,
    )
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    models = Path(os.environ["AIDOC_MODELS_DIR"]) / "docling"
    if os.environ.get("AIDOC_DOCLING_OCR") == "rapidocr":
        ocr = RapidOcrOptions(lang=["chinese_cht"], mode=OcrMode.FULL_PAGE)
    else:
        ocr = EasyOcrOptions(lang=["ch_tra", "en"], mode=OcrMode.FULL_PAGE)
    po = PdfPipelineOptions(artifacts_path=str(models), do_ocr=True, ocr_options=ocr, do_table_structure=True,
                            accelerator_options=AcceleratorOptions(device="cuda"))
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=po),
                                             InputFormat.IMAGE: ImageFormatOption(pipeline_options=po)})
    sample = Path(__file__).parent / "samples" / "selfcheck_cht.png"
    md = conv.convert(str(sample)).document.export_to_markdown()
    chars = len("".join(md.split()))
    print(json.dumps({"ok": chars > 0, "cuda": bool(torch.cuda.is_available()),
                      "arch_list": list(torch.cuda.get_arch_list()), "sample_chars": chars,
                      "torch": torch.__version__, "version": version("docling"),
                      "sample_has_cjk": "繁體中文" in md}, ensure_ascii=False))


if __name__ == "__main__":
    main()
