import { describe, expect, it } from "vitest";
import { isLoopbackUrl } from "../isLoopbackUrl";

describe("isLoopbackUrl", () => {
  it("recognises loopback hosts", () => {
    expect(isLoopbackUrl("http://127.0.0.1:1234/v1")).toBe(true);
    expect(isLoopbackUrl("http://localhost:11434/v1")).toBe(true);
    expect(isLoopbackUrl("http://[::1]:8080/v1")).toBe(true);
  });
  it("treats tailnet and public hosts as remote", () => {
    expect(isLoopbackUrl("http://100.64.0.7:1234/v1")).toBe(false);
    expect(isLoopbackUrl("https://api.openai.com/v1")).toBe(false);
  });
  it("returns null for garbage", () => {
    expect(isLoopbackUrl("not a url")).toBeNull();
  });
});
