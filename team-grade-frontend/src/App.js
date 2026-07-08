import { useEffect, useRef, useState } from "react";
import "./App.css";
import { ingestVideo, getStatus, getAnalysis } from "./api";

const STAGES = [
  "metadata",
  "download",
  "frame_extraction",
  "pose",
  "torso_crop",
  "jersey_ocr",
  "rep_extraction",
  "biomechanics",
  "complete",
];

const POLL_INTERVAL_MS = 3000;

function StageList({ stages }) {
  return (
    <ul className="stage-list">
      {STAGES.map((stage) => {
        const stageStatus = stages?.[stage]?.status || "pending";
        return (
          <li key={stage} className={`stage stage-${stageStatus}`}>
            <span className="stage-name">{stage.replace(/_/g, " ")}</span>
            <span className="stage-badge">{stageStatus}</span>
          </li>
        );
      })}
    </ul>
  );
}

function App() {
  const [videoUrl, setVideoUrl] = useState("");
  const [teamId, setTeamId] = useState("");
  const [playerNumber, setPlayerNumber] = useState("");
  const [position, setPosition] = useState("");

  const [phase, setPhase] = useState("idle"); // idle | submitting | processing | done | error
  const [videoId, setVideoId] = useState(null);
  const [statusData, setStatusData] = useState(null);
  const [analysisData, setAnalysisData] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);

  const pollRef = useRef(null);

  useEffect(() => {
    return () => clearInterval(pollRef.current);
  }, []);

  async function pollStatus(id) {
    try {
      const data = await getStatus(id);
      setStatusData(data);

      if (data.status === "completed") {
        clearInterval(pollRef.current);
        const analysis = await getAnalysis(id);
        setAnalysisData(analysis);
        setPhase("done");
      } else if (data.status === "failed") {
        clearInterval(pollRef.current);
        setErrorMessage(data.error || "Processing failed");
        setPhase("error");
      }
    } catch (err) {
      clearInterval(pollRef.current);
      setErrorMessage(err.message);
      setPhase("error");
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setPhase("submitting");
    setErrorMessage(null);
    setStatusData(null);
    setAnalysisData(null);

    try {
      const result = await ingestVideo({ videoUrl, teamId, playerNumber, position });
      setVideoId(result.video_id);
      setPhase("processing");

      pollStatus(result.video_id);
      pollRef.current = setInterval(() => pollStatus(result.video_id), POLL_INTERVAL_MS);
    } catch (err) {
      setErrorMessage(err.message);
      setPhase("error");
    }
  }

  function handleReset() {
    clearInterval(pollRef.current);
    setPhase("idle");
    setVideoId(null);
    setStatusData(null);
    setAnalysisData(null);
    setErrorMessage(null);
    setVideoUrl("");
  }

  return (
    <div className="App">
      <header className="App-header">
        <h1>TEAM-GRADE</h1>
        <p className="subtitle">Submit game film for pose &amp; biomechanics analysis</p>
      </header>

      <main className="App-main">
        {(phase === "idle" || phase === "submitting" || phase === "error") && (
          <form className="ingest-form" onSubmit={handleSubmit}>
            <label>
              YouTube URL
              <input
                type="text"
                value={videoUrl}
                onChange={(e) => setVideoUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
                required
              />
            </label>
            <div className="optional-fields">
              <label>
                Team ID
                <input value={teamId} onChange={(e) => setTeamId(e.target.value)} />
              </label>
              <label>
                Player #
                <input value={playerNumber} onChange={(e) => setPlayerNumber(e.target.value)} />
              </label>
              <label>
                Position
                <input value={position} onChange={(e) => setPosition(e.target.value)} />
              </label>
            </div>
            <button type="submit" disabled={phase === "submitting"}>
              {phase === "submitting" ? "Submitting..." : "Submit for analysis"}
            </button>
            {phase === "error" && <p className="error-message">{errorMessage}</p>}
          </form>
        )}

        {phase === "processing" && (
          <div className="status-panel">
            <h2>Processing {videoId}</h2>
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${statusData?.progress ?? 0}%` }}
              />
            </div>
            <p>{statusData?.progress ?? 0}% complete</p>
            <StageList stages={statusData?.stages} />
          </div>
        )}

        {phase === "done" && (
          <div className="results-panel">
            <h2>Analysis complete — {videoId}</h2>
            <pre className="analysis-json">{JSON.stringify(analysisData, null, 2)}</pre>
            <button onClick={handleReset}>Analyze another video</button>
          </div>
        )}

        {phase === "error" && videoId && (
          <button onClick={handleReset}>Try again</button>
        )}
      </main>
    </div>
  );
}

export default App;
