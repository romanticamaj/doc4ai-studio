from __future__ import annotations
from dataclasses import dataclass, field, asdict, fields
from enum import Enum
from pathlib import Path
from typing import Literal, Callable

Lang = Literal["cht", "en"]
ENGINE_NAMES = ("markitdown", "docling", "mineru")
SEGMENT_PAGES = 40


class TaskStatus(str, Enum):
    queued = "queued"
    probing = "probing"
    converting = "converting"
    checking = "checking"
    done = "done"
    low = "low"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


TERMINAL_TASK = {TaskStatus.done, TaskStatus.low, TaskStatus.failed, TaskStatus.skipped, TaskStatus.cancelled}


class SegmentStatus(str, Enum):
    queued = "queued"
    converting = "converting"
    done = "done"
    failed = "failed"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    cancelled = "cancelled"


class ErrorKind(str, Enum):
    transient = "transient"
    engine = "engine"
    input = "input"


class DocStatus(str, Enum):
    ok = "ok"
    low = "low"
    orphaned = "orphaned"


InputKind = Literal["pdf", "image", "office", "html", "audio", "other"]


@dataclass
class ConvertOptions:
    output_dir: Path
    engine: str | None = None          # forced engine, no fallback
    lang: Lang = "cht"
    force: bool = False
    retry_low: bool = False
    timeout_s: int | None = None
    allow_online_audio: bool = False
    mineru_tier: str = "basic"         # "basic" | "standard"
    docling_ocr: str = "easyocr"       # "easyocr" | "rapidocr" (decided by P1 spike)

    def to_json(self) -> dict:
        d = asdict(self)
        d["output_dir"] = str(self.output_dir)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "ConvertOptions":
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        kw["output_dir"] = Path(kw["output_dir"])
        return cls(**kw)


@dataclass
class ProbeResult:
    kind: InputKind
    ext: str                           # lower-case, with dot: ".pdf"
    size: int
    pages: int | None = None           # PDFs only
    text_ratio: float = 0.0
    image_cover: float = 0.0
    math_hint: bool = False
    layout_hint: bool = False
    has_table_lines: bool = False
    blank_pages: list[int] = field(default_factory=list)   # 1-based
    error: str | None = None           # "corrupt" | "encrypted" | None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class RouteDecision:
    engines: list[str]                 # primary first, then fallbacks
    reason: str                        # human readable rule name, e.g. "pdf_scanned"
    forced: bool = False
    missing: list[str] = field(default_factory=list)   # engines dropped because not installed


@dataclass
class TableEdge:
    page: int                          # page within the segment's *own* PDF (1-based)
    n_cols: int
    touches_edge: bool                 # first table: touches top; last table: touches bottom

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "TableEdge":
        return cls(**d)


@dataclass
class RawResult:
    markdown: str
    image_paths: list[Path]
    raw_dir: Path
    has_page_markers: bool
    page_count: int | None = None
    first_table: TableEdge | None = None
    last_table: TableEdge | None = None


@dataclass
class NormalizedResult:
    markdown: str
    assets: list[tuple[Path, str]]


@dataclass
class QualityResult:
    score: float                       # 0..1
    level: Literal["ok", "low"]
    reasons: list[str]                 # "chars_per_page", "garbage_ratio", "missing_table"
    metrics: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Attempt:                         # one entry of tasks.tried_json
    engine: str
    attempt: int
    score: float | None
    reasons: list[str]
    error_kind: str | None = None
    error_msg: str | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Attempt":
        return cls(**d)


ProgressCb = Callable[[float, str], None]   # (fraction 0..1 within the segment, log line)
