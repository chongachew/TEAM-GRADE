import { BrowserRouter, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import IngestPage from "./pages/IngestPage";
import AnalysisPage from "./pages/AnalysisPage";

const queryClient = new QueryClient();

// Resume-in-progress: a video submitted elsewhere (e.g. the-bridge.app's
// landing page hero) hands off here via ?video_id=, rather than through this
// app's own ingest form - redirect straight to that video's analysis page.
function RootRoute() {
  const [searchParams] = useSearchParams();
  const resumeVideoId = searchParams.get("video_id");
  if (resumeVideoId) {
    return <Navigate to={`/analysis/${resumeVideoId}`} replace />;
  }
  return <IngestPage />;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRoute />} />
          <Route path="/analysis/:videoId" element={<AnalysisPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
