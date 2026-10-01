export type Job = { run: () => Promise<unknown>; priority: () => number };

/**
 * Runs at most `limit` jobs at once. When a slot frees, the pending job with the lowest priority value *at that
 * moment* starts (priorities are functions, so a job can become urgent as the view moves). `schedule` returns a
 * cancel function that drops a job that has not started yet.
 */
export class RenderQueue {
  private pending = new Set<Job>();
  private running = 0;

  private limit: number;

  constructor(limit: number) {
    this.limit = limit;
  }

  schedule(job: Job): () => void {
    this.pending.add(job);
    queueMicrotask(() => this.pump());
    return () => {
      this.pending.delete(job);
    };
  }

  private pump() {
    while (this.running < this.limit && this.pending.size) {
      let best: Job | null = null;
      let bestP = Infinity;
      for (const j of this.pending) {
        const p = j.priority();
        if (best === null || p < bestP) {
          best = j;
          bestP = p;
        }
      }
      if (!best) return;
      this.pending.delete(best);
      this.running++;
      let p: Promise<unknown>;
      try {
        p = best.run();
      } catch (e) {
        p = Promise.reject(e);
      }
      p.catch(() => undefined).finally(() => {
        this.running--;
        this.pump();
      });
    }
  }
}
