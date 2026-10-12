import { describe, expect, it } from "vitest";
import { engineState } from "../EngineCard";

describe("engineState for the VLM engine", () => {
  it("says no backend is chosen", () => {
    expect(engineState({ installed: false, ready: false, backend: "none" }, "vlm")).toEqual({ label: "尚未選擇後端", status: "queued" });
  });
  it("tells remote from local endpoints when ready", () => {
    expect(engineState({ installed: true, ready: true, backend: "openai", local_endpoint: false }, "vlm")).toEqual({ label: "就緒（遠端）", status: "done" });
    expect(engineState({ installed: true, ready: true, backend: "openai", local_endpoint: true }, "vlm").label).toBe("就緒（本機端點）");
  });
  it("shows a configured endpoint without consent as not enabled", () => {
    expect(engineState({ installed: true, ready: false, backend: "openai", remote_enabled: false }, "vlm")).toEqual({ label: "已設定未啟用", status: "low" });
  });
  it("marks the local backend as unsupported on this platform", () => {
    expect(engineState({ installed: false, ready: false, backend: "local", local_supported: false }, "vlm").label).toBe("本機後端尚未支援");
  });
  it("leaves the other engines unchanged", () => {
    expect(engineState({ installed: true, ready: true })).toEqual({ label: "就緒", status: "done" });
  });
});
