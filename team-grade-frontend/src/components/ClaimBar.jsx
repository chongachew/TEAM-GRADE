// Neither service is deployed publicly yet, so this points at localhost in
// dev - mirrors NEXT_PUBLIC_TEAM_GRADE_APP_URL's documentation on the Bridge
// Athletics side (apps/web/.env.example).
const BRIDGE_APP_URL = process.env.REACT_APP_BRIDGE_APP_URL || "http://localhost:3000";

export default function ClaimBar({ videoId, claimedTrackId, onClear }) {
  if (claimedTrackId == null) return null;

  const saveUrl = `${BRIDGE_APP_URL}/claim-film-stats?videoId=${encodeURIComponent(
    videoId
  )}&trackId=${claimedTrackId}`;

  return (
    <div className="sticky bottom-4 mt-4 flex items-center justify-between gap-4 rounded-lg border border-bridge-gold bg-zinc-900 p-4 shadow-lg shadow-black/40">
      <span className="font-condensed text-sm text-white">
        Player <span className="text-bridge-gold">#{claimedTrackId}</span> claimed as you.
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onClear}
          className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-white"
        >
          Undo
        </button>
        <a
          href={saveUrl}
          className="rounded bg-bridge-gold px-4 py-1.5 text-sm font-bold text-black hover:bg-amber-400"
        >
          Save to my profile
        </a>
      </div>
    </div>
  );
}
