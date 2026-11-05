"""OAuth client registry (spec 2026-11-05 §5.1, §5.2)."""
from __future__ import annotations

DCR_UNUSED_DAYS = 30                 # a DCR client that never got a grant is deleted after this
