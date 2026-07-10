import { useEffect, useRef, useState } from "react";
import { getFrameBoxes } from "../api";

// Must match config/settings.py's FRAME_EXTRACTION_FPS default - there's no
// config-exposure endpoint to read this from, and adding one is unnecessary
// scope for this pass.
const FRAME_EXTRACTION_FPS = 15;

export default function BoxOverlay({ videoId, currentTime, videoEl }) {
  const canvasRef = useRef(null);
  const lastFrameRef = useRef(-1);
  const [boxes, setBoxes] = useState([]);

  useEffect(() => {
    const frameIndex = Math.floor(currentTime * FRAME_EXTRACTION_FPS);
    if (frameIndex === lastFrameRef.current) return;
    lastFrameRef.current = frameIndex;

    let cancelled = false;
    getFrameBoxes(videoId, frameIndex)
      .then((data) => {
        if (!cancelled) setBoxes(data.boxes || []);
      })
      .catch(() => {
        if (!cancelled) setBoxes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [videoId, currentTime]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !videoEl || !videoEl.videoWidth) return;

    canvas.width = videoEl.clientWidth;
    canvas.height = videoEl.clientHeight;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!boxes.length) return;

    // Detection/tracking boxes are stored in frame_extraction's working
    // resolution, which isn't guaranteed to match the source file's native
    // resolution this <video> element decodes at (the same wrinkle the
    // blueprint flags for the highlight-tape export overlay). This live
    // preview scales against the video's own decoded dimensions, which is
    // exact when frame_extraction didn't downscale - a reasonable
    // approximation for an in-progress preview, not the final export.
    const scaleX = canvas.width / videoEl.videoWidth;
    const scaleY = canvas.height / videoEl.videoHeight;

    ctx.strokeStyle = "#F59E0B";
    ctx.lineWidth = 2;
    ctx.font = "12px sans-serif";
    ctx.fillStyle = "#F59E0B";

    for (const box of boxes) {
      if (!box.bbox || box.bbox.length !== 4) continue;
      const [x1, y1, x2, y2] = box.bbox;
      const x = x1 * scaleX;
      const y = y1 * scaleY;
      const w = (x2 - x1) * scaleX;
      const h = (y2 - y1) * scaleY;
      ctx.strokeRect(x, y, w, h);
      ctx.fillText(`#${box.track_id}`, x, Math.max(10, y - 4));
    }
  }, [boxes, videoEl]);

  return <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />;
}
