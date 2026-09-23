from __future__ import annotations
import os
import sys
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path

import tomli_w

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
    docling_ocr: str = "easyocr"
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


_SECTIONS = {"general": General, "engines": Engines, "limits": Limits, "server": Server}


@dataclass
class AidocConfig:
    general: General = field(default_factory=General)
    engines: Engines = field(default_factory=Engines)
    limits: Limits = field(default_factory=Limits)
    server: Server = field(default_factory=Server)
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


def save_config(cfg: AidocConfig, path: Path | None = None) -> None:
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(tomli_w.dumps(cfg.to_dict()), encoding="utf-8")
    os.replace(tmp, p)
