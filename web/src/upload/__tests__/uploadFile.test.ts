import { afterEach, expect, test } from "vitest";
import { uploadFile } from "../uploadFile";
import { ApiError } from "@/api/client";

afterEach(() => sessionStorage.clear());

function fakeApi(script: Array<{ status: number; body?: any; throw?: boolean }>, opts: { size: number; headStatus?: number }) {
  let received = 0;
  const calls: any[] = [];
  return {
    calls,
    post: async (path: string, body: any) => {
      calls.push(["post", path, body]);
      return { upload_id: "u1", chunk_size: 4, received: 0 };
    },
    head: async (path: string) => {
      calls.push(["head", path]);
      if (opts.headStatus === 404) throw new ApiError(404, { error: "not_found" });
      return new Headers({ "Upload-Offset": String(received), "Upload-Length": String(opts.size) });
    },
    putRaw: async (path: string, bytes: Blob) => {
      const step = script.shift() ?? { status: 200 };
      calls.push(["put", path, bytes.size]);
      if (step.throw) throw new TypeError("network");
      if (step.status === 200) {
        received += bytes.size;
        return new Response(JSON.stringify({ received, status: received === opts.size ? "complete" : "receiving" }), { status: 200 });
      }
      return new Response(JSON.stringify({ error: "bad_offset", received, ...(step.body ?? {}) }), { status: step.status });
    },
  };
}

const file = () => new File([new Uint8Array(10)], "a.pdf", { lastModified: 1 });

test("uploads in chunks and completes", async () => {
  const api = fakeApi([], { size: 10 });
  const id = await uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any, onProgress: () => {} });
  expect(id).toBe("u1");
  expect(api.calls.filter((c) => c[0] === "put").map((c) => c[1])).toEqual([
    "/api/uploads/u1?offset=0",
    "/api/uploads/u1?offset=4",
    "/api/uploads/u1?offset=8",
  ]);
});

test("resumes after a network error using HEAD", async () => {
  const api = fakeApi([{ status: 200 }, { status: 0, throw: true }], { size: 10 });
  await uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any, retryDelayMs: 0 });
  const puts = api.calls.filter((c) => c[0] === "put").map((c) => c[1]);
  expect(puts).toEqual(["/api/uploads/u1?offset=0", "/api/uploads/u1?offset=4", "/api/uploads/u1?offset=4", "/api/uploads/u1?offset=8"]);
  expect(api.calls.some((c) => c[0] === "head")).toBe(true);
});

test("409 adopts server offset", async () => {
  const api = fakeApi([{ status: 409, body: { received: 4 } }], { size: 10 });
  await uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any });
  const puts = api.calls.filter((c) => c[0] === "put").map((c) => c[1]);
  expect(puts[0]).toBe("/api/uploads/u1?offset=0");
  expect(puts[1]).toBe("/api/uploads/u1?offset=4");
});

test("reuses upload id from sessionStorage after reload", async () => {
  sessionStorage.setItem("aidoc_upload:a.pdf:10:1", "u1");
  const api = fakeApi([], { size: 10 });
  await uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any });
  expect(api.calls[0][0]).toBe("head");
  expect(api.calls.some((c) => c[0] === "post")).toBe(false);
  expect(sessionStorage.getItem("aidoc_upload:a.pdf:10:1")).toBeNull();
});

test("a resumed upload does not need the checksum; an unknown one is created anew", async () => {
  sessionStorage.setItem("aidoc_upload:a.pdf:10:1", "gone");
  const api = fakeApi([], { size: 10, headStatus: 404 });
  let hashed = 0;
  await uploadFile(file(), { sha256: async () => (hashed++, "x".repeat(64)), chunkSize: 4, api: api as any });
  expect(api.calls.map((c) => c[0]).slice(0, 2)).toEqual(["head", "post"]);
  expect(hashed).toBe(1);
});

test("a final chunk whose response was lost counts as done when the server says the upload is complete", async () => {
  const api = fakeApi([], { size: 10 });
  let n = 0;
  const putRaw = api.putRaw;
  api.putRaw = async (path: string, bytes: Blob) => {
    n += 1;
    if (n === 3) {
      await putRaw(path, bytes); // the server stored it...
      throw new TypeError("network"); // ...but the response never arrived
    }
    if (n === 4) return new Response(JSON.stringify({ error: "upload_not_receiving", status: "complete", received: 10 }), { status: 409 });
    return putRaw(path, bytes);
  };
  // HEAD after the error reports 10/10: nothing left to send, so resolve without another PUT
  const id = await uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any, retryDelayMs: 0 });
  expect(id).toBe("u1");
});

test("409 upload_not_receiving with status complete is success", async () => {
  sessionStorage.setItem("aidoc_upload:a.pdf:10:1", "u1");
  const api = {
    head: async () => new Headers({ "Upload-Offset": "8", "Upload-Length": "10", "Upload-Status": "receiving" }),
    post: async () => ({ upload_id: "u1", chunk_size: 4, received: 0 }),
    putRaw: async () => new Response(JSON.stringify({ error: "upload_not_receiving", status: "complete", received: 10 }), { status: 409 }),
  };
  await expect(uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any })).resolves.toBe("u1");
});

test("409 upload_not_receiving for a consumed upload throws and forgets it", async () => {
  sessionStorage.setItem("aidoc_upload:a.pdf:10:1", "u1");
  const api = {
    head: async () => new Headers({ "Upload-Offset": "8", "Upload-Length": "10", "Upload-Status": "receiving" }),
    post: async () => ({ upload_id: "u1", chunk_size: 4, received: 0 }),
    putRaw: async () => new Response(JSON.stringify({ error: "upload_not_receiving", status: "consumed", received: 10 }), { status: 409 }),
  };
  await expect(uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any })).rejects.toMatchObject({ status: 409 });
  expect(sessionStorage.getItem("aidoc_upload:a.pdf:10:1")).toBeNull();
});

test("507 insufficient_disk fails at once instead of retrying as a network error", async () => {
  const api = fakeApi([{ status: 507, body: { error: "insufficient_disk", needed: 30, free: 1 } }], { size: 10 });
  const t0 = Date.now();
  await expect(uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any, retryDelayMs: 200 })).rejects.toMatchObject({
    status: 507,
    body: { error: "insufficient_disk" },
  });
  expect(Date.now() - t0).toBeLessThan(150);
  expect(api.calls.some((c) => c[0] === "head")).toBe(false);
});

test("sha mismatch (422) throws and forgets the saved upload id", async () => {
  const api = fakeApi([{ status: 200 }, { status: 200 }, { status: 422, body: { error: "sha_mismatch" } }], { size: 10 });
  await expect(uploadFile(file(), { sha256: "x".repeat(64), chunkSize: 4, api: api as any })).rejects.toMatchObject({
    status: 422,
    body: { error: "sha_mismatch" },
  });
  expect(sessionStorage.getItem("aidoc_upload:a.pdf:10:1")).toBeNull();
});
