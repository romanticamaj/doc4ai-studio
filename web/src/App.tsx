import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router";
import { AppShell } from "@/components/layout/AppShell";
import { StatusCluster } from "@/components/layout/StatusCluster";
import { Toaster } from "@/components/ui/sonner";
import { AppRoutes } from "@/routes";
import { EventStreamProvider } from "@/events/EventStreamProvider";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true, retry: 1 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <EventStreamProvider>
      <BrowserRouter>
        <AppShell status={<StatusCluster />}>
          <AppRoutes />
        </AppShell>
        <Toaster />
      </BrowserRouter>
      </EventStreamProvider>
    </QueryClientProvider>
  );
}
