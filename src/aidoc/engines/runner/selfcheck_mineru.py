"""Self-check for the mineru env: GPU facts + parse of the bundled 繁中 sample. Prints one JSON line."""
from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    from importlib.metadata import version

    import torch
    from mineru.parser import parse
    sample = Path(__file__).parent / "samples" / "selfcheck_cht.png"
    result = parse(str(sample), tier=os.environ.get("AIDOC_MINERU_TIER", "basic"))
    md = result.markdown()
    chars = len("".join(md.split()))
    print(json.dumps({"ok": chars > 0, "cuda": bool(torch.cuda.is_available()),
                      "arch_list": list(torch.cuda.get_arch_list()), "sample_chars": chars,
                      "torch": torch.__version__, "version": version("mineru"),
                      "sample_has_cjk": "繁體中文" in md}, ensure_ascii=False))


if __name__ == "__main__":
    main()
