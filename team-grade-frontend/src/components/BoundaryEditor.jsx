import { useEffect, useRef, useState } from "react";
import { correctRepBoundary } from "../api";

// Must match config/settings.py's FRAME_EXTRACTION_FPS default on the backend.
const FRAME_EXTRACTION_FPS = 15;
// Mirrors the backend's settings.REP_MIN_DURATION floor - kept in sync by
// convention (both default to 15), not fetched from the server.
const MIN_GAP_FRAMES = 15;

export default function BoundaryEditor({ videoId, videoRef, rep, onSaved, onCancel }) {
  const trackRef = useRef(null);
  const boundsRef = useRef({ start: rep.start_frame, end: rep.end_frame });
  const [localStart, setLocalStart] = useState(rep.start_frame);
  const [localEnd, setLocalEnd] = useState(rep.end_frame);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLocalStart(rep.start_frame);
    setLocalEnd(rep.end_frame);
    setError(null);
  }, [rep]);

  useEffect(() => {
    boundsRef.current = { start: localStart, end: localEnd };
  }, [localStart, localEnd]);

  // videoRef.current.duration loads asynchronously - reading it directly
  // during render would only ever reflect whatever it happened to be on this
  // component's last render, with nothing forcing a re-render once metadata
  // actually finishes loading. Track it in state, updated via the video's own
  // event, so the handles position correctly regardless of load timing.
  const [duration, setDuration] = useState(videoRef.current?.duration || 0);

  useEffect(() => {
    const videoEl = videoRef.current;
    if (!videoEl) return;
    function handleLoadedMetadata() {
      setDuration(videoEl.duration || 0);
    }
    if (videoEl.duration) setDuration(videoEl.duration);
    videoEl.addEventListener("loadedmetadata", handleLoadedMetadata);
    return () => videoEl.removeEventListener("loadedmetadata", handleLoadedMetadata);
  }, [videoRef]);
  const maxFrame = duration ? Math.floor(duration * FRAME_EXTRACTION_FPS) : 0;

  function frameToPercent(frame) {
    return maxFrame ? Math.min(100, Math.max(0, (frame / maxFrame) * 100)) : 0;
  }

  function percentToFrame(percent) {
    return Math.round((percent / 100) * maxFrame);
  }

  function startDrag(which) {
    function handleMove(e) {
      if (!trackRef.current || !maxFrame) return;
      const rect = trackRef.current.getBoundingClientRect();
      const percent = ((e.clientX - rect.left) / rect.width) * 100;
      const frame = Math.min(maxFrame, Math.max(0, percentToFrame(percent)));
      const { start, end } = boundsRef.current;

      if (which === "start") {
        setLocalStart(Math.min(frame, end - MIN_GAP_FRAMES));
      } else {
        setLocalEnd(Math.max(frame, start + MIN_GAP_FRAMES));
      }
    }
    function handleUp() {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    }
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }

  function previewSeek(frame) {
    if (videoRef.current) {
      videoRef.current.currentTime = frame / FRAME_EXTRACTION_FPS;
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const updated = await correctRepBoundary(videoId, {
        trackId: rep.track_id,
        repIndex: rep.rep_index,
        startFrame: localStart,
        endFrame: localEnd,
      });
      onSaved(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-bridge-gold/60 bg-zinc-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-condensed text-sm font-bold uppercase tracking-wide text-zinc-300">
          Edit boundary · Play {rep.rep_index + 1}
        </h3>
        <span className="text-xs text-zinc-500">
          {(localStart / FRAME_EXTRACTION_FPS).toFixed(1)}s – {(localEnd / FRAME_EXTRACTION_FPS).toFixed(1)}s
        </span>
      </div>

      <div ref={trackRef} className="relative h-3 rounded-full bg-zinc-800">
        <div
          className="absolute h-full rounded-full bg-bridge-gold/40"
          style={{
            left: `${frameToPercent(localStart)}%`,
            width: `${Math.max(0, frameToPercent(localEnd) - frameToPercent(localStart))}%`,
          }}
        />
        <button
          type="button"
          aria-label="Start handle"
          onPointerDown={() => startDrag("start")}
          className="absolute top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border-2 border-black bg-bridge-gold"
          style={{ left: `${frameToPercent(localStart)}%` }}
        />
        <button
          type="button"
          aria-label="End handle"
          onPointerDown={() => startDrag("end")}
          className="absolute top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border-2 border-black bg-bridge-gold"
          style={{ left: `${frameToPercent(localEnd)}%` }}
        />
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => previewSeek(localStart)}
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:text-white"
          >
            Preview start
          </button>
          <button
            type="button"
            onClick={() => previewSeek(localEnd)}
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:text-white"
          >
            Preview end
          </button>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="rounded bg-bridge-gold px-4 py-1.5 text-sm font-bold text-black hover:bg-amber-400 disabled:opacity-60"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {error && <p className="mt-2 text-sm font-semibold text-red-400">{error}</p>}
    </div>
  );
}
