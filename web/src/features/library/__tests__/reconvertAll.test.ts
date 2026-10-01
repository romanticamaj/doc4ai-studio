import { expect, test } from "vitest";
import { ApiError } from "@/api/client";
import { reconvertAll } from "../reconvertAll";

test("documents whose source is gone are skipped; the rest is reconverted in chunks of 500", async () => {
  const calls: string[][] = [];
  const post = async (ids: string[]) => {
    calls.push(ids);
    const gone = ids.find((i) => i === "b" || i === "d");
    if (gone) throw new ApiError(410, { error: "source_missing", id: gone });
    return { id: `job${calls.length}` };
  };
  const r = await reconvertAll(["a", "b", "c", "d"], post as never);
  expect(r.skipped).toEqual(["b", "d"]);
  expect(r.jobs.map((j) => j.id)).toEqual(["job3"]);
  expect(calls.at(-1)).toEqual(["a", "c"]);

  const many = Array.from({ length: 1001 }, (_, i) => `x${i}`);
  const sizes: number[] = [];
  const r2 = await reconvertAll(many, (async (ids: string[]) => (sizes.push(ids.length), { id: "j" })) as never);
  expect(sizes).toEqual([500, 500, 1]);
  expect(r2.jobs.length).toBe(3);
});

test("other errors are not swallowed", async () => {
  const post = async () => {
    throw new ApiError(409, { error: "already_converting", id: "a" });
  };
  await expect(reconvertAll(["a"], post as never)).rejects.toThrow(/already_converting/);
});
