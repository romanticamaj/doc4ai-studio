# Engine environments

Each engine lives in its own uv project (`envs/<engine>/pyproject.toml` + `uv.lock`) with its own
`.venv`, so torch / model stacks never touch the main `aidoc` env. `uv run aidoc setup <engine>`
runs `uv sync --project envs/<engine>`, downloads models into `data/models/`, runs the engine's
self-check and writes `envs/<engine>/.ready`.

## Regenerating locks

    uv lock --project envs/docling      # likewise envs/mineru, envs/markitdown
    uv lock --project envs/docling --upgrade-package docling

Commit the resulting `uv.lock`; never commit `.venv`.

## Why cu128

The host GPU is an RTX 5070 Ti (Blackwell, sm_120). sm_120 kernels ship in torch >= 2.7 wheels built
for CUDA 12.8 (`https://download.pytorch.org/whl/cu128`). The `cu126` wheels and the PyPI CPU wheel do
not contain sm_120, so the index is declared `explicit = true` and `torch` / `torchvision` are pinned to
it through `[tool.uv.sources]` for win32/linux. The self-check requires `torch.cuda.is_available()` and
`"sm_120" in torch.cuda.get_arch_list()`.

## Switching to cu130

Change the index URL to `https://download.pytorch.org/whl/cu130`, raise the torch bound to `>=2.12`
(first release with cu130 wheels for all platforms), make sure the NVIDIA driver is R580 or newer, then
`uv lock --project envs/<e>` and `uv run aidoc setup <e>`.

## MarkItDown extras

`markitdown[all]==0.1.8` cannot be resolved (it requires the pre-release
`azure-ai-contentunderstanding>=1.2.0b1`), so the env lists the local extras explicitly:
`docx, pptx, xlsx, xls, pdf, outlook, audio-transcription`. The Azure extras are not used.

## Licences

Internal tool. MinerU 4.x uses a custom licence (Apache-2.0 plus extra conditions: commercial licence above a
size threshold, attribution for online services; AGPL-3.0 before 3.1); PyMuPDF (main program) is AGPL-3.0 with a
commercial option; Docling and MarkItDown are MIT. Re-check before offering this as an external service (spec §2).

## Installed on the reference host (2026-09-24, RTX 5070 Ti, driver 591.86)

| env | engine version | torch | `torch.cuda.get_arch_list()` |
|---|---|---|---|
| docling | docling 2.130.0 (easyocr 1.7.2) | 2.11.0+cu128 (CUDA 12.8) | sm_75 sm_80 sm_86 sm_90 sm_100 sm_120 |
| mineru | mineru 4.0.7 | 2.11.0+cu128 (CUDA 12.8) | sm_75 sm_80 sm_86 sm_90 sm_100 sm_120 |
| markitdown | markitdown 0.1.8 | — | — |
