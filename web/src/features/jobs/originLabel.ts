import type { Job } from "@/api/types";

/** How a job's origin reads in the Jobs list and detail: the docling-serve compatible API gets a word, the others
 *  keep their technical name. */
export function originLabel(origin: Job["origin"] | string): string {
  return origin === "compat" ? "相容 API" : origin;
}
