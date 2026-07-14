"""
Film authenticity heuristics - is the submitted footage genuine, unmanipulated
video?

A port of VerifAI's (a separate project - a Chrome extension that analyzes a
live browser MediaStream in JS) heuristic detection *concepts* into this
package's convention (standalone processing modules, importable independently
of the Firestore-backed pipeline - see audio_analyzer.py, rep_extraction.py).
Not code reuse: VerifAI works frame-by-frame in JS against a live tab capture;
this works against an already-downloaded file, server-side, in Python.

Two independent signals, matching the two touchpoints in
docs/team-grade-integration-blueprint.md's "AI verification" section:

- analyze_file_integrity(): whole-file gate. Container/codec/metadata only -
  no pixel decode - so it stays cheap. Run right after download, before
  frame_extraction (see ingest/stages/authenticity_check_stage.py).
- analyze_frame_signals(): chunk-based vision/motion pass over a sampled
  spread of already-extracted frames. Run inline from frame_extraction (see
  ingest/stages/frame_extraction_stage_gpu.py) - reuses frames already decoded
  rather than decoding the video a second time.

Both are hand-tuned heuristics, not a trained classifier - same honesty
VerifAI's own docs carry: this catches some classes of manipulation, it is not
a certified deepfake detector, and it can false-flag a real video occasionally.
Confidence is always reported as "low"/"medium"/"unknown", never "high" or
certain. Callers treat a flag as a soft signal (see the stage files for how
it's surfaced), never a hard failure on its own.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import settings

logger = logging.getLogger(__name__)

# Same path-allowlisting convention as ingest/utils/ffmpeg_utils.py - only the
# configured/system binary name is ever used, never an arbitrary path, so
# there's no command-injection surface even though these are subprocess calls.
_FFPROBE_PATH = getattr(settings, "FFPROBE_PATH", "ffprobe")
_PROBE_TIMEOUT_SEC = 10


def _run_ffprobe(args: list[str]) -> dict[str, Any] | None:
    """Run ffprobe with the given extra args, returning parsed JSON or None on any failure."""
    cmd = [_FFPROBE_PATH, "-v", "error", "-of", "json", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_PROBE_TIMEOUT_SEC, text=True, shell=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"[film_authenticity] ffprobe unavailable or timed out: {e}")
        return None

    if result.returncode != 0:
        logger.warning(f"[film_authenticity] ffprobe exited {result.returncode}: {result.stderr[:200]}")
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.warning(f"[film_authenticity] ffprobe returned invalid JSON: {e}")
        return None


def analyze_file_integrity(video_path: Path) -> dict[str, Any]:
    """Whole-file gate: container/codec/metadata heuristics only, no pixel decode.

    Looks for two weak signals real camera/broadcast footage tends not to
    show: a missing/generic encoder tag with no creation_time (a re-mux/
    re-encode indicator), and an abnormally sparse keyframe interval sampled
    over a bounded window (cheap - reads a frame index window, not the whole
    file).

    Never raises - any internal failure (ffprobe missing, malformed output,
    unreadable file) is caught and returns confidence="unknown" so a broken
    environment never blocks the pipeline, it just skips this signal.

    Returns:
        {"flagged": bool, "confidence": "low"|"medium"|"unknown", "reasons": [str, ...], "raw": {...}}
    """
    try:
        video_path = Path(video_path)
        if not video_path.exists():
            return {"flagged": False, "confidence": "unknown", "reasons": ["video_file_not_found"], "raw": {}}

        reasons: list[str] = []
        raw: dict[str, Any] = {}

        # -------- Encoder/creation_time metadata --------
        format_info = _run_ffprobe([
            "-show_entries", "format_tags=encoder,creation_time",
            str(video_path),
        ])
        encoder = None
        creation_time = None
        if format_info:
            tags = format_info.get("format", {}).get("tags", {}) or {}
            encoder = tags.get("encoder")
            creation_time = tags.get("creation_time")
            raw["encoder"] = encoder
            raw["creation_time"] = creation_time
            if not encoder and not creation_time:
                reasons.append("missing_encoder_and_creation_time_metadata")

        # -------- Keyframe (GOP) interval sample over a bounded window --------
        window_sec = getattr(settings, "AUTHENTICITY_GOP_PROBE_WINDOW_SEC", 30.0)
        frame_info = _run_ffprobe([
            "-select_streams", "v:0",
            "-show_entries", "frame=pict_type",
            "-read_intervals", f"%+{int(window_sec)}",
            str(video_path),
        ])
        if frame_info:
            frames = frame_info.get("frames", [])
            pict_types = [f.get("pict_type") for f in frames if f.get("pict_type")]
            keyframe_count = sum(1 for p in pict_types if p == "I")
            raw["sampled_frame_count"] = len(pict_types)
            raw["keyframe_count_in_window"] = keyframe_count
            if pict_types and keyframe_count > 0:
                keyframe_interval_sec = window_sec / keyframe_count
                raw["keyframe_interval_sec"] = round(keyframe_interval_sec, 2)
                max_interval = getattr(settings, "AUTHENTICITY_MAX_KEYFRAME_INTERVAL_SEC", 12.0)
                if keyframe_interval_sec > max_interval:
                    reasons.append(f"sparse_keyframe_interval_{keyframe_interval_sec:.1f}s")
            elif pict_types and keyframe_count == 0:
                # A sampled window with frames but zero I-frames is itself unusual.
                reasons.append("no_keyframe_in_sample_window")

        if not format_info and not frame_info:
            return {"flagged": False, "confidence": "unknown", "reasons": ["ffprobe_unavailable"], "raw": {}}

        return {
            "flagged": len(reasons) > 0,
            "confidence": "medium" if reasons else "low",
            "reasons": reasons,
            "raw": raw,
        }

    except Exception as e:
        logger.warning(f"[film_authenticity] analyze_file_integrity failed: {e}")
        return {"flagged": False, "confidence": "unknown", "reasons": [f"check_failed: {e}"], "raw": {}}


def analyze_frame_signals(frame_paths: list[Path]) -> dict[str, Any]:
    """Chunk-based vision/motion pass over an already-sampled spread of frames.

    Reads each frame image (already decoded/written by frame_extraction - no
    second video decode happens here) and looks for three weak signals:
    edge-density variance (vision), histogram drift between consecutive
    frames (vision), and motion magnitude between consecutive frames (motion).
    A single unreadable frame is skipped, not fatal to the whole check.

    Never raises - same "return confidence=unknown on failure" contract as
    analyze_file_integrity.

    Returns:
        {"flagged": bool, "confidence": "low"|"medium"|"unknown", "reasons": [str, ...], "raw": {...}}
    """
    try:
        images = []
        for p in frame_paths:
            img = cv2.imread(str(p))
            if img is not None:
                images.append(img)

        if len(images) < 2:
            return {"flagged": False, "confidence": "unknown", "reasons": ["not_enough_readable_frames"], "raw": {}}

        reasons: list[str] = []
        raw: dict[str, Any] = {}

        # -------- Vision: edge density variance across the sample --------
        edge_densities = []
        for img in images:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 100, 200)
            edge_densities.append(float(np.count_nonzero(edges)) / edges.size)
        edge_variance = float(np.var(edge_densities))
        raw["edge_density_variance"] = edge_variance
        min_variance = getattr(settings, "AUTHENTICITY_EDGE_DENSITY_VARIANCE_MIN", 0.0005)
        if edge_variance < min_variance:
            reasons.append(f"low_edge_density_variance_{edge_variance:.6f}")

        # -------- Vision: chi-square histogram drift between consecutive frames --------
        histograms = []
        for img in images:
            hist = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            histograms.append(hist)
        chisqr_drifts = [
            float(cv2.compareHist(histograms[i], histograms[i + 1], cv2.HISTCMP_CHISQR))
            for i in range(len(histograms) - 1)
        ]
        max_drift = max(chisqr_drifts) if chisqr_drifts else 0.0
        raw["max_histogram_chisqr_drift"] = max_drift
        spike_threshold = getattr(settings, "AUTHENTICITY_HISTOGRAM_CHISQR_SPIKE_THRESHOLD", 5000.0)
        if max_drift > spike_threshold:
            reasons.append(f"histogram_drift_spike_{max_drift:.0f}")

        # -------- Motion: mean absolute difference between consecutive frames --------
        motion_diffs = []
        for i in range(len(images) - 1):
            g1 = cv2.cvtColor(images[i], cv2.COLOR_BGR2GRAY)
            g2 = cv2.cvtColor(images[i + 1], cv2.COLOR_BGR2GRAY)
            if g1.shape != g2.shape:
                continue
            motion_diffs.append(float(np.mean(cv2.absdiff(g1, g2))))
        mean_motion = float(np.mean(motion_diffs)) if motion_diffs else 0.0
        raw["mean_motion_diff"] = mean_motion
        static_threshold = getattr(settings, "AUTHENTICITY_MOTION_DIFF_STATIC_THRESHOLD", 1.0)
        if motion_diffs and mean_motion < static_threshold:
            reasons.append(f"suspiciously_static_{mean_motion:.3f}")

        return {
            "flagged": len(reasons) > 0,
            "confidence": "medium" if reasons else "low",
            "reasons": reasons,
            "raw": raw,
        }

    except Exception as e:
        logger.warning(f"[film_authenticity] analyze_frame_signals failed: {e}")
        return {"flagged": False, "confidence": "unknown", "reasons": [f"check_failed: {e}"], "raw": {}}
