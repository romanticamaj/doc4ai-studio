from __future__ import annotations

import os
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
    default_token_ttl_days: int = 90
    max_token_ttl_days: int = 365
    allow_no_expiry: bool = False
    rate_limit_per_min: int = 60
    max_concurrent_jobs_per_token: int = 3
    max_upload_mb: int = 20
    response_token_budget: int = 8000
    call_log_retention_days: int = 30
    call_log_max_rows: int = 200000


_SECTIONS = {"general": General, "engines": Engines, "limits": Limits, "server": Server, "mcp": Mcp}


@dataclass
class AidocConfig:
    general: General = field(default_factory=General)
    engines: Engines = field(default_factory=Engines)
    limits: Limits = field(default_factory=Limits)
    server: Server = field(default_factory=Server)
    mcp: Mcp = field(default_factory=Mcp)
    root: Path = field(default_factory=paths.project_root)
    data_dir: Path = field(default_factory=paths.data_dir)

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
    return cfg


def save_config(cfg: AidocConfig, path: Path | None = None, changes: dict | None = None) -> None:
    """Write to aidoc.toml, keeping the existing file's comments and layout (tomlkit). With `changes`
    ({section: {key: value}}) only those keys are written; otherwise every value of `cfg` that differs from the file
    (or is missing from it) is."""
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = tomlkit.parse(p.read_text(encoding="utf-8")) if p.exists() else tomlkit.document()
    except (OSError, tomlkit.exceptions.ParseError):
        doc = tomlkit.document()
    for section, values in (changes if changes is not None else cfg.to_dict()).items():
        if section not in doc:
            doc[section] = tomlkit.table()
        table = doc[section]
        for key, val in values.items():
            if key not in table or table[key] != val:
                table[key] = val
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, p)
