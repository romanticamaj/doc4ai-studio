import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CreateTokenDialog } from "@/features/mcp/CreateTokenDialog";

afterEach(() => vi.restoreAllMocks());

function setup(allowNoExpiry = false) {
  const created = { token: "doc4ai_pat_SECRET", record: { id: "t1", name: "laptop", prefix: "doc4ai_pat_SECR", scopes: ["doc4ai:read", "doc4ai:manage"],
    status: "active", calls_24h: 0, errors_24h: 0, created_at: 1, expires_at: 2, revoked_at: null, revoked_reason: null, rotated_from: null,
    rate_limit_per_min: null, last_used_at: null, last_used_ip: null, last_client: null, note: null }, snippets: [] };
  const calls: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/settings")) return new Response(JSON.stringify({ settings: { mcp: { allow_no_expiry: allowNoExpiry, default_token_ttl_days: 90 } } }), { status: 200 });
    calls.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify(created), { status: 201 });
  });
  const onCreated = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CreateTokenDialog open onOpenChange={() => {}} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return { onCreated, calls };
}

test("read is preselected and locked; risky scopes show a warning; the body matches the form", async () => {
  const { onCreated, calls } = setup();
  const read = screen.getByRole("checkbox", { name: /doc4ai:read/ });
  expect(read).toBeChecked();
  expect(read).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: /doc4ai:manage/ }));
  expect(screen.getByText(/高風險/)).toBeInTheDocument();
  expect(screen.queryByRole("radio", { name: "永不過期" })).toBeNull();
  await userEvent.type(screen.getByLabelText("名稱"), "laptop");
  await userEvent.click(screen.getByRole("radio", { name: "180 天" }));
  await userEvent.click(screen.getByRole("button", { name: "建立" }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  expect(calls[0]).toEqual({ name: "laptop", scopes: ["doc4ai:read", "doc4ai:manage"], expires_in_days: 180 });
  expect(onCreated.mock.calls[0][0].token).toBe("doc4ai_pat_SECRET");
});

test("no-expiry option appears only when the server allows it", async () => {
  setup(true);
  await waitFor(() => expect(screen.getByRole("radio", { name: "永不過期" })).toBeInTheDocument());
});

test("empty name cannot be submitted", async () => {
  const { onCreated } = setup();
  await userEvent.click(screen.getByRole("button", { name: "建立" }));
  expect(onCreated).not.toHaveBeenCalled();
});
