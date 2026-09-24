import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Document,
  DocumentDetail,
  EngineName,
  Job,
  JobCreateBody,
  JobDetail,
  QueueInfo,
  Settings,
  SettingsPatch,
  SystemInfo,
  Task,
} from "./types";

export type DocFilters = { q?: string; engine?: string; status?: string };

export const qk = {
  jobs: () => ["jobs", "list"] as const,
  job: (id: string) => ["jobs", "detail", id] as const,
  documents: (f: DocFilters = {}) => ["documents", "list", f] as const,
  document: (id: string) => ["documents", "detail", id] as const,
  markdown: (id: string) => ["documents", "markdown", id] as const,
  system: () => ["system"] as const,
  settings: () => ["settings"] as const,
};

function qs(params: Record<string, string | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) u.set(k, v);
  const s = u.toString();
  return s ? `?${s}` : "";
}

export function useJobs() {
  return useQuery({
    queryKey: qk.jobs(),
    queryFn: ({ signal }) => api.get<{ jobs: Job[] }>("/api/jobs?limit=200", signal).then((r) => r.jobs),
  });
}

export function useJob(id: string | undefined) {
  return useQuery({
    queryKey: qk.job(id ?? ""),
    queryFn: ({ signal }) => api.get<JobDetail>(`/api/jobs/${id}`, signal),
    enabled: !!id,
  });
}

export function useDocuments(filters: DocFilters) {
  return useQuery({
    queryKey: qk.documents(filters),
    queryFn: ({ signal }) =>
      api.get<{ documents: Document[] }>(`/api/documents${qs(filters)}`, signal).then((r) => r.documents),
    placeholderData: keepPreviousData,
  });
}

export function useDocument(id: string | undefined) {
  return useQuery({
    queryKey: qk.document(id ?? ""),
    queryFn: ({ signal }) => api.get<DocumentDetail>(`/api/documents/${id}`, signal),
    enabled: !!id,
  });
}

export function useMarkdown(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.markdown(id ?? ""),
    queryFn: () => api.text(`/api/documents/${id}/markdown`).then((r) => r.text),
    enabled: !!id && enabled,
    staleTime: 60_000,
  });
}

export function useSystem() {
  return useQuery({
    queryKey: qk.system(),
    queryFn: ({ signal }) => api.get<SystemInfo>("/api/system", signal),
    refetchInterval: 10_000,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: qk.settings(),
    queryFn: ({ signal }) => api.get<{ settings: Settings }>("/api/settings", signal).then((r) => r.settings),
  });
}

// ------------------------------------------------------------------ mutations

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobCreateBody) => api.post<{ job: Job }>("/api/jobs", body).then((r) => r.job),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.post<{ job: Job }>(`/api/jobs/${jobId}/cancel`).then((r) => r.job),
    onSuccess: (job) => qc.invalidateQueries({ queryKey: qk.job(job.id) }),
  });
}

export function useRetryTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { taskId: string; use_new_version?: boolean; retry_low?: boolean }) =>
      api
        .post<{ task: Task }>(`/api/tasks/${v.taskId}/retry`, {
          use_new_version: !!v.use_new_version,
          retry_low: !!v.retry_low,
        })
        .then((r) => r.task),
    onSuccess: (task) => qc.invalidateQueries({ queryKey: qk.job(task.job_id) }),
  });
}

function useQueueMutation(action: "pause" | "resume") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ queue: QueueInfo }>(`/api/queue/${action}`).then((r) => r.queue),
    onSuccess: (queue) =>
      qc.setQueryData<SystemInfo>(qk.system(), (old) => (old ? { ...old, queue } : old)),
  });
}
export const usePauseQueue = () => useQueueMutation("pause");
export const useResumeQueue = () => useQueueMutation("resume");

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.put<{ settings: Settings }>("/api/settings", patch).then((r) => r.settings),
    onSuccess: (settings) => {
      qc.setQueryData(qk.settings(), settings);
      qc.invalidateQueries({ queryKey: qk.system() });
    },
  });
}

export function useSetupEngine() {
  return useMutation({
    mutationFn: (engine: EngineName | "all") =>
      api.post<{ setup_id: string; engine: string }>(`/api/system/setup/${engine}`),
  });
}

export function useDeleteOrphaned() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.del<{ deleted: number }>("/api/documents/orphaned"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useRescan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ orphaned: number }>("/api/documents/rescan"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}
