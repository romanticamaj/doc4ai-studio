import { ApiError } from "@/api/client";
import type { Job } from "@/api/types";

const MAX_IDS = 500;

/** 「全部重新轉換」: POST /documents/reconvert in chunks of 500. The server checks every id before creating anything,
 *  so one document whose original and work copy are both gone (410 source_missing {id}) would block the whole batch:
 *  it is dropped and the request repeated. Other errors propagate. */
export async function reconvertAll(
  ids: string[],
  post: (ids: string[]) => Promise<Job>,
): Promise<{ jobs: Job[]; skipped: string[] }> {
  const jobs: Job[] = [];
  const skipped: string[] = [];
  for (let start = 0; start < ids.length; start += MAX_IDS) {
    let chunk = ids.slice(start, start + MAX_IDS);
    while (chunk.length) {
      try {
        jobs.push(await post(chunk));
        break;
      } catch (e) {
        const gone = e instanceof ApiError && e.status === 410 ? String(e.body.id ?? "") : "";
        if (!gone || !chunk.includes(gone)) throw e;
        skipped.push(gone);
        chunk = chunk.filter((i) => i !== gone);
      }
    }
  }
  return { jobs, skipped };
}
