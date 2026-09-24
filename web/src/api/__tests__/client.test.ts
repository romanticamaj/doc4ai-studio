import { afterEach, expect, test, vi } from "vitest";
import { api, setToken, eventsUrl, getToken } from "../client";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

test("adds bearer token and parses envelope", async () => {
  setToken("abc");
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify({ workspace: "default", jobs: [] }), { status: 200 }));
  const r = await api.get<{ jobs: unknown[] }>("/api/jobs");
  expect(r.jobs).toEqual([]);
  expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer abc" });
  expect(eventsUrl()).toBe("/api/events?token=abc");
});

test("throws ApiError with body", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ workspace: "default", error: "bad_offset", received: 8 }), { status: 409 }),
  );
  await expect(api.putRaw("/api/uploads/x?offset=0", new Uint8Array(1))).resolves.toHaveProperty("status", 409);
  await expect(api.post("/api/jobs", {})).rejects.toMatchObject({ status: 409, body: { error: "bad_offset", received: 8 } });
});

test("non-JSON error bodies fall back to http_<status>", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("Internal Server Error", { status: 500 }));
  await expect(api.get("/api/system")).rejects.toMatchObject({ status: 500, body: { error: "http_500" } });
});

test("no token: no Authorization header and plain events url", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  await api.get("/api/system");
  expect(getToken()).toBe("");
  expect((fetchMock.mock.calls[0][1] as RequestInit).headers).not.toHaveProperty("Authorization");
  expect(eventsUrl()).toBe("/api/events");
});
