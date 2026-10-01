"""Input and output models of every tool (MCP spec §5.3). Output models become `outputSchema`; the SDK validates
`structuredContent` against them, so every field the tools fill is declared here and nowhere else."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchFilters(_In):
    engine: Literal["markitdown", "docling", "mineru"] | None = None
    level: Literal["ok", "warn", "low"] | None = None
    flagged: bool | None = None
    doc_ids: list[str] | None = Field(default=None, max_length=50)


class ListFilters(_In):
    engine: Literal["markitdown", "docling", "mineru"] | None = None
    level: Literal["ok", "warn", "low"] | None = None
    flagged: bool | None = None
    q: str | None = Field(default=None, max_length=200)


class ErrorInfo(BaseModel):
    code: str
    message: str
    hint: str | None = None


class SearchHit(BaseModel):
    doc_id: str
    title: str
    page: int | None
    snippet: str
    score: float
    more_in_doc: int = 0
    uri: str


class SearchOut(BaseModel):
    hits: list[SearchHit]
    next_cursor: str | None = None
    query: str


class QualityBrief(BaseModel):
    level: str
    score: float


class DocSummary(BaseModel):
    doc_id: str
    title: str
    source_name: str
    pages: int | None
    engine: str
    quality: QualityBrief
    flagged_pages: int
    updated_at: float


class ListDocumentsOut(BaseModel):
    documents: list[DocSummary]
    next_cursor: str | None = None
    total: int


class PageWarning(BaseModel):
    page: int
    reason: str
    reasons: list[str]
    repaired: bool


class OutlineItem(BaseModel):
    level: int
    title: str
    page: int | None


class TokenRange(BaseModel):
    pages: str
    tokens: int


class TokenEstimate(BaseModel):
    total: int
    per_page_avg: int | None
    ranges: list[TokenRange]
    method: Literal["tiktoken", "bytes"]


class QualityFull(BaseModel):
    level: str
    score: float
    reasons: list[str]


class PageMapBrief(BaseModel):
    expected: int | None = None
    found: int | None = None
    coverage: float | None = None
    alignment: float | None = None


class DocInfoOut(BaseModel):
    doc_id: str
    title: str
    source_name: str
    pages: int | None
    engine: str
    lang: str
    quality: QualityFull
    page_map: PageMapBrief | None
    flagged_pages: list[PageWarning]
    flagged_pages_truncated: bool = False
    outline: list[OutlineItem]
    outline_truncated: bool = False
    token_estimate: TokenEstimate
    chunks: int | None
    resources: list[str]
    stale: bool = False
    job_id: str | None = None


class PageSpan(BaseModel):
    start: int
    end: int


class NextRef(BaseModel):
    pages: str | None = None       # paged documents: the pages still to read, e.g. "16-19"
    chunk: int | None = None       # page-less documents: the next chunk index
    offset: int | None = None      # character offset inside the first unit when one page/chunk had to be cut


class ReadOut(BaseModel):
    doc_id: str
    title: str
    unit: Literal["pages", "chunks"]
    pages: PageSpan | None
    markdown: str
    truncated: bool
    next: NextRef | None
    page_warnings: list[PageWarning]
    tokens_est: int
    stale: bool = False
    job_id: str | None = None


class ChunkOut(BaseModel):
    chunk_id: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    text: str


class ChunksOut(BaseModel):
    chunks: list[ChunkOut]
    next_cursor: str | None = None
    total: int


class TaskBrief(BaseModel):
    task_id: str
    source_name: str
    status: str
    engine: str | None
    quality_level: str | None
    doc_id: str | None
    error: str | None


class Progress(BaseModel):
    pages_done: int
    pages_total: int | None


class JobOut(BaseModel):
    job_id: str
    status: str
    origin: str
    progress: Progress
    tasks: list[TaskBrief]
    error: str | None = None


class ConvertOut(BaseModel):
    job_id: str | None
    status: str                    # queued | running | done | low | failed | cancelled | cached
    doc_id: str | None = None


class ChangedOut(BaseModel):
    changed: bool
    status: str


AnyOut = BaseModel
JsonDict = dict[str, Any]
