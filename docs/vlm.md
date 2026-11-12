# VLM OCR (optional, off by default)

A fourth engine, `vlm`, sends page images to any **OpenAI-compatible vision endpoint** (Ollama or LM Studio on this
computer, vLLM / SGLang you host yourself, or a cloud API). It is only used where everything else failed: it is the
**last** engine of every PDF / image route (after MarkItDown) and the **last** per-page repair engine (after Docling
and MinerU). With `[vlm] backend = "none"` (the default) nothing changes.

```toml
[vlm]
backend = "openai"
base_url = "http://127.0.0.1:11434/v1"   # Ollama on this computer
model = "gemma4:12b"
remote_enabled = true                    # consent: page images go to base_url
allow_no_key = true                      # local servers need no key; otherwise set DOC4AI_VLM_API_KEY
```

- **Privacy:** pages are sent only after `remote_enabled = true` (Settings → VLM OCR has the consent switch; an endpoint
  that is not on this computer is shown in red). Every use is logged (`vlm(openai): sending N page image(s) to <host>`)
  and recorded in the sidecar (`vlm.endpoint_host`, `vlm.pages_sent`). `doc4ai convert --no-vlm-remote` turns it off for
  one run. Keep the key in the `DOC4AI_VLM_API_KEY` environment variable; a key in `doc4ai.toml` is masked by the API.
- **Limits:** a daily page budget (`remote_daily_page_budget`, default 500) and a per-document page cap
  (`remote_max_pages_per_doc`, default 200; per-page repair is limited by the budget only).
- `doc4ai setup vlm` (or 「測試連線」 in Settings) sends one built-in test image and checks the answer.
- The VLM engine **does not extract images** (no layout boxes): pictures become `<!-- image missing -->`.
- The **local backend** (a llama.cpp server with PaddleOCR-VL-1.6 managed by doc4ai) is **not available yet**: the
  feasibility spike did not pass its acceptance rule on this hardware.
