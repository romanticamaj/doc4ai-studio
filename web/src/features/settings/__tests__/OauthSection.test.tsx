import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OauthSection } from "@/features/settings/OauthSection";

afterEach(() => vi.restoreAllMocks());

const oauth = { enabled: true, allow_cimd: true, allow_dcr: true, access_ttl_min: 60, refresh_ttl_days: 30, refresh_max_days: 90,
  code_ttl_s: 300, consent_ttl_s: 600, dcr_per_hour_per_ip: 20 };

function setup() {
  const puts: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    puts.push(JSON.parse(String(init?.body ?? "{}")));
    return new Response(JSON.stringify({ settings: { oauth } }), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OauthSection original={oauth} />
    </QueryClientProvider>,
  );
  return puts;
}

test("fields, inline range error, and a save that sends only the changes", async () => {
  const user = userEvent.setup();
  const puts = setup();
  expect(screen.getByRole("heading", { name: "OAuth 授權" })).toBeInTheDocument();
  for (const l of ["啟用", "允許自動註冊（DCR）", "允許 CIMD"]) expect(screen.getByRole("switch", { name: l })).toBeChecked();
  const minutes = screen.getByLabelText("access token 分鐘");
  expect(minutes).toHaveValue(60);
  await user.clear(minutes);
  await user.type(minutes, "2");
  expect(screen.getByRole("alert")).toHaveTextContent("請輸入 5–1440 的整數");
  expect(screen.getByRole("button", { name: "儲存" })).toBeDisabled();
  await user.clear(minutes);
  await user.type(minutes, "15");
  expect(screen.queryByRole("alert")).toBeNull();
  await user.click(screen.getByRole("switch", { name: "允許自動註冊（DCR）" }));
  await user.click(screen.getByRole("button", { name: "儲存" }));
  await waitFor(() => expect(puts).toEqual([{ oauth: { allow_dcr: false, access_ttl_min: 15 } }]));
});

test("refresh days cannot pass the absolute limit", async () => {
  const user = userEvent.setup();
  setup();
  const days = screen.getByLabelText("refresh 天數");
  await user.clear(days);
  await user.type(days, "120");
  expect(screen.getByRole("alert")).toHaveTextContent("請輸入 1–90 的整數");
});
