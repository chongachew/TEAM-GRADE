import { thumbnailUrl } from "../api";

export default function PlayerLibrary({ videoId, tracks, claimedTrackId, onClaim }) {
  if (!tracks.length) {
    return <p className="text-sm text-zinc-500">No tracked players yet.</p>;
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {tracks.map((track) => {
        const isClaimed = track.track_id === claimedTrackId;
        // Thumbnail crop + generic label whenever jersey OCR doesn't resolve -
        // already-decided fallback (blueprint: "Player-library/claim UI").
        const label = track.jersey_number ? `#${track.jersey_number}` : `Player ${track.track_id}`;

        return (
          <div
            key={track.track_id}
            className={`flex flex-col overflow-hidden rounded-lg border bg-zinc-900 ${
              isClaimed ? "border-bridge-gold" : "border-zinc-800"
            }`}
          >
            <img
              src={thumbnailUrl(videoId, track.track_id)}
              alt={label}
              className="aspect-square w-full bg-zinc-800 object-cover"
              onError={(e) => {
                e.currentTarget.style.display = "none";
              }}
            />
            <div className="flex items-center justify-between gap-2 p-2">
              <span className="font-condensed text-sm font-semibold text-white">
                {label}
                {isClaimed && <span className="ml-1 text-bridge-gold">✓ You</span>}
              </span>
              {!isClaimed && (
                <button
                  type="button"
                  onClick={() => onClaim(track.track_id)}
                  className="rounded bg-bridge-gold px-2 py-1 text-xs font-bold text-black hover:bg-amber-400"
                >
                  Claim
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
