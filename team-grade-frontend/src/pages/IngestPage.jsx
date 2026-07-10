import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ingestVideo } from "../api";

export default function IngestPage() {
  const navigate = useNavigate();
  const [videoUrl, setVideoUrl] = useState("");
  const [teamId, setTeamId] = useState("");
  const [playerNumber, setPlayerNumber] = useState("");
  const [position, setPosition] = useState("");
  const [phase, setPhase] = useState("idle"); // idle | submitting | error
  const [errorMessage, setErrorMessage] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setPhase("submitting");
    setErrorMessage(null);

    try {
      const result = await ingestVideo({ videoUrl, teamId, playerNumber, position });
      navigate(`/analysis/${result.video_id}`);
    } catch (err) {
      setErrorMessage(err.message);
      setPhase("error");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-white">
      <div className="w-full max-w-lg">
        <header className="mb-8 text-center">
          <h1 className="font-condensed text-3xl font-bold tracking-wide">TEAM-GRADE</h1>
          <p className="mt-1 text-zinc-500">Submit game film for pose &amp; biomechanics analysis</p>
        </header>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg bg-zinc-900 p-6">
          <label className="flex flex-col gap-1.5 text-sm font-semibold text-zinc-300">
            YouTube URL
            <input
              type="text"
              value={videoUrl}
              onChange={(e) => setVideoUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              required
              className="rounded border border-zinc-700 bg-zinc-950 px-3 py-2 font-normal text-white placeholder:text-zinc-600"
            />
          </label>

          <div className="grid grid-cols-3 gap-3">
            <label className="flex flex-col gap-1.5 text-xs font-semibold text-zinc-400">
              Team ID
              <input
                value={teamId}
                onChange={(e) => setTeamId(e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-normal text-white"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-xs font-semibold text-zinc-400">
              Player #
              <input
                value={playerNumber}
                onChange={(e) => setPlayerNumber(e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-normal text-white"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-xs font-semibold text-zinc-400">
              Position
              <input
                value={position}
                onChange={(e) => setPosition(e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 font-normal text-white"
              />
            </label>
          </div>

          <button
            type="submit"
            disabled={phase === "submitting"}
            className="rounded bg-bridge-gold px-4 py-2 font-bold text-black hover:bg-amber-400 disabled:opacity-60"
          >
            {phase === "submitting" ? "Submitting..." : "Submit for analysis"}
          </button>

          {phase === "error" && <p className="text-sm font-semibold text-red-400">{errorMessage}</p>}
        </form>
      </div>
    </div>
  );
}
