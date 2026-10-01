import { expect, test } from "vitest";
import { RenderQueue } from "../renderQueue";

const tick = () => new Promise((r) => setTimeout(r, 0));

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

test("never runs more jobs than the limit at once", async () => {
  const q = new RenderQueue(2);
  const ds = Array.from({ length: 5 }, deferred);
  let running = 0;
  let peak = 0;
  ds.forEach((d) =>
    q.schedule({
      priority: () => 0,
      run: async () => {
        running++;
        peak = Math.max(peak, running);
        await d.promise;
        running--;
      },
    }),
  );
  await tick();
  expect(running).toBe(2);
  ds.forEach((d) => d.resolve());
  for (let i = 0; i < 5; i++) await tick();
  expect(peak).toBe(2);
  expect(running).toBe(0);
});

test("a cancelled job that has not started never runs", async () => {
  const q = new RenderQueue(1);
  const d = deferred();
  const ran: number[] = [];
  q.schedule({
    priority: () => 0,
    run: async () => {
      ran.push(1);
      await d.promise;
    },
  });
  const cancel = q.schedule({ priority: () => 0, run: async () => void ran.push(2) });
  q.schedule({ priority: () => 0, run: async () => void ran.push(3) });
  cancel();
  d.resolve();
  for (let i = 0; i < 5; i++) await tick();
  expect(ran).toEqual([1, 3]);
});

test("when a slot frees, the job with the best (lowest) priority right now runs next", async () => {
  const q = new RenderQueue(1);
  const d = deferred();
  const ran: string[] = [];
  const prio: Record<string, number> = { a: 5, b: 1, c: 3 };
  q.schedule({ priority: () => 0, run: () => d.promise });
  for (const k of ["a", "b", "c"]) q.schedule({ priority: () => prio[k], run: async () => void ran.push(k) });
  prio.c = 0; // the view moved: c is nearest now
  d.resolve();
  for (let i = 0; i < 6; i++) await tick();
  expect(ran).toEqual(["c", "b", "a"]);
});

test("a failing job frees its slot", async () => {
  const q = new RenderQueue(1);
  const ran: number[] = [];
  q.schedule({ priority: () => 0, run: () => Promise.reject(new Error("x")) });
  q.schedule({ priority: () => 0, run: async () => void ran.push(2) });
  for (let i = 0; i < 4; i++) await tick();
  expect(ran).toEqual([2]);
});
