import { useCallback, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getAnalysis, getStatus, getTracks } from "../api";
import VideoPlayer from "../components/VideoPlayer";
import BoxOverlay from "../components/BoxOverlay";
import PlaysGrid from "../components/PlaysGrid";
import PlayerLibrary from "../components/PlayerLibrary";
import ClaimBar from "../components/ClaimBar";
import BoundaryEditor from "../components/BoundaryEditor";

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

function stageColor(status) {
  if (status === "completed" || status === "skipped") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  if (status === "in-progress" || status === "processing") return "text-bridge-gold";
  return "text-zinc-500";
}

function StageList({ stages }) {
  return (
    <ul className="mt-4 flex flex-col gap-1.5">
      {STAGES.map((stage) => {
        const status = stages?.[stage]?.status || "pending";
        return (
          <li
            key={stage}
            className="flex items-center justify-between rounded bg-zinc-900 px-3 py-1.5"
          >
            <span className="font-condensed text-sm capitalize text-zinc-300">
              {stage.replace(/_/g, " ")}
            </span>
            <span className={`text-xs font-bold uppercase ${stageColor(status)}`}>{status}</span>
          </li>
        );
      })}
    </ul>
  );
}

export default function AnalysisPage() {
  const { videoId } = useParams();
  const queryClient = useQueryClient();
  const videoRef = useRef(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [claimedTrackId, setClaimedTrackId] = useState(null);
  const [editingRep, setEditingRep] = useState(null);

  const statusQuery = useQuery({
    queryKey: ["status", videoId],
    queryFn: () => getStatus(videoId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 3000;
    },
  });

  const isComplete = statusQuery.data?.status === "completed";

  const analysisQuery = useQuery({
    queryKey: ["analysis", videoId],
    queryFn: () => getAnalysis(videoId),
    enabled: isComplete,
  });

  const tracksQuery = useQuery({
    queryKey: ["tracks", videoId],
    queryFn: () => getTracks(videoId),
    enabled: isComplete,
  });

  const handleSeek = useCallback((seconds) => {
    if (videoRef.current && Number.isFinite(seconds)) {
      videoRef.current.currentTime = seconds;
      videoRef.current.play().catch(() => {});
    }
  }, []);

  const handleBoundarySaved = useCallback(
    (updatedRep) => {
      queryClient.setQueryData(["analysis", videoId], (old) => {
        if (!old) return old;
        return {
          reps: old.reps.map((r) =>
            r.rep_index === updatedRep.rep_index && r.track_id === updatedRep.track_id
              ? { ...r, ...updatedRep }
              : r
          ),
        };
      });
      setEditingRep(null);
    },
    [queryClient, videoId]
  );

  return (
    <div className="min-h-screen bg-zinc-950 text-white">
      <header className="border-b border-zinc-800 px-6 py-4">
        <Link
          to="/"
          className="font-condensed text-xs uppercase tracking-widest text-zinc-500 hover:text-bridge-gold"
        >
          ← New analysis
        </Link>
        <h1 className="font-condensed text-2xl font-bold tracking-wide">
          TEAM-GRADE <span className="text-bridge-gold">/ {videoId}</span>
        </h1>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        {statusQuery.isError && (
          <p className="text-red-400">{statusQuery.error.message}</p>
        )}

        {statusQuery.data?.status === "failed" && (
          <p className="rounded-lg bg-red-950 p-4 text-red-300">
            {statusQuery.data.error || "Processing failed"}
          </p>
        )}

        {!isComplete && statusQuery.data && statusQuery.data.status !== "failed" && (
          <div className="rounded-lg bg-zinc-900 p-6">
            <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full bg-bridge-gold transition-all"
                style={{ width: `${statusQuery.data.progress ?? 0}%` }}
              />
            </div>
            <p className="mt-2 text-sm text-zinc-400">{statusQuery.data.progress ?? 0}% complete</p>
            <StageList stages={statusQuery.data.stages} />
          </div>
        )}

        {isComplete && (
          <div className="flex flex-col gap-8">
            <div className="relative">
              <VideoPlayer ref={videoRef} videoId={videoId} onTimeUpdate={setCurrentTime} />
              <BoxOverlay videoId={videoId} currentTime={currentTime} videoEl={videoRef.current} />
            </div>

            <section>
              <h2 className="mb-3 font-condensed text-lg font-bold uppercase tracking-wide text-zinc-400">
                Plays
              </h2>
              <PlaysGrid
                videoId={videoId}
                reps={analysisQuery.data?.reps || []}
                claimedTrackId={claimedTrackId}
                onSeek={handleSeek}
                onEditBoundary={setEditingRep}
              />
            </section>

            {editingRep && (
              <BoundaryEditor
                videoId={videoId}
                videoRef={videoRef}
                rep={editingRep}
                onSaved={handleBoundarySaved}
                onCancel={() => setEditingRep(null)}
              />
            )}

            <section>
              <h2 className="mb-3 font-condensed text-lg font-bold uppercase tracking-wide text-zinc-400">
                Players
              </h2>
              <PlayerLibrary
                videoId={videoId}
                tracks={tracksQuery.data?.tracks || []}
                claimedTrackId={claimedTrackId}
                onClaim={setClaimedTrackId}
              />
            </section>

            <ClaimBar
              videoId={videoId}
              claimedTrackId={claimedTrackId}
              onClear={() => setClaimedTrackId(null)}
            />
          </div>
        )}
      </main>
    </div>
  );
}
