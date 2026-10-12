"""VLM OCR engine (spec 2026-10-05 VLM §4): one engine name, backends "openai" (any OpenAI-compatible vision
endpoint) and "local" (deferred: spike S1 rule 4, docs/superpowers/spikes/2026-10-vlm-ocr.md). The runner runs on
the host interpreter; there is no envs/vlm/.venv."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from aidoc import paths
from aidoc.engines.base import RunnerEngine
from aidoc.models import ConvertOptions, ProbeResult

# Spike S1 (Task 1) fell through to decision rule 4: the local backend is not delivered on any platform yet.
LOCAL_SUPPORTED = False
LOCAL_UNSUPPORTED_MSG = ("vlm: the local backend is not supported on this platform yet "
                         "(see docs/superpowers/spikes/2026-10-vlm-ocr.md); use backend = \"openai\"")
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def sanitize_model(model: str) -> str:
    """Model name as used in labels (`repaired_by`): [A-Za-z0-9._:-] only, at most 64 characters (spec §6)."""
    return re.sub(r"[^A-Za-z0-9._:-]", "", model)[:64]


def endpoint_host(base_url: str) -> str:
    return urlsplit(base_url).netloc


def is_loopback_url(base_url: str) -> bool:
    host = urlsplit(base_url).hostname or ""
    return host in _LOOPBACK or host.startswith("127.")


class VlmEngine(RunnerEngine):
    name = "vlm"

    @property
    def v(self):
        return self.config.vlm

    # --- runtime locations: the runner is stdlib + pymupdf, it runs on this interpreter
    def python(self) -> Path:
        return Path(sys.executable)

    def script(self) -> Path:
        return paths.runner_script("vlm")

    def env_extra(self) -> dict[str, str]:
        if self.v.backend == "openai" and self.v.api_key:
            return {"AIDOC_VLM_API_KEY_INLINE": self.v.api_key}      # never written to request.json
        return {}

    # --- state
    def local_supported(self) -> bool:
        return LOCAL_SUPPORTED

    def is_remote(self) -> bool:
        return self.v.backend == "openai"

    def key_available(self) -> bool:
        v = self.v
        if v.api_key_env and os.environ.get(v.api_key_env):
            return True
        return bool(v.api_key) or bool(v.allow_no_key)

    def available(self) -> bool:
        v = self.v
        if v.backend == "openai":
            return bool(v.remote_enabled and v.base_url and v.model and self.key_available())
        if v.backend == "local":
            return LOCAL_SUPPORTED and paths.ready_marker("vlm").exists()
        return False

    def remote_allowed(self, opts: ConvertOptions) -> bool:
        return bool(self.v.remote_enabled) and opts.vlm_remote is not False

    def endpoint_host(self) -> str:
        return endpoint_host(self.v.base_url) if self.v.backend == "openai" else "local"

    def local_endpoint(self) -> bool:
        return self.v.backend != "openai" or is_loopback_url(self.v.base_url)

    def model_name(self) -> str:
        """The backend's model identity (options_key, labels): the model for openai, the preset for local."""
        if self.v.backend == "openai":
            return self.v.model
        if self.v.backend == "local":
            return self.v.local_preset
        return ""

    def label(self) -> str:
        if self.v.backend == "openai":
            return f"vlm:openai:{sanitize_model(self.v.model)}"
        return f"vlm:local:{self.v.local_preset}"

    # --- runner options
    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        v = self.v
        if v.backend != "openai":
            return {"backend": v.backend}
        host = (urlsplit(v.base_url).hostname or "").lower()
        return {"backend": "openai", "dpi": v.dpi, "max_image_px": v.max_image_px, "image_format": "png",
                "prompt_version": _prompt_version(), "prompt_style": "generic", "lang": opts.lang,
                "base_url": v.base_url, "model": v.model, "api_key_env": v.api_key_env,
                "allow_no_key": v.allow_no_key, "request_timeout_s": v.request_timeout_s,
                "max_concurrent": v.max_concurrent, "max_output_tokens": v.max_output_tokens,
                # `detail` only for OpenAI-family endpoints (spec §12: do not send fields others may reject)
                "detail": "high" if "openai" in host else None,
                "page_cap": v.remote_max_pages_per_doc}

    def oom_downgrade(self, engine_opts: dict) -> dict | None:
        return None                                   # remote: no local memory to save

    def timeout_factor(self, pages: int | None, timeout: float) -> float:
        """The segment timeout for this engine: remote VLMs take 10–40 s a page (spec §4.4)."""
        if self.v.backend != "openai" or not pages:
            return timeout
        return max(timeout, pages * self.v.request_timeout_s / max(1, self.v.max_concurrent) * 1.5)



def _prompt_version() -> int:
    from aidoc.engines.runner import _vlm_prompt
    return _vlm_prompt.PROMPT_VERSION
