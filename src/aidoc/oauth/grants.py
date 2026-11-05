"""Authorization codes, grants, access and refresh tokens (spec 2026-11-05 §5.3)."""
from __future__ import annotations

GRANT_REVOKED_KEEP_DAYS = 30         # revoked grants stay visible (已撤銷) this long, then go with their tokens
TOKEN_EXPIRED_KEEP_DAYS = 90         # expired / revoked token rows are kept this long
CODE_CONSUMED_KEEP_S = 3600          # a consumed code is kept this long so a reuse can be detected
