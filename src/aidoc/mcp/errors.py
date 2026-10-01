"""Anticipated tool failures (MCP spec §5.1): `isError: true` with `{code, message, hint}` for the model to act on."""
from __future__ import annotations

ERROR_CODES = frozenset({
    "forbidden_scope", "tool_disabled", "invalid_arguments", "invalid_cursor", "document_not_found", "output_missing",
    "page_range_invalid", "heading_not_found", "job_not_found", "file_too_large", "invalid_base64", "unsupported_file",
    "insufficient_disk", "too_many_jobs", "path_not_allowed", "path_not_found", "source_missing", "already_converting",
    "tokenizer_unavailable", "search_unavailable",
})


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None, **extra):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown tool error code {code!r}")
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.hint, self.extra = code, message, hint, extra

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint, **self.extra}
