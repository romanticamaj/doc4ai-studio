import json
import sys
from pathlib import Path

from aidoc.config import load_config
from aidoc.engines.vlm import VlmEngine
from aidoc.models import ConvertOptions, ProbeResult

PDF = ProbeResult(kind="pdf", ext=".pdf", size=1)


def _cfg(tmp_path, body):
    p = tmp_path / "aidoc.toml"
    p.write_text(body, encoding="utf-8")
    return load_config(p)


def test_none_backend_is_unavailable_and_inert(tmp_path):
    e = VlmEngine(_cfg(tmp_path, ""))
    assert not e.available() and e.engine_opts(ConvertOptions(output_dir=tmp_path), PDF) == {"backend": "none"}
    assert e.python() == Path(sys.executable) and e.script().name == "vlm_runner.py"


def test_openai_availability_needs_switch_and_key(tmp_path, monkeypatch):
    body = '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "m"\nremote_enabled = %s\n'
    monkeypatch.delenv("AIDOC_VLM_API_KEY", raising=False)
    assert not VlmEngine(_cfg(tmp_path, body % "false")).available()
    assert not VlmEngine(_cfg(tmp_path, body % "true")).available()          # no key
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "sk-test-secret")
    e = VlmEngine(_cfg(tmp_path, body % "true"))
    assert e.available() and e.is_remote() and e.label() == "vlm:openai:m"
    eo = e.engine_opts(ConvertOptions(output_dir=tmp_path), PDF)
    assert eo["backend"] == "openai" and eo["api_key_env"] == "AIDOC_VLM_API_KEY"
    assert "sk-test-secret" not in json.dumps(eo)
    assert e.oom_downgrade(eo) is None
    allow = '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "m"\nremote_enabled = true\n' \
            'api_key_env = ""\nallow_no_key = true\n'
    monkeypatch.delenv("AIDOC_VLM_API_KEY")
    assert VlmEngine(_cfg(tmp_path, allow)).available()


def test_local_backend_is_not_supported_yet(tmp_path, monkeypatch):
    """Spike S1 rule 4 (docs/superpowers/spikes/2026-10-vlm-ocr.md): only the openai backend is delivered."""
    monkeypatch.setenv("AIDOC_ENVS", str(tmp_path / "envs"))
    env = tmp_path / "envs" / "vlm"
    env.mkdir(parents=True)
    (env / ".ready").write_text("{}", encoding="utf-8")
    e = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "local"\n'))
    assert not e.available() and not e.local_supported() and e.label() == "vlm:local:paddleocr-vl-1.6"
    assert not e.is_remote()


def test_label_sanitises_model_and_detail_only_for_openai_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    e = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "https://api.openai.com/v1"\n'
                                 'model = "gpt-4.1-mini (2026) / vision"\nremote_enabled = true\n'))
    assert e.label() == "vlm:openai:gpt-4.1-mini2026vision"
    assert e.engine_opts(ConvertOptions(output_dir=tmp_path), PDF)["detail"] == "high"
    o = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "m"\n'
                                 'remote_enabled = true\n'))
    assert o.engine_opts(ConvertOptions(output_dir=tmp_path), PDF)["detail"] is None
    assert o.remote_allowed(ConvertOptions(output_dir=tmp_path))
    assert not o.remote_allowed(ConvertOptions(output_dir=tmp_path, vlm_remote=False))


def test_inline_key_goes_to_the_environment_not_the_request(tmp_path, monkeypatch):
    monkeypatch.delenv("AIDOC_VLM_API_KEY", raising=False)
    e = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://h/v1"\nmodel = "m"\n'
                                 'remote_enabled = true\napi_key = "sk-inline"\n'))
    assert e.available() and e.env_extra() == {"AIDOC_VLM_API_KEY_INLINE": "sk-inline"}
    assert "sk-inline" not in json.dumps(e.engine_opts(ConvertOptions(output_dir=tmp_path), PDF))


def test_remote_timeout_factor(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    e = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://h/v1"\nmodel = "m"\nremote_enabled = true\n'
                                 'request_timeout_s = 100\nmax_concurrent = 2\n'))
    assert e.timeout_factor(40, 2400) == 3000                        # 40 × 100 / 2 × 1.5
    assert e.timeout_factor(1, 120) == 120
