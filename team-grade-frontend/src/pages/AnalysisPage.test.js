import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AnalysisPage from "./AnalysisPage";
import { getStatus, getAnalysis, getTracks } from "../api";

// Plays-list gating is what's under test here - the video player/box overlay/
// player library are unrelated subsystems with their own dependencies
// (canvas, video refs), stubbed out so this stays a focused test of
// AnalysisPage's own isComplete/provisional wiring.
jest.mock("../api");
jest.mock("../components/VideoPlayer", () => () => <div data-testid="video-player" />);
jest.mock("../components/BoxOverlay", () => () => null);
jest.mock("../components/PlayerLibrary", () => () => <div data-testid="player-library" />);
jest.mock("../components/ClaimBar", () => () => null);
jest.mock("../components/BoundaryEditor", () => () => null);

function renderAnalysisPage(videoId = "dQw4w9WgXcQ") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/analysis/${videoId}`]}>
        <Routes>
          <Route path="/analysis/:videoId" element={<AnalysisPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("AnalysisPage - plays list shown independent of completion", () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it("shows a provisional preview of plays while the video is still processing", async () => {
    getStatus.mockResolvedValue({
      status: "processing",
      progress: 55,
      stages: {},
    });
    getAnalysis.mockResolvedValue({
      reps: [
        { rep_index: 0, track_id: 0, overall_grade: 80, buckets: {}, start_frame: 10 },
      ],
      provisional: true,
    });
    getTracks.mockResolvedValue({ tracks: [] });

    renderAnalysisPage();

    expect(await screen.findByText(/play 1/i)).toBeInTheDocument();
    expect(screen.getByText(/preview.*may still change/i)).toBeInTheDocument();
    // Video player is completion-gated - must not render while processing.
    expect(screen.queryByTestId("video-player")).not.toBeInTheDocument();
  });

  it("shows the final plays list with no preview badge once complete", async () => {
    getStatus.mockResolvedValue({
      status: "completed",
      progress: 100,
      stages: {},
    });
    getAnalysis.mockResolvedValue({
      reps: [
        { rep_index: 0, track_id: 0, overall_grade: 92, buckets: {}, start_frame: 10 },
      ],
      provisional: false,
    });
    getTracks.mockResolvedValue({ tracks: [] });

    renderAnalysisPage();

    expect(await screen.findByText(/play 1/i)).toBeInTheDocument();
    expect(screen.queryByText(/preview.*may still change/i)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("video-player")).toBeInTheDocument());
  });

  it("renders \"no plays detected yet\" instead of crashing when there is nothing to preview", async () => {
    getStatus.mockResolvedValue({ status: "processing", progress: 10, stages: {} });
    getAnalysis.mockResolvedValue({ reps: [], provisional: false });
    getTracks.mockResolvedValue({ tracks: [] });

    renderAnalysisPage();

    expect(await screen.findByText(/no plays detected yet/i)).toBeInTheDocument();
  });
});
