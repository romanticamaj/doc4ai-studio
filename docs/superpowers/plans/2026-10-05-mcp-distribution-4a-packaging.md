# MCP Distribution & Hardening — Part 4a: Packaging and one-line install

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Read `docs/superpowers/plans/2026-10-05-mcp-distribution-index.md` first (§0, §3 D2–D5, §4, §7 gates G3–G5) and spec §3 of `docs/superpowers/specs/2026-10-05-mcp-distribution-hardening-design.md`.

**Goal:** Ship Doc4AI Studio as two Docker images (CPU, CUDA 12.8) with models on a volume and a healthcheck; let a user write any supported MCP client's config with one command (`aidoc mcp install`) or one click (Cursor / VS Code install links); ship a Claude Desktop `.mcpb`; publish a validated `server.json` to the MCP Registry.

**Conventions for every task:** fast suite `uv run pytest -m "not slow" -q` and `ruff check src tests` green before each commit; Docker steps are verified on the Windows host with Docker Desktop (WSL 2) and, when available, on a Linux box; commit trailer from the index §0. Tasks A1–A5 touch `envs/*` and `setup_engines.py`, which the VLM-OCR work may also touch: pull/rebase before each.

---

### Task A1: SPIKE — uv dependency groups for torch variants, model-download idempotency, CUDA base image

**Files:** Create `docs/superpowers/spikes/2026-10-packaging.md`.

**Questions:**

- **S-A1 Group-scoped sources.** In a scratch copy of `envs/docling/pyproject.toml` add:
  ```toml
  [dependency-groups]
  cu128 = ["torch>=2.7,<2.12", "torchvision>=0.22"]
  cpu   = ["torch>=2.7,<2.12", "torchvision>=0.22"]
  [tool.uv]
  conflicts = [[{ group = "cpu" }, { group = "cu128" }]]
  [[tool.uv.index]]
  name = "pytorch-cu128"; url = "https://download.pytorch.org/whl/cu128"; explicit = true
  [[tool.uv.index]]
  name = "pytorch-cpu"; url = "https://download.pytorch.org/whl/cpu"; explicit = true
  [tool.uv.sources]
  torch = [{ index = "pytorch-cu128", group = "cu128" }, { index = "pytorch-cpu", group = "cpu" }]
  torchvision = [{ index = "pytorch-cu128", group = "cu128" }, { index = "pytorch-cpu", group = "cpu" }]
  ```
  (torch moves out of `dependencies` into the groups; `docling[...]` stays in `dependencies` and must not pull a PyPI torch — check with `uv tree --group cu128`.) Run `uv lock` and `uv sync --group cu128` on the Windows host; expect `torch 2.11.0+cu128` as today (`envs/README.md` table) and the self-check green. Then `uv sync --group cpu` into a second scratch venv (`UV_PROJECT_ENVIRONMENT`) and confirm `torch.cuda.is_available() is False`, `+cpu` build. **Decision rule:** both work from one `uv.lock` → index D2 stands; otherwise two lock files (D2 fallback) — record the exact uv version and error.
- **S-A2 Model downloads.** With models present, time `docling-tools models download -o <dir> layout tableformer rapidocr` and `mineru-kit models download --tier basic --small-backend torch --source auto`; expect no re-download (seconds). If either re-downloads, design the marker file `data/models/<engine>/.complete` (contents: tier/list + version) checked by `aidoc setup --no-sync`.
- **S-A3 CUDA base image.** Build a throwaway `nvidia/cuda:12.8.1-base-ubuntu24.04` + `apt python3.12` + `uv` image, `uv pip install torch --index-url …/cu128`, run with `--gpus all` on the Windows host: `torch.cuda.is_available()`, `get_arch_list()` contains `sm_120`. Record the final image size and whether `libgl1`/`libglib2.0-0` are needed for MinerU/OpenCV (MinerU's Dockerfile installs `libgl1`).
- **S-A4 MinerU on CPU.** In the cpu venv, run `selfcheck_mineru.py` on the sample page; record time and whether `MINERU_DEVICE_MODE=cpu` (or equivalent) is required.
- **S-A5 `.ready` in the container layer.** Confirm `paths.ready_marker()` is writable as the non-root user (`envs/<e>/` owned by UID 10001 at build) and that recreating the container reruns the self-check in < 30 s with models present.

- [ ] **Step 1:** Run the probes; write the spike file; update index D2 if the fallback is needed.
- [ ] **Step 2: Commit** `docs(spike): packaging — uv torch variant groups, model cache idempotency, CUDA base image`.

---

### Task A2: Engine env variant groups, `[engines] torch_variant`, `aidoc setup --no-sync`

**Files:** Modify `envs/docling/pyproject.toml`, `envs/mineru/pyproject.toml` (+ regenerate both `uv.lock`), `envs/README.md`; `src/aidoc/config.py` (`Engines.torch_variant: str = "cu128"`), `src/aidoc/setup_engines.py` (`plan_commands` adds `--group <variant>` to `uv sync`; `setup(..., sync=True)`; cpu self-check rule), `src/aidoc/cli.py` (`aidoc setup --no-sync`, `--engines` filter for `all`), `src/aidoc/server/api/settings.py` (`engines.torch_variant` read-only → 403 `engines_readonly`); tests `tests/unit/test_setup_variants.py`, `tests/api/test_system_settings.py` (append).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_setup_variants.py
from pathlib import Path

from aidoc.config import AidocConfig


def test_plan_commands_pass_the_variant_group():
    from aidoc.setup_engines import plan_commands
    cfg = AidocConfig()
    sync = plan_commands("docling", cfg)[0]
    assert sync[:3] == ["uv", "sync", "--project"] and sync[-2:] == ["--group", "cu128"]
    cfg.engines.torch_variant = "cpu"
    assert plan_commands("mineru", cfg)[0][-2:] == ["--group", "cpu"]
    assert "--group" not in plan_commands("markitdown", cfg)[0]          # no torch there


def test_no_sync_skips_uv_sync_only():
    from aidoc.setup_engines import plan_commands
    cmds = plan_commands("docling", AidocConfig(), sync=False)
    assert all(c[:2] != ["uv", "sync"] for c in cmds) and any("models" in c for c in cmds)


def test_cpu_variant_relaxes_gpu_check(tmp_root, monkeypatch):
    from aidoc import setup_engines
    cfg = AidocConfig()
    cfg.engines.torch_variant = "cpu"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stderr = ""
        R.stdout = '{"ok": true, "cuda": false, "arch_list": [], "sample_chars": 120, "version": "2.130", "device": "cpu"}'
        return R()
    (tmp_root / "envs" / "docling").mkdir(parents=True)
    monkeypatch.setenv("AIDOC_ENVS", str(tmp_root / "envs"))
    assert setup_engines.setup("docling", cfg, log=lambda s: None, run=fake_run) is True
    marker = __import__("json").loads((tmp_root / "envs" / "docling" / ".ready").read_text())
    assert marker["device"] == "cpu" and marker["cuda"] is False
    cfg.engines.torch_variant = "cu128"
    assert setup_engines.setup("docling", cfg, log=lambda s: None, run=fake_run) is False   # GPU variant still requires CUDA


def test_env_pyprojects_declare_both_groups():
    import tomllib
    for e in ("docling", "mineru"):
        data = tomllib.loads((Path(__file__).resolve().parents[2] / "envs" / e / "pyproject.toml").read_text(encoding="utf-8"))
        assert set(data["dependency-groups"]) >= {"cpu", "cu128"}
        assert all("torch" not in d for d in data["project"]["dependencies"])
        assert {"group": "cpu"} in [{k: v for k, v in s.items() if k == "group"} for s in data["tool"]["uv"]["sources"]["torch"]]
```

Append to `tests/api/test_system_settings.py`: `PUT /api/settings {"engines": {"torch_variant": "cpu"}}` → 403 `engines_readonly`; `GET` shows the value.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spike S-A1 (or the two-lock fallback: `plan_commands` then passes `--lockfile`/swaps files — record in the spike and here). `setup()` GPU rule: `if engine in GPU_ENGINES and config.engines.torch_variant != "cpu" and not (cuda and sm_120): fail`; cpu variant requires only `ok` + `sample_chars ≥ 20`, writes `device: "cpu"`. CLI: `setup --no-sync`, `setup all --engines docling,markitdown`. `uv lock --project envs/docling` and `envs/mineru`; run `uv run aidoc setup all` on the host (cu128) to confirm nothing changed for the Windows workflow (torch 2.11.0+cu128 per `envs/README.md`).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_setup_variants.py tests/unit/test_setup*.py tests/api/test_system_settings.py -q` → PASS; `uv run aidoc setup docling` on the host → `ready`.
- [ ] **Step 5: Commit** `feat(setup): cpu/cu128 torch variants as uv groups; aidoc setup --no-sync`.

---

### Task A3: Dockerfile (cpu + cuda targets), `.dockerignore`, entrypoint

**Files:** Create `docker/Dockerfile`, `docker/entrypoint.sh`, `.dockerignore`; test `tests/unit/test_dockerfiles.py` (static checks — the build itself is Task A5/CI).

**Dockerfile shape (one file, `--target cpu|cuda`):**

```dockerfile
# syntax=docker/dockerfile:1.7
ARG UV_VERSION=0.12.23
FROM node:24-slim AS web
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim-bookworm AS base-cpu
FROM nvidia/cuda:12.8.1-base-ubuntu24.04 AS base-cuda
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 python3.12-venv libgl1 libglib2.0-0 ca-certificates && rm -rf /var/lib/apt/lists/*

FROM base-${VARIANT_BASE} AS build          # VARIANT_BASE=cpu|cuda via ARG, or two explicit build stages (spike decides)
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=bind,source=uv.lock,target=uv.lock --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY envs/ /app/envs/
RUN --mount=type=cache,target=/root/.cache/uv for e in markitdown docling mineru; do uv sync --locked --project envs/$e --group ${TORCH_VARIANT} || uv sync --locked --project envs/$e; done
COPY src/ /app/src/
COPY pyproject.toml uv.lock aidoc.toml README.md LICENSE /app/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
COPY --from=web /src/web/dist /app/web/dist
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN useradd -u 10001 -m aidoc && mkdir -p /data /out && chown -R aidoc:aidoc /app /data /out
USER 10001
ENV AIDOC_ROOT=/app AIDOC_ENVS=/app/envs AIDOC_DATA=/data AIDOC_MODELS=/data/models AIDOC_CONFIG=/data/aidoc.toml \
    PYTHONUNBUFFERED=1 UV_NO_SYNC=1 OMP_NUM_THREADS=4 PATH="/app/.venv/bin:$PATH"
LABEL org.opencontainers.image.source="https://github.com/<user>/doc4ai-studio" org.opencontainers.image.licenses="AGPL-3.0" \
      io.modelcontextprotocol.server.name="io.github.<user>/doc4ai"
VOLUME ["/data", "/out"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4)" || exit 1
ENTRYPOINT ["/app/docker/entrypoint.sh"]
```

(`TORCH_VARIANT` build-arg = `cpu` for the cpu target, `cu128` for cuda; the `|| uv sync` fallback handles `markitdown`, which has no groups. Exact stage wiring per the spike; the test below checks the invariants, not the layout.)

**`entrypoint.sh`:**

```sh
#!/bin/sh
set -eu
[ -f /data/aidoc.toml ] || cp /app/aidoc.toml /data/aidoc.toml
HOST="${AIDOC_HOST:-0.0.0.0}"; PORT="${AIDOC_PORT:-8765}"
if [ "$HOST" != "127.0.0.1" ] && [ -z "${AIDOC_TOKEN:-}" ]; then
  echo "AIDOC_TOKEN is required when binding $HOST (see compose.yaml / .env.example)" >&2; exit 2
fi
case "${AIDOC_SETUP_ENGINES:-all}" in
  none) ;;
  all)  aidoc setup all --no-sync ;;
  *)    aidoc setup all --no-sync --engines "$AIDOC_SETUP_ENGINES" ;;
esac
exec aidoc serve --host "$HOST" --port "$PORT" ${AIDOC_SERVE_ARGS:-}
```

(`aidoc serve` already reads `AIDOC_TOKEN`; in the cuda image, `aidoc setup` prints a warning instead of failing when `torch.cuda.is_available()` is false — index M5 — by honouring `torch_variant = "cu128"` + env `AIDOC_ALLOW_NO_GPU=1` set by the cuda entrypoint; Task A2's rule gains that escape hatch.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_dockerfiles.py
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DF = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_invariants():
    assert "FROM python:3.12-slim-bookworm AS base-cpu" in DF
    assert "FROM nvidia/cuda:12.8.1-base-ubuntu24.04 AS base-cuda" in DF         # base, not cudnn-runtime (pip wheels bring CUDA libs)
    assert re.search(r"COPY --from=ghcr.io/astral-sh/uv:\d+\.\d+\.\d+ /uv /uvx /bin/", DF)
    assert "UV_COMPILE_BYTECODE=1" in DF and "UV_LINK_MODE=copy" in DF and "--locked" in DF
    assert "USER 10001" in DF and "HEALTHCHECK" in DF and "/health" in DF and "--start-period=300s" in DF
    for env in ("AIDOC_ROOT=/app", "AIDOC_ENVS=/app/envs", "AIDOC_DATA=/data", "AIDOC_MODELS=/data/models", "AIDOC_CONFIG=/data/aidoc.toml"):
        assert env in DF
    assert "io.modelcontextprotocol.server.name=" in DF and 'VOLUME ["/data", "/out"]' in DF
    assert "models download" not in DF                                             # models never baked into the image


def test_dockerignore_excludes_runtime_dirs():
    ign = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for d in ("data/", "out/", ".venv/", "envs/*/.venv/", "node_modules/", "web/dist/", "tests/fixtures/manual/", ".git/"):
        assert d in ign, d


def test_entrypoint_refuses_public_bind_without_token_and_allows_loopback(tmp_path):
    sh = ROOT / "docker" / "entrypoint.sh"
    stub = tmp_path / "bin"; stub.mkdir()
    (stub / "aidoc").write_text("#!/bin/sh\necho aidoc \"$@\"\n"); (stub / "aidoc").chmod(0o755)
    env = {"PATH": f"{stub}:/usr/bin:/bin", "AIDOC_SETUP_ENGINES": "none"}
    r = subprocess.run(["sh", str(sh)], env={**env, "AIDOC_HOST": "0.0.0.0"}, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2 and "AIDOC_TOKEN" in r.stderr
    r = subprocess.run(["sh", str(sh)], env={**env, "AIDOC_HOST": "127.0.0.1"}, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and "serve --host 127.0.0.1" in r.stdout
    r = subprocess.run(["sh", str(sh)], env={**env, "AIDOC_HOST": "0.0.0.0", "AIDOC_TOKEN": "t", "AIDOC_SETUP_ENGINES": "docling,markitdown"},
                       capture_output=True, text=True, cwd=tmp_path)
    assert "setup all --no-sync --engines docling,markitdown" in r.stdout
```

(On the Windows host `sh` is Git Bash's; mark the entrypoint test `@pytest.mark.skipif(shutil.which("sh") is None)`. The stub writes into `/data`? No — the entrypoint copies `aidoc.toml` only if `/data` exists; guard with `[ -d /data ]` so the test can run outside a container.)

- [ ] **Step 2: Run** → FAIL (files missing).
- [ ] **Step 3: Write the three files** (Dockerfile per spike wiring; `.dockerignore` with the list above plus `*.pyc`, `.pytest_cache/`, `docs/superpowers/screenshots/`).
- [ ] **Step 4: Run** the unit tests → PASS; `docker build --target cpu -t doc4ai:cpu-dev .` on the host (expect success; record size); `docker run --rm -p 127.0.0.1:8765:8765 -e AIDOC_HOST=127.0.0.1 -e AIDOC_SETUP_ENGINES=none doc4ai:cpu-dev` → `curl :8765/health` ok.
- [ ] **Step 5: Commit** `build(docker): Dockerfile with cpu and cuda targets, entrypoint, .dockerignore`.

---

### Task A4: `compose.yaml`, `compose.cpu.yaml`, `.env.example`, container smoke test

**Files:** Create `compose.yaml` (spec §3.1 block), `compose.cpu.yaml` (override: `image: …:cpu`, `build.target: cpu`, no `deploy.resources`), `.env.example` (`AIDOC_TOKEN=`, `AIDOC_SETUP_ENGINES=all`); test `tests/unit/test_compose.py` (`docker compose -f compose.yaml config` parses when `docker` is on PATH — else skip; YAML-level assertions otherwise: `127.0.0.1:8765:8765`, `AIDOC_TOKEN` required syntax `${AIDOC_TOKEN:?…}`, `ipc: host`, healthcheck test uses `/health`, named volume `doc4ai-data:/data`, `./out:/out`).

- [ ] **Step 1: Tests** as described (parse with `yaml` from PyYAML — add to dev deps if absent — or `tomllib`-free string checks).
- [ ] **Step 2–4:** RED → write files → GREEN. Smoke on the host: `AIDOC_TOKEN=devtoken docker compose -f compose.yaml -f compose.cpu.yaml up -d` → wait for `healthy` → `curl -H "Authorization: Bearer devtoken" :8765/api/system` shows `docling.ready true` after the models download (first run: minutes; log the elapsed time); `curl -X POST :8765/mcp -d '{}'` → 401; `docker compose down && up -d` → healthy within 60 s (models on the volume). Record sizes and timings in the deviations section. GPU host: same with `compose.yaml` alone → `/api/system` `cuda: true`.
- [ ] **Step 5: Commit** `build(docker): compose files and env example`.

---

### Task A5: Deployment docs, CI image build, README

**Files:** Create `docs/deploy/docker.md` (spec §3.1 "Windows 開發主機說明" + volume layout + env table + first-start model download + updating + backup of `/data`), `.github/workflows/docker.yml` (on tag `v*` and manual: build `cpu` target, run `uv run pytest -m "not slow" -q` inside the built image (`docker run --rm doc4ai:cpu uv run --no-sync pytest -m "not slow" -q` — tests and fixtures are in the image only in a `test` stage; add a `FROM build AS test` stage that copies `tests/`), push `cpu` to GHCR; build `cuda` without running tests; **push is gated by gate G4** — the workflow has `push` behind `if: vars.DOC4AI_PUBLISH == 'true'`), `README.md` (Quick start gains a "Docker" subsection: two commands, link to the doc); test `tests/unit/test_docs_docker.py` (README contains `docker compose up`, `docs/deploy/docker.md` exists and mentions WSL 2, `--gpus all`, named volume, `AIDOC_TOKEN`).

- [ ] **Step 1–5:** RED → write → GREEN → commit `docs(deploy): Docker guide, CI image build, README quick start`. Record gate G4's answer before enabling the publish variable.

---

### Task A6: `snippets.client_configs()` — one source for snippets, installer and install links

**Files:** Modify `src/aidoc/mcp/snippets.py`; test `tests/mcp/test_snippets.py` (append).

**Interfaces:**

```python
@dataclass(frozen=True)
class ClientConfig:
    client: str                 # claude-code | cursor | vscode | claude-desktop
    title: str
    path_hint: str              # where it lives, with ~ and %APPDATA% placeholders (display only)
    payload: dict | None        # JSON merged into the file (None for claude-code: a command instead)
    command: list[str] | None   # claude-code: argv for `claude mcp add …`
    install_link: str | None    # cursor:// … / vscode:mcp/install?… ; None otherwise
    snippet: str                # the text shown today (render_snippets().text)
    language: str

def client_configs(endpoint_url: str, token: str = PLACEHOLDER, *, alias: str = SERVER_ALIAS, allow_http: bool | None = None) -> list[ClientConfig]
def render_snippets(endpoint_url, token=PLACEHOLDER) -> list[dict]      # unchanged output, now derived from client_configs()
def install_links(endpoint_url, token=PLACEHOLDER) -> list[dict]        # [{client, url}] for cursor + vscode
```

- [ ] **Step 1: Write the failing tests**

```python
import base64
import json
from urllib.parse import unquote, urlparse, parse_qs


def test_client_configs_are_the_single_source():
    from aidoc.mcp.snippets import client_configs, render_snippets
    cfgs = {c.client: c for c in client_configs("http://box:8765/mcp", "doc4ai_pat_X")}
    assert set(cfgs) >= {"claude-code", "cursor", "vscode", "claude-desktop"}
    snippets = {s["client"]: s for s in render_snippets("http://box:8765/mcp", "doc4ai_pat_X")}
    for k, c in cfgs.items():
        if k in snippets:
            assert snippets[k]["text"] == c.snippet and snippets[k]["language"] == c.language
    assert cfgs["claude-code"].command[:5] == ["claude", "mcp", "add", "--transport", "http"]
    assert cfgs["cursor"].payload == {"mcpServers": {"doc4ai": {"url": "http://box:8765/mcp", "headers": {"Authorization": "Bearer doc4ai_pat_X"}}}}
    assert cfgs["vscode"].payload["servers"]["doc4ai"]["headers"]["Authorization"] == "Bearer ${input:doc4ai-token}"
    assert "--allow-http" in cfgs["claude-desktop"].payload["mcpServers"]["doc4ai"]["args"]
    https = {c.client: c for c in client_configs("https://pub.example/mcp", "doc4ai_pat_X")}
    assert "--allow-http" not in https["claude-desktop"].payload["mcpServers"]["doc4ai"]["args"]


def test_install_links_encode_the_same_payloads():
    from aidoc.mcp.snippets import client_configs, install_links
    links = {l["client"]: l["url"] for l in install_links("http://box:8765/mcp", "doc4ai_pat_X")}
    cur = urlparse(links["cursor"])
    assert cur.scheme == "cursor" and cur.netloc == "anysphere.cursor-deeplink" and cur.path == "/mcp/install"
    q = parse_qs(cur.query)
    assert q["name"] == ["doc4ai"]
    decoded = json.loads(base64.b64decode(q["config"][0]))
    assert decoded == {"url": "http://box:8765/mcp", "headers": {"Authorization": "Bearer doc4ai_pat_X"}}   # one server entry, not the whole file
    vs = links["vscode"]
    assert vs.startswith("vscode:mcp/install?")
    obj = json.loads(unquote(vs.split("?", 1)[1]))
    assert obj["name"] == "doc4ai" and obj["type"] == "http" and obj["inputs"][0]["id"] == "doc4ai-token"
    assert "doc4ai_pat_X" not in vs                                   # VS Code link never carries the token
    assert {c.client for c in client_configs("http://b/mcp") if c.install_link} == {"cursor", "vscode"}
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Build the four payloads once; `render_snippets` maps them to the existing `{client, title, language, text}` dicts (keep byte-identical output: the existing snippet tests pin it); the Cursor link = `cursor://anysphere.cursor-deeplink/mcp/install?name=doc4ai&config=` + `base64(json.dumps(payload["mcpServers"]["doc4ai"]))` (https://cursor.com/docs/mcp/install-links); the VS Code link = `vscode:mcp/install?` + `quote(json.dumps({"name": "doc4ai", **payload["servers"]["doc4ai"], "inputs": payload["inputs"]}))` (https://code.visualstudio.com/api/extension-guides/ai/mcp). `allow_http` defaults to `endpoint_url.startswith("http://")`.
- [ ] **Step 4: Run** `uv run pytest tests/mcp/test_snippets.py -q` → PASS.
- [ ] **Step 5: Commit** `refactor(mcp): client_configs() as the single source for snippets and install links`.

---

### Task A7: `aidoc mcp install`

**Files:** Create `src/aidoc/mcp/install.py`; modify `src/aidoc/cli.py` (`mcp` subcommand group with `install`); test `tests/unit/test_mcp_install.py`.

**Interfaces:**

```python
# install.py
def config_path(client: str, *, scope: str, project: Path | None, home: Path, platform: str, appdata: Path | None) -> Path | None
def merge_config(existing: dict | None, cfg: ClientConfig, *, remove: bool = False) -> dict        # pure
def write_json_atomic(path: Path, data: dict, *, backup: bool = True) -> None                     # tmp + os.replace, one .bak
def read_json_strict(path: Path) -> dict | None                                                   # None if missing; raises InstallError(position) on bad JSON
def install(clients: list[str], *, url: str, token: str, scope: str, project: Path | None, dry_run: bool, remove: bool, run=subprocess.run, env=os.environ, out=print) -> int   # exit code
class InstallError(Exception): code: int
```

- [ ] **Step 1: Write the failing tests**

```python
import json
import sys
from pathlib import Path

import pytest

from aidoc.mcp.snippets import client_configs


def _cfg(client, url="http://box:8765/mcp", token="doc4ai_pat_X"):
    return {c.client: c for c in client_configs(url, token)}[client]


def test_merge_keeps_other_servers_and_removes_only_ours():
    from aidoc.mcp.install import merge_config
    existing = {"mcpServers": {"other": {"command": "x"}}, "unrelated": 1}
    merged = merge_config(existing, _cfg("cursor"))
    assert merged["mcpServers"]["other"] == {"command": "x"} and merged["mcpServers"]["doc4ai"]["url"].endswith("/mcp") and merged["unrelated"] == 1
    assert merge_config(merged, _cfg("cursor"), remove=True) == existing
    vs = merge_config(None, _cfg("vscode"))
    assert vs["servers"]["doc4ai"]["type"] == "http" and vs["inputs"][0]["id"] == "doc4ai-token"
    vs2 = merge_config({"inputs": [{"id": "doc4ai-token", "type": "promptString"}], "servers": {}}, _cfg("vscode"))
    assert len(vs2["inputs"]) == 1                                     # no duplicate input


def test_paths_per_client_and_platform(tmp_path):
    from aidoc.mcp.install import config_path
    home, appdata = tmp_path / "home", tmp_path / "appdata"
    assert config_path("cursor", scope="user", project=None, home=home, platform="win32", appdata=appdata) == home / ".cursor" / "mcp.json"
    assert config_path("cursor", scope="project", project=tmp_path / "p", home=home, platform="win32", appdata=appdata) == tmp_path / "p" / ".cursor" / "mcp.json"
    assert config_path("vscode", scope="project", project=tmp_path / "p", home=home, platform="linux", appdata=None) == tmp_path / "p" / ".vscode" / "mcp.json"
    assert config_path("claude-desktop", scope="user", project=None, home=home, platform="win32", appdata=appdata) == appdata / "Claude" / "claude_desktop_config.json"
    assert config_path("claude-desktop", scope="user", project=None, home=home, platform="darwin", appdata=None) == home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    assert config_path("claude-desktop", scope="user", project=None, home=home, platform="linux", appdata=None) == home / ".config" / "Claude" / "claude_desktop_config.json"
    assert config_path("claude-code", scope="user", project=None, home=home, platform="linux", appdata=None) is None    # driven via the CLI


def test_broken_json_is_never_overwritten(tmp_path, monkeypatch):
    from aidoc.mcp.install import install
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.setenv("USERPROFILE", str(tmp_path)); monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    f = tmp_path / ".cursor" / "mcp.json"; f.parent.mkdir(); f.write_text('{"mcpServers": {"a": 1},}', encoding="utf-8")   # trailing comma
    out = []
    code = install(["cursor"], url="http://b/mcp", token="doc4ai_pat_X", scope="user", project=None, dry_run=False, remove=False, out=out.append)
    assert code == 2 and f.read_text(encoding="utf-8") == '{"mcpServers": {"a": 1},}' and any("line 1" in s for s in out)


def test_write_is_atomic_with_backup_and_dry_run_writes_nothing(tmp_path, monkeypatch):
    from aidoc.mcp.install import install
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.setenv("USERPROFILE", str(tmp_path)); monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    f = tmp_path / ".cursor" / "mcp.json"; f.parent.mkdir(); f.write_text('{"mcpServers": {"a": {"command": "x"}}}', encoding="utf-8")
    out = []
    assert install(["cursor"], url="http://b/mcp", token="doc4ai_pat_X", scope="user", project=None, dry_run=True, remove=False, out=out.append) == 0
    assert "doc4ai" not in f.read_text() and any("doc4ai_pat_X" in s for s in out)
    assert install(["cursor"], url="http://b/mcp", token="doc4ai_pat_X", scope="user", project=None, dry_run=False, remove=False, out=out.append) == 0
    data = json.loads(f.read_text(encoding="utf-8"))
    assert set(data["mcpServers"]) == {"a", "doc4ai"} and list(f.parent.glob("mcp.json.bak.*"))
    assert install(["cursor"], url="http://b/mcp", token="x", scope="user", project=None, dry_run=False, remove=True, out=out.append) == 0
    assert "doc4ai" not in json.loads(f.read_text())["mcpServers"]


def test_claude_code_runs_the_cli_or_prints_it(tmp_path):
    from aidoc.mcp.install import install
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    out = []
    assert install(["claude-code"], url="http://b/mcp", token="doc4ai_pat_X", scope="local", project=None, dry_run=False, remove=False,
                   run=fake_run, env={"PATH": ""}, out=out.append) == 3           # no `claude` on PATH → print the command, exit 3
    assert not calls and any("claude mcp add --transport http doc4ai http://b/mcp" in s for s in out)
    with_claude = {"PATH": str(tmp_path)}
    exe = tmp_path / ("claude.cmd" if sys.platform == "win32" else "claude"); exe.write_text(""); exe.chmod(0o755)
    assert install(["claude-code"], url="http://b/mcp", token="doc4ai_pat_X", scope="local", project=None, dry_run=False, remove=False,
                   run=fake_run, env=with_claude, out=out.append) == 0
    assert calls[-1][:3] == [str(exe), "mcp", "add"] and "-s" in calls[-1] and "local" in calls[-1]


def test_token_never_in_vscode_file_and_all_continues_on_failure(tmp_path, monkeypatch):
    from aidoc.mcp.install import install
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.setenv("USERPROFILE", str(tmp_path)); monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    (tmp_path / ".cursor").mkdir(); (tmp_path / ".cursor" / "mcp.json").write_text("{broken", encoding="utf-8")
    out = []
    code = install(["vscode", "cursor"], url="http://b/mcp", token="doc4ai_pat_X", scope="project", project=tmp_path, dry_run=False, remove=False,
                   env={"PATH": ""}, out=out.append)
    assert code == 2
    vs = (tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8")
    assert "doc4ai_pat_X" not in vs and "${input:doc4ai-token}" in vs
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `install()` resolves clients (`all` → the four), for each: `claude-code` → `shutil.which("claude", path=env["PATH"])`; found → `run([exe, "mcp", "add", "--transport", "http", alias, url, "--header", f"Authorization: Bearer {token}", "-s", scope])` (remove → `[exe, "mcp", "remove", alias, "-s", scope]`); not found → print the command and remember exit 3. Others → `config_path` → `read_json_strict` (`json.JSONDecodeError` → message with `line`/`column`, exit 2, continue) → `merge_config` → dry-run prints path + JSON, else `write_json_atomic` (one `.bak.<unix ts>`; keep only the newest backup) → print the next-step hint (text from `ClientConfig.title`/a small dict). Exit code = max over clients. CLI: `aidoc mcp install --client … [--url] [--token | --token-stdin] [--scope local|project|user] [--project DIR] [--dry-run] [--remove]`; token resolution per spec §3.2 (`--token`, stdin, `getpass`); `--url` default from `data/aidoc.lock` + `endpoint_urls()` when a server is running, else error exit 2. `--help` text warns about shell history.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_mcp_install.py tests/unit/test_cli*.py -q` → PASS; manual: `uv run aidoc mcp install --client cursor --dry-run --url http://127.0.0.1:8765/mcp --token x`.
- [ ] **Step 5: Commit** `feat(cli): aidoc mcp install writes Claude Code, Cursor, VS Code and Claude Desktop configs`.

---

### Task A8: Install links in the admin API and UI; README

**Files:** Modify `src/aidoc/server/api/mcp.py` (`config_snippets()` and the create/rotate responses add `install_links: [{client, url}]`; the create/rotate response uses the real token for the Cursor link), `web/src/api/types.ts`, `web/src/features/mcp/ConfigSnippets.tsx` (an 「安裝到 Cursor」 / 「安裝到 VS Code」 button next to the matching tab; the Cursor button is enabled only in the one-time reveal dialog where the real token exists — elsewhere a tooltip 「建立 token 時才能一鍵安裝」), `README.md` (MCP section: a line about `aidoc mcp install` and the two install-link badges with placeholder tokens are **not** rendered — links need a real token; show the command instead); tests `tests/mcp/test_admin_tokens.py` (append: `install_links` present; Cursor link decodes to the real token in the create response and to `<YOUR_TOKEN>` in `config-snippets`), Vitest `ConfigSnippets.install.test.tsx`.

- [ ] **Step 1–5:** RED → implement → GREEN (`uv run pytest tests/mcp/test_admin_tokens.py tests/mcp/test_snippets.py -q`, `pnpm --dir web test`) → commit `feat(web): one-click install links for Cursor and VS Code in the MCP page`.

---

### Task A9: Claude Desktop `.mcpb`

**Files:** Create `mcpb/package.json` (deps: `mcp-remote` pinned), `mcpb/server/index.js`, `mcpb/icon.png` (512×512, generated from the web favicon by `scripts/build_mcpb.py` or committed), `scripts/build_mcpb.py` (writes `mcpb/manifest.json` from `aidoc.__version__`, the tool table from `aidoc.mcp.middleware.TOOL_SCOPES` + descriptions, then runs `npx -y @anthropic-ai/mcpb pack mcpb dist/doc4ai-<version>.mcpb`, `mcpb sign --self-signed` (gate G5), `mcpb verify`, prints sha256), `.gitignore` (`mcpb/node_modules/`, `dist/`); tests `tests/mcp/test_mcpb.py`.

**`manifest.json` (generated; shape per spec §3.3):**

```json
{"manifest_version": "0.3", "name": "doc4ai", "display_name": "Doc4AI Studio", "version": "<aidoc.__version__>",
 "description": "Search and read documents converted by your Doc4AI Studio server (remote MCP over HTTP; needs a personal access token).",
 "long_description": "…", "author": {"name": "<user>"}, "homepage": "https://github.com/<user>/doc4ai-studio", "license": "AGPL-3.0", "icon": "icon.png",
 "server": {"type": "node", "entry_point": "server/index.js",
            "mcp_config": {"command": "node", "args": ["${__dirname}/server/index.js"],
                           "env": {"DOC4AI_URL": "${user_config.server_url}", "DOC4AI_TOKEN": "${user_config.token}", "DOC4AI_ALLOW_HTTP": "${user_config.allow_http}"}}},
 "user_config": {"server_url": {"type": "string", "title": "Doc4AI Studio MCP URL", "description": "e.g. http://127.0.0.1:8765/mcp", "required": true, "default": "http://127.0.0.1:8765/mcp"},
                 "token": {"type": "string", "title": "Personal access token", "description": "MCP → Tokens in the Doc4AI web UI", "sensitive": true, "required": true},
                 "allow_http": {"type": "boolean", "title": "Allow plain HTTP (tailnet / LAN only)", "default": true}},
 "tools": [{"name": "search_library", "description": "…"}, …],
 "compatibility": {"claude_desktop": ">=1.0.0", "platforms": ["darwin", "win32", "linux"], "runtimes": {"node": ">=18"}},
 "keywords": ["documents", "pdf", "ocr", "markdown", "rag"]}
```

**`server/index.js`:** read `DOC4AI_URL`, `DOC4AI_TOKEN`, `DOC4AI_ALLOW_HTTP`; refuse to start (stderr + exit 2) when the URL is `http://` and `allow_http !== "true"`; otherwise `require("mcp-remote")`'s programmatic entry (or spawn its bin with `--header Authorization:Bearer <token>` via env substitution, `--allow-http` when permitted, `--transport http-only`) and pipe stdio.

- [ ] **Step 1: Write the failing tests**

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_build_script_generates_a_valid_manifest(tmp_path):
    from scripts.build_mcpb import write_manifest
    from aidoc import __version__
    from aidoc.mcp.middleware import TOOL_SCOPES
    m = write_manifest(tmp_path / "manifest.json")
    assert m["manifest_version"] == "0.3" and m["version"] == __version__ and m["server"]["type"] == "node"
    assert set(m["user_config"]) == {"server_url", "token", "allow_http"} and m["user_config"]["token"]["sensitive"] is True
    assert m["server"]["mcp_config"]["env"]["DOC4AI_TOKEN"] == "${user_config.token}"
    assert "${user_config.token}" not in json.dumps(m["server"]["mcp_config"]["args"])           # token via env, never args
    assert {t["name"] for t in m["tools"]} == set(TOOL_SCOPES) - {"delete_document"}
    assert m["compatibility"]["runtimes"] == {"node": ">=18"}


@pytest.mark.skipif(shutil.which("npx") is None, reason="needs Node")
def test_mcpb_validate_and_pack(tmp_path):
    from scripts.build_mcpb import write_manifest
    write_manifest(ROOT / "mcpb" / "manifest.json")
    r = subprocess.run(["npx", "-y", "@anthropic-ai/mcpb", "validate", str(ROOT / "mcpb" / "manifest.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "doc4ai.mcpb"
    r = subprocess.run(["npx", "-y", "@anthropic-ai/mcpb", "pack", str(ROOT / "mcpb"), str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and out.exists() and out.stat().st_size < 30 * 1024 * 1024


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_index_js_refuses_http_without_allow(tmp_path):
    r = subprocess.run(["node", str(ROOT / "mcpb" / "server" / "index.js")], capture_output=True, text=True, timeout=20,
                       env={"PATH": "", "DOC4AI_URL": "http://127.0.0.1:1/mcp", "DOC4AI_TOKEN": "t", "DOC4AI_ALLOW_HTTP": "false"})
    assert r.returncode == 2 and "allow_http" in r.stderr
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** (`pnpm --dir mcpb install`; `mcpb/.npmrc` `node-linker=hoisted` so `node_modules` is a plain tree for packing). Tool descriptions for the manifest come from the registered tools (`build_mcp(ctx)` is heavy; instead keep a small `TOOL_SUMMARIES` dict in `aidoc.mcp.middleware` next to `TOOL_SCOPES` and have Task C6/B tests assert it stays in sync with `mcp.list_tools()` names).
- [ ] **Step 4: Run** → PASS; manual: install `dist/doc4ai-<ver>.mcpb` into Claude Desktop on the Windows host (Settings → Extensions → Advanced → Install Extension), fill URL + token, run one `read_document`. Record the Desktop version and whether the self-signed warning blocked anything.
- [ ] **Step 5: Commit** `feat(mcpb): Claude Desktop extension bundle (Node + mcp-remote) generated from the tool table`.

---

### Task A10: `server.json` and the Registry release procedure

**Files:** Create `server.json` (spec §3.4; `name` from gate G3 — until answered, the placeholder `io.github.<user>/doc4ai` with a test that fails while the placeholder remains **only when** `DOC4AI_RELEASE=1`), `docs/release/registry.md`, `scripts/build_mcpb.py` (prints the `fileSha256` line to paste), `.github/workflows/registry.yml` (manual dispatch; `mcp-publisher login github-oidc` + `publish`; gated like the image push); tests `tests/unit/test_server_json.py`.

- [ ] **Step 1: Write the failing tests**

```python
import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SJ = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_server_json_shape():
    from aidoc import __version__
    assert SJ["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    assert SJ["name"].startswith("io.github.") and SJ["name"].endswith("/doc4ai") and SJ["version"] == __version__
    kinds = {p["registryType"] for p in SJ["packages"]}
    assert kinds == {"oci", "mcpb"} and "remotes" not in SJ                                  # gate G3 default: no public URL listed
    oci = [p for p in SJ["packages"] if p["registryType"] == "oci"][0]
    assert oci["transport"] == {"type": "streamable-http", "url": "http://localhost:8765/mcp"}
    assert any(e["name"] == "AIDOC_TOKEN" and e["isSecret"] for e in oci["environmentVariables"])
    mcpb = [p for p in SJ["packages"] if p["registryType"] == "mcpb"][0]
    assert mcpb["identifier"].startswith("https://github.com/") and "/releases/download/" in mcpb["identifier"] and len(mcpb["fileSha256"]) == 64
    assert "pypi" not in kinds                                                                   # uvx aidoc cannot run (spec 3.4)


def test_server_json_validates_against_the_published_schema():
    schema_file = ROOT / "tests" / "fixtures" / "registry_server_schema_2025-12-11.json"   # snapshot committed in this task
    jsonschema.validate(SJ, json.loads(schema_file.read_text(encoding="utf-8")))


@pytest.mark.skipif(shutil.which("mcp-publisher") is None, reason="needs mcp-publisher")
def test_mcp_publisher_validate():
    r = subprocess.run(["mcp-publisher", "validate", str(ROOT / "server.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dockerfile_label_matches_server_json_name():
    df = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert f'io.modelcontextprotocol.server.name="{SJ["name"]}"' in df
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Write** `server.json`, download the schema snapshot (`curl -o tests/fixtures/registry_server_schema_2025-12-11.json https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`), write `docs/release/registry.md` (install `mcp-publisher`, `validate`, `login github`, `publish`, verify with `curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=<name>"`, bump + republish, `status deprecated`; note immutability and the OCI label / mcpb sha requirements), the workflow.
- [ ] **Step 4: Run** → PASS (publisher test skipped unless installed).
- [ ] **Step 5: Commit** `chore(registry): server.json, schema snapshot and release procedure` — the actual `mcp-publisher publish` is run by the user after gate G3 (record date/result in deviations).

---

### Task A11: Acceptance run and deviations

Manual rows (record pass/fail/not-run, versions, sizes, timings in the deviations section):

1. Windows host, Docker Desktop (WSL 2): `docker compose -f compose.yaml -f compose.cpu.yaml up -d` → healthy; Web UI reachable; `/api/system` engines; convert the sample PDF through the UI inside the container; `docker compose down/up` keeps the Library (volume).
2. Same host, `compose.yaml` (cuda) with `--gpus all`: `/api/system` `cuda: true`, `sm_120`; convert a scanned PDF with MinerU.
3. `uv run aidoc mcp install --client all --url http://127.0.0.1:8765/mcp --token-stdin` (host server running): Claude Code `claude mcp list` shows `doc4ai`; Cursor and Claude Desktop pick up the entries after restart; VS Code prompts for the token on Start.
4. Click 「安裝到 Cursor」 from a freshly created token's reveal dialog → Cursor opens the install prompt → `tools/list` ok in 呼叫紀錄.
5. `.mcpb` install on Claude Desktop (Task A9 Step 4).
6. `mcp-publisher validate server.json` on the host; `publish` only after gate G3 → search result URL recorded.

## Phase acceptance (4a)

- [ ] `uv run pytest -m "not slow" -q` green; `ruff check src tests` clean; `pnpm --dir web test`, `build`, `e2e` green.
- [ ] `docker build --target cpu` and `--target cuda` succeed on the host; sizes recorded; CI builds and tests the cpu image.
- [ ] Both compose files start healthy; models persist across `down/up`; a missing `AIDOC_TOKEN` with a public bind exits 2 with a clear message.
- [ ] `envs/*/uv.lock` cover both variants (or the fallback is documented); `uv run aidoc setup all` on the Windows host still yields `torch 2.11.0+cu128` and `sm_120`.
- [ ] `aidoc mcp install` writes/removes all four clients idempotently; broken JSON is never overwritten; tokens never land in the VS Code file; `render_snippets()` output unchanged byte for byte.
- [ ] `.mcpb` validates, packs (< 30 MB), installs into Claude Desktop and reads a document.
- [ ] `server.json` validates against the schema snapshot and `mcp-publisher validate`; the Dockerfile label equals its `name`.
- [ ] README: Docker quick start, `aidoc mcp install`, `.mcpb` download, Registry mention; `docs/deploy/docker.md`, `docs/release/registry.md` exist.
- [ ] Gates G3–G5 answered and recorded.

## Implementation notes / deviations

(Filled in while executing.)
