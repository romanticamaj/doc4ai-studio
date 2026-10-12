import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VlmBackendForm } from "../VlmBackendForm";
import type { VlmSettings } from "@/api/types";

const base: VlmSettings = { backend: "openai", remote_enabled: false, base_url: "", model: "", api_key: "", api_key_env: "AIDOC_VLM_API_KEY",
  allow_no_key: false, request_timeout_s: 120, max_concurrent: 2, max_output_tokens: 4096, remote_max_pages_per_doc: 200,
  remote_daily_page_budget: 500, use_as_fallback: true, use_for_repair: true, dpi: 150, max_image_px: 2000,
  local_preset: "paddleocr-vl-1.6", local_dpi: 200, local_max_image_px: 1400, local_ctx_size: 8192, local_gguf: "", local_mmproj: "", local_prompt_style: "" };

describe("VlmBackendForm", () => {
  it("keeps the consent switch disabled until base_url and model are set", () => {
    render(<VlmBackendForm settings={base} info={{ installed: false, ready: false, backend: "openai", local_supported: true }} onSave={vi.fn()} />);
    expect(screen.getByRole("switch", { name: /我了解/ })).toBeDisabled();
  });
  it("shows a red warning for a remote host and an info note for loopback", () => {
    const { rerender } = render(<VlmBackendForm settings={{ ...base, base_url: "https://api.example.com/v1", model: "m" }} info={{ installed: false, ready: false, backend: "openai" }} onSave={vi.fn()} />);
    expect(screen.getByText(/會傳到 api.example.com/)).toHaveAttribute("data-tone", "warn");
    rerender(<VlmBackendForm settings={{ ...base, base_url: "http://127.0.0.1:1234/v1", model: "m" }} info={{ installed: false, ready: false, backend: "openai" }} onSave={vi.fn()} />);
    expect(screen.getByText(/端點在本機/)).toHaveAttribute("data-tone", "info");
  });
  it("never echoes a stored key and only sends a typed one", () => {
    const onSave = vi.fn();
    render(<VlmBackendForm settings={{ ...base, base_url: "http://127.0.0.1:1/v1", model: "m", api_key: "***" }} info={{ installed: false, ready: false, backend: "openai" }} onSave={onSave} />);
    const key = screen.getByLabelText("API key") as HTMLInputElement;
    expect(key.value).toBe("");
    expect(key.placeholder).toBe("***");
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(onSave.mock.calls[0][0].vlm).not.toHaveProperty("api_key");
  });
  it("hides local when the platform does not support it", () => {
    render(<VlmBackendForm settings={base} info={{ installed: false, ready: false, backend: "openai", local_supported: false }} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole("combobox", { name: "後端" }));
    expect(screen.getByRole("option", { name: /local/ })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("VlmBackendForm consent and endpoint changes", () => {
  it("turns consent off when the endpoint host changes", () => {
    const onSave = vi.fn();
    render(<VlmBackendForm settings={{ ...base, base_url: "http://127.0.0.1:1/v1", model: "m", remote_enabled: true }} info={{ installed: true, ready: true, backend: "openai" }} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("base_url"), { target: { value: "https://api.example.com/v1" } });
    expect(screen.getByRole("switch", { name: /我了解/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(onSave.mock.calls[0][0].vlm.remote_enabled).toBe(false);
  });
});
