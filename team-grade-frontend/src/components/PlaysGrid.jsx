// Must match config/settings.py's FRAME_EXTRACTION_FPS default - used to seek
// the video player to a rep's start_frame.
const FRAME_EXTRACTION_FPS = 15;

// Neither service is deployed publicly yet, so this points at localhost in
// dev - mirrors ClaimBar.jsx's REACT_APP_BRIDGE_APP_URL documentation.
const BRIDGE_APP_URL = process.env.REACT_APP_BRIDGE_APP_URL || "http://localhost:3000";

function bucketClasses(bucket) {
  switch (bucket) {
    case "elite":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-400";
    case "strong":
      return "border-bridge-gold/40 bg-bridge-gold/10 text-bridge-gold";
    case "average":
      return "border-zinc-500/40 bg-zinc-500/10 text-zinc-300";
    default:
      return "border-zinc-700/40 bg-zinc-700/10 text-zinc-500";
  }
}

export default function PlaysGrid({ videoId, reps, claimedTrackId, onSeek, onEditBoundary, provisional }) {
  if (!reps.length) {
    return <p className="text-sm text-zinc-500">No plays detected yet.</p>;
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {reps.map((rep) => (
        <div
          key={`${rep.track_id}-${rep.rep_index}`}
          className={`flex flex-col gap-1 rounded-lg border p-3 transition hover:border-bridge-gold/60 ${
            rep.track_id === claimedTrackId
              ? "border-bridge-gold bg-bridge-gold/10"
              : "border-zinc-800 bg-zinc-900"
          }`}
        >
          <button
            type="button"
            disabled={rep.start_frame == null}
            onClick={() => onSeek(rep.start_frame / FRAME_EXTRACTION_FPS)}
            className="flex flex-col items-start gap-1 text-left disabled:cursor-default disabled:opacity-60"
          >
            <span className="font-condensed text-xs uppercase tracking-wide text-zinc-500">
              Play {rep.rep_index + 1}
              {rep.track_id != null ? ` · #${rep.track_id}` : ""}
            </span>
            <span className="font-condensed text-2xl font-bold text-white">
              {rep.overall_grade != null ? Math.round(rep.overall_grade) : "—"}
            </span>
            <div className="flex flex-wrap gap-1">
              {Object.entries(rep.buckets || {}).map(([trait, bucket]) => (
                <span
                  key={trait}
                  className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${bucketClasses(bucket)}`}
                >
                  {trait}
                </span>
              ))}
            </div>
          </button>
          <div className="flex items-center justify-end gap-3">
            {onEditBoundary && rep.start_frame != null && (
              <button
                type="button"
                onClick={() => onEditBoundary(rep)}
                className="text-[10px] uppercase tracking-wide text-zinc-500 hover:text-bridge-gold"
              >
                Edit boundary
              </button>
            )}
            {/* Provisional plays aren't written to Firestore's `reps`
                collection yet (that only happens on the real, final
                rep_extraction pass) - TEAM-GRADE's clip-cut endpoint would
                404 looking one up, so this stays hidden until the play is
                no longer just a preview. */}
            {!provisional && videoId && rep.start_frame != null && (
              <a
                href={`${BRIDGE_APP_URL}/add-to-reel?videoId=${encodeURIComponent(videoId)}&trackId=${rep.track_id}&repIndex=${rep.rep_index}`}
                className="text-[10px] uppercase tracking-wide text-zinc-500 hover:text-bridge-gold"
              >
                Add to reel
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
