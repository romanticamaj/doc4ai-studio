"""Pre-registered constants and decision rules of the real-sample evaluation (spec 2026-10-22 §7; the spec is the
authority — a number here that differs from it is a bug in this file). Changing a constant after the freeze commit
is a spec change and voids the run (spec §8.2)."""
from __future__ import annotations

SEED = 20261022
BOOTSTRAP_N = 10_000
ROUTE_MIN_PAGES = 60
ROUTE_MIN_DOCS = 5
ROUTE_MARGIN = 0.05            # spec §6.5: doubles to ROUTE_MARGIN_LOW_GT when the GT CER upper bound > GT_CER_SOFT
ROUTE_MARGIN_LOW_GT = 0.10
GT_CER_SOFT = 0.025
GT_SPOT_PAGES = 12             # 2 per zh document
ROUTE_DOC_WIN = 0.02
ROUTE_DOC_WINS_MIN = 5         # of 6
ROUTE_DOC_BIG_LOSS = 0.05
FORMULA_MATCH = 0.80
FORMULA_MIN_DOCS = 3           # of 4
FORMULA_MIN_DISPLAY = 15
FORMULA_RECOVERY_MIN = 0.50
FORMULA_GAP = 0.30
TEXT_GUARD = 0.05
VLM_RECALL_MIN = 0.95          # VLM spec §3.4 rule 1 — never changed here
VLM_MAX_SEC_PER_PAGE = 10.0
VLM_MAX_VRAM_MB = 6 * 1024
VLM_COVER_PHRASE = "wouldn't it be dreamy"
VLM_RENDER_DPI = 200
VLM_MAX_LONG_SIDE_PX = 1400
VLM_MAX_TOKENS = 4096
VLM_PINNED = {   # sha256 from docs/superpowers/spikes/2026-10-vlm-ocr.md
    "llamacpp_zip": "419de1292bcb772824fec45e4bc3722e79459e8d89c552578bafdad51266294a",
    "cudart_zip": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
    "gguf": "f3ae46ec885050acf4b3d31944431e1fd90d50664fb09126af4a3c050ba14ee8",
    "mmproj": "204d757d7610d9b3faab10d506d69e5b244e32bf765e2bab2d0167e65e0a058a",
}
CONTROL_RENDER_DPI = 300
CONTROL_VALIDITY_NED = 0.85    # spec §7.0 check 3 on en-aashto-usrn-1984
SNIPPET_MAX = 30
EPS = 1e-9                     # floating-point tolerance at a threshold (0.83 - 0.80 = 0.02999...); not a rule
