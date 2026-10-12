from __future__ import annotations

import os
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomlkit

from aidoc import paths

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class General:
    output_dir: str = "out"
    lang: str = "cht"
    work_retention_days: int = 7
    enable_audio: bool = False


@dataclass
class Engines:
    mineru_tier: str = "basic"
    docling_ocr: str = "rapidocr"
    docling_page_batch_size: int = 16


@dataclass
class Limits:
    upload_max_bytes: int = 2147483648
    timeout_per_page_s: int = 60
    timeout_min_s: int = 120
    timeout_per_mb_s: int = 30
    timeout_max_no_pages_s: int = 1800
    startup_timeout_s: int = 900
    disk_space_factor: int = 3
    # ZIP-based inputs (Office, EPUB, ODF, .zip) are checked before any engine runs (decompression bombs)
    archive_max_uncompressed_mb: int = 512
    archive_max_entries: int = 10000
    archive_max_entry_mb: int = 256
    archive_max_ratio: int = 200
    # memory cap of one engine runner process tree; 0 = automatic (75 % of RAM; MarkItDown 25 %, at most 8 GB)
    engine_memory_mb: int = 0
    markitdown_memory_mb: int = 0


@dataclass
class Server:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""


@dataclass
class Mcp:
    """[mcp] (spec 2026-10-01 MCP §10)."""
    enabled: bool = True
    allowed_hosts: list[str] = field(default_factory=list)      # extra Host values; the bound interface is added
    local_path_roots: list[str] = field(default_factory=list)   # convert_path whitelist; empty = tool hidden
    allow_drive_root: bool = False                              # a whole drive (C:\ or /) as a root needs this
    default_token_ttl_days: int = 90
    max_token_ttl_days: int = 365
    allow_no_expiry: bool = False
    rate_limit_per_min: int = 60
    max_concurrent_jobs_per_token: int = 3
    max_upload_mb: int = 20
    response_token_budget: int = 8000
    call_log_retention_days: int = 30
    call_log_max_rows: int = 200000


@dataclass
class Preview:
    """[preview] Office preview via LibreOffice (aidoc.preview). soffice_path is read-only over the API."""
    enabled: bool = True
    soffice_path: str = ""          # empty = PATH, then the usual install locations
    timeout_s: int = 180
    max_mb: int = 200


@dataclass
class Compat:
    """[compat] docling-serve compatible API (spec 2026-10-05 compat §7)."""
    enabled: bool = True
    sync_timeout_s: int = 600
    max_file_mb: int = 200
    max_source_mb: int = 50
    embed_images_max_mb: int = 20
    max_concurrent_jobs_per_token: int = 8
    poll_max_wait_s: int = 60
    task_retention_days: int = 30
    json_content: str = "null"          # "null" | "doc4ai" (spike S1: RAGFlow reads md_content first)


@dataclass
class Vlm:
    """[vlm] VLM OCR engine (spec 2026-10-05 VLM §8.3). backend "none" (default) changes nothing."""
    backend: str = "none"                  # "none" | "local" | "openai"
    use_as_fallback: bool = True
    use_for_repair: bool = True
    dpi: int = 150                         # remote; the local backend uses local_dpi
    max_image_px: int = 2000               # remote: long-side limit
    request_timeout_s: int = 120
    max_output_tokens: int = 4096
    # --- openai backend
    remote_enabled: bool = False           # explicit consent: page images are sent to base_url
    base_url: str = ""
    model: str = ""
    api_key_env: str = "AIDOC_VLM_API_KEY"
    api_key: str = ""                      # prefer the environment variable; masked over the API
    allow_no_key: bool = False
    max_concurrent: int = 2
    remote_max_pages_per_doc: int = 200
    remote_daily_page_budget: int = 500    # 0 = unlimited
    # --- local backend (llama.cpp + GGUF)
    local_preset: str = "paddleocr-vl-1.6"  # "paddleocr-vl-1.6" | "qwen2.5-vl-3b" | "custom"
    local_dpi: int = 200
    local_max_image_px: int = 1400
    local_ctx_size: int = 8192
    local_gguf: str = ""                   # preset "custom": absolute path
    local_mmproj: str = ""
    local_prompt_style: str = ""           # "" = by preset; "paddle_tasks" | "generic"


VLM_BACKENDS = ("none", "local", "openai")
VLM_PRESETS = ("paddleocr-vl-1.6", "qwen2.5-vl-3b", "custom")
VLM_PROMPT_STYLES = ("", "paddle_tasks", "generic")
# (key, low, high) — shared by load_config and PUT /api/settings
VLM_RANGES = {"max_concurrent": (1, 8), "dpi": (72, 300), "local_dpi": (72, 300), "max_image_px": (512, 4096),
              "local_max_image_px": (512, 4096), "local_ctx_size": (2048, 32768), "request_timeout_s": (10, 600),
              "max_output_tokens": (256, 16384), "remote_max_pages_per_doc": (0, 10 ** 6),
              "remote_daily_page_budget": (0, 10 ** 7)}


def vlm_base_url_problem(url: str) -> str | None:
    """None when `url` is an absolute http(s) URL without credentials or a query (spec §8.3)."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
    except ValueError:
        return "vlm.base_url is not a URL"
    if u.scheme not in ("http", "https") or not u.netloc:
        return "vlm.base_url must be an absolute http(s):// URL"
    if "@" in u.netloc:
        return "vlm.base_url must not contain credentials (@); use api_key_env"
    if u.query or u.fragment:
        return "vlm.base_url must not contain a query or fragment"
    return None


def vlm_problem(v: Vlm) -> str | None:
    """The first fatal problem of a [vlm] section (load_config: refuse to start; PUT /api/settings: 422)."""
    if v.backend not in VLM_BACKENDS:
        return f"vlm.backend must be one of {list(VLM_BACKENDS)}"
    if v.local_preset not in VLM_PRESETS:
        return f"vlm.local_preset must be one of {list(VLM_PRESETS)}"
    if v.local_prompt_style not in VLM_PROMPT_STYLES:
        return f"vlm.local_prompt_style must be one of {list(VLM_PROMPT_STYLES)}"
    for key, (lo, hi) in VLM_RANGES.items():
        val = getattr(v, key)
        if not isinstance(val, int) or isinstance(val, bool) or not lo <= val <= hi:
            return f"vlm.{key} must be an integer in {lo}..{hi}"
    if v.backend == "openai" or v.base_url:
        problem = vlm_base_url_problem(v.base_url)
        if problem:
            return problem
    if v.backend == "openai" and not v.model.strip():
        return "vlm.model must be set for the openai backend"
    return None


def _check_vlm(cfg: AidocConfig, where: Path) -> None:
    v = cfg.vlm
    problem = vlm_problem(v)
    if problem:
        raise ConfigError(f"{where}: {problem}")
    if v.remote_enabled and v.backend != "openai":
        cfg.warnings.append("vlm.remote_enabled is only meaningful with backend = \"openai\"; ignored")
        v.remote_enabled = False
    if v.api_key:
        cfg.warnings.append("vlm.api_key is stored in aidoc.toml; prefer the environment variable "
                            f"{v.api_key_env or 'AIDOC_VLM_API_KEY'}")
    if v.local_preset == "custom" and v.backend == "local":
        for key in ("local_gguf", "local_mmproj"):
            val = getattr(v, key)
            if not val or not Path(val).is_absolute() or not Path(val).is_file():
                cfg.warnings.append(f"vlm.{key} must be an existing absolute path for local_preset = \"custom\"")


class ConfigError(ValueError):
    """aidoc.toml holds a value that is unsafe to run with (the server refuses to start)."""


_HOST_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:*]+$")
MCP_LISTS = ("allowed_hosts", "local_path_roots")


def is_drive_root(value: str) -> bool:
    p = Path(value)
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        pass
    return p.parent == p


def mcp_list_problem(key: str, value: str, allow_drive_root: bool = False) -> tuple[str, bool] | None:
    """The one rule set for [mcp] list entries, shared by PUT /api/settings (422 on any problem) and load_config
    (fatal problems refuse to start, the others are dropped with a warning). Returns (message, fatal) or None."""
    if key == "allowed_hosts":
        if not value or not _HOST_RE.match(value):
            return f"mcp.allowed_hosts entry '{value}' is not a host[:port|:*]", False
        return None
    s = value.replace("\\", "/")
    if s.startswith("//"):                         # UNC and \\?\ / \\.\ device paths alike
        return f"mcp.local_path_roots entry '{value}' must be a local path (no UNC or \\\\?\\ paths)", True
    p = Path(value)
    if not p.is_absolute():
        return f"mcp.local_path_roots entry '{value}' must be absolute", True
    if is_drive_root(value) and not allow_drive_root:
        return (f"mcp.local_path_roots entry '{value}' is a whole drive; name a folder, or set "
                f"mcp.allow_drive_root = true if every file on it may be converted by path"), True
    if not p.is_dir():
        return f"mcp.local_path_roots entry '{value}' does not exist", False
    return None


def _check_mcp(cfg: AidocConfig, where: Path) -> None:
    days = cfg.mcp.call_log_retention_days
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ConfigError(f"{where}: mcp.call_log_retention_days must be an integer >= 1 (0 would delete the whole "
                          f"MCP call log on every maintenance run)")
    for key in MCP_LISTS:
        val = getattr(cfg.mcp, key)
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            raise ConfigError(f"{where}: mcp.{key} must be a list of strings")
        kept = []
        for v in val:
            problem = mcp_list_problem(key, v, bool(cfg.mcp.allow_drive_root))
            if problem is None:
                kept.append(v)
            elif problem[1]:
                raise ConfigError(f"{where}: {problem[0]}; fix or remove it and start again")
            else:
                cfg.warnings.append(f"{problem[0]}; ignored")
        setattr(cfg.mcp, key, kept)
    for root in cfg.mcp.local_path_roots:
        if is_drive_root(root):
            cfg.warnings.append(f"mcp.local_path_roots includes the drive root '{root}' (allowed by "
                                f"mcp.allow_drive_root): any file on that drive can be converted by path")


_SECTIONS = {"general": General, "engines": Engines, "limits": Limits, "server": Server, "mcp": Mcp,
             "preview": Preview, "compat": Compat, "vlm": Vlm}


@dataclass
class AidocConfig:
    general: General = field(default_factory=General)
    engines: Engines = field(default_factory=Engines)
    limits: Limits = field(default_factory=Limits)
    server: Server = field(default_factory=Server)
    mcp: Mcp = field(default_factory=Mcp)
    preview: Preview = field(default_factory=Preview)
    compat: Compat = field(default_factory=Compat)
    vlm: Vlm = field(default_factory=Vlm)
    root: Path = field(default_factory=paths.project_root)
    data_dir: Path = field(default_factory=paths.data_dir)
    warnings: list[str] = field(default_factory=list)            # load-time problems that were ignored

    def output_root(self) -> Path:
        p = Path(self.general.output_dir)
        return p if p.is_absolute() else self.root / p

    def to_dict(self) -> dict:
        return {name: asdict(getattr(self, name)) for name in _SECTIONS}


def config_path() -> Path:
    env = os.environ.get("AIDOC_CONFIG")
    return Path(env) if env else paths.project_root() / "aidoc.toml"


def _build(section: str, cls, data: dict):
    names = {f.name for f in fields(cls)}
    for key in data:
        if key not in names:
            raise ValueError(f"unknown config key {section}.{key}")
    return cls(**data)


def load_config(path: Path | None = None) -> AidocConfig:
    p = Path(path) if path else config_path()
    raw: dict = {}
    if p.exists():
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    cfg = AidocConfig()
    for section, data in raw.items():
        if section not in _SECTIONS:
            raise ValueError(f"unknown config section {section}")
        setattr(cfg, section, _build(section, _SECTIONS[section], data))
    _check_mcp(cfg, p)
    _check_vlm(cfg, p)
    return cfg


def save_config(cfg: AidocConfig, path: Path | None = None, changes: dict | None = None) -> None:
    """Write to aidoc.toml, keeping the existing file's comments and layout (tomlkit). With `changes`
    ({section: {key: value}}) only those keys are written; otherwise every value of `cfg` that differs from the file
    (or is missing from it) is."""
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    newline = "\n"
    try:
        raw = p.read_bytes().decode("utf-8") if p.exists() else ""
        newline = "\r\n" if "\r\n" in raw else "\n"           # keep the file's own line endings (no whole-file diff)
        doc = tomlkit.parse(raw.replace("\r\n", "\n")) if raw else tomlkit.document()
    except (OSError, UnicodeDecodeError, tomlkit.exceptions.ParseError):
        doc = tomlkit.document()
    for section, values in (changes if changes is not None else cfg.to_dict()).items():
        if section not in doc:
            doc[section] = tomlkit.table()
        table = doc[section]
        for key, val in values.items():
            if key not in table or table[key] != val:
                table[key] = val
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(tomlkit.dumps(doc).replace("\r\n", "\n"), encoding="utf-8", newline=newline)
    os.replace(tmp, p)
