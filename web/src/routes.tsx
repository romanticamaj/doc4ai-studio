import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import ConvertPage from "@/pages/ConvertPage";
import JobsPage from "@/pages/JobsPage";
import JobDetailPage from "@/pages/JobDetailPage";
import LibraryPage from "@/pages/LibraryPage";
import ChunksPage from "@/pages/ChunksPage";
import SettingsPage from "@/pages/SettingsPage";
import McpPage from "@/pages/McpPage";
import { PageFallback } from "@/components/states";

// pdf.js + KaTeX are heavy: the document view loads on demand
const DocumentPage = lazy(() => import("@/pages/DocumentPage"));

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/convert" replace />} />
      <Route path="/convert" element={<ConvertPage />} />
      <Route path="/jobs" element={<JobsPage />} />
      <Route path="/jobs/:jobId" element={<JobDetailPage />} />
      <Route path="/library" element={<LibraryPage />} />
      <Route
        path="/documents/:docId"
        element={
          <Suspense fallback={<PageFallback />}>
            <DocumentPage />
          </Suspense>
        }
      />
      <Route path="/chunks" element={<ChunksPage />} />
      <Route path="/mcp" element={<McpPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/convert" replace />} />
    </Routes>
  );
}
