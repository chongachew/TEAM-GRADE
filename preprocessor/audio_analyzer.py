"""
Audio analyzer — whistle detection using librosa RMS energy + spectral centroid.

Detection strategy (hybrid):
  1. Compute short-time RMS energy.  Whistles produce sharp energy spikes.
  2. Compute spectral centroid.  Whistles have centroid concentrated in a
     narrow high-frequency band (≈ 2–4 kHz).
  3. Combine both signals: candidate frames must exceed both thresholds to
     reduce false-positives from crowd noise / commentary.

Output: list of dicts with keys {start_time, end_time, confidence}.

References:
  - librosa.feature.rms / spectral_centroid docs
  - craigfrancis/audio-detect pattern for whistle isolation
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Any

import numpy as np

try:
    import librosa
except ImportError as exc:  # pragma: no cover
    raise ImportError("librosa is required: pip install librosa") from exc

from preprocessor.config import (
    AUDIO_SR,
    AUDIO_HOP_LENGTH,
    AUDIO_N_FFT,
    WHISTLE_MIN_FREQ_HZ,
    WHISTLE_MAX_FREQ_HZ,
    WHISTLE_RMS_PERCENTILE,
    WHISTLE_MIN_DURATION_S,
    WHISTLE_CONFIDENCE_WEIGHT,
    FFMPEG_BIN,
)

logger = logging.getLogger(__name__)


def _extract_audio_wav(video_path: str, wav_path: str) -> bool:
    """Extract mono 22 kHz PCM audio from *video_path* to *wav_path*."""
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i", video_path,
        "-ac", "1",
        "-ar", str(AUDIO_SR),
        "-vn",
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        logger.error("FFmpeg audio extract failed: %s", result.stderr.decode())
        return False
    return True


def _merge_intervals(intervals: List[tuple], gap_s: float = 0.5) -> List[tuple]:
    """Merge overlapping / nearby half-open intervals (start, end)."""
    if not intervals:
        return []
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1] + gap_s:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class AudioAnalyzer:
    """Detect referee whistle events in a video file."""

    def __init__(
        self,
        sample_rate: int = AUDIO_SR,
        hop_length: int = AUDIO_HOP_LENGTH,
        n_fft: int = AUDIO_N_FFT,
        whistle_min_hz: float = WHISTLE_MIN_FREQ_HZ,
        whistle_max_hz: float = WHISTLE_MAX_FREQ_HZ,
        rms_percentile: float = WHISTLE_RMS_PERCENTILE,
        min_duration_s: float = WHISTLE_MIN_DURATION_S,
    ) -> None:
        self.sr = sample_rate
        self.hop_length = hop_length
        self.n_fft = n_fft
        self.whistle_min_hz = whistle_min_hz
        self.whistle_max_hz = whistle_max_hz
        self.rms_percentile = rms_percentile
        self.min_duration_s = min_duration_s
        # Seconds per analysis frame
        self._frame_duration = hop_length / sample_rate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, video_path: str) -> List[Dict[str, Any]]:
        """Return list of whistle detections for *video_path*.

        Each detection is:
            {start_time, end_time, confidence, whistle_confidence}
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            wav_path = tmp.name

        if not _extract_audio_wav(video_path, wav_path):
            logger.warning("Could not extract audio from %s", video_path)
            return []

        try:
            return self._detect_whistles(wav_path)
        finally:
            Path(wav_path).unlink(missing_ok=True)

    def analyze_wav(self, wav_path: str) -> List[Dict[str, Any]]:
        """Analyze a pre-extracted WAV file (useful for testing)."""
        return self._detect_whistles(wav_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_whistles(self, wav_path: str) -> List[Dict[str, Any]]:
        y, sr = librosa.load(wav_path, sr=self.sr, mono=True)

        # 1) Short-time RMS energy
        rms = librosa.feature.rms(y=y, frame_length=self.n_fft,
                                   hop_length=self.hop_length)[0]

        # 2) Spectral centroid (Hz per frame)
        centroid = librosa.feature.spectral_centroid(
            y=y, sr=sr, n_fft=self.n_fft, hop_length=self.hop_length
        )[0]

        # 3) Band-limited energy in whistle range
        stft = np.abs(librosa.stft(y, n_fft=self.n_fft,
                                    hop_length=self.hop_length))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=self.n_fft)
        band_mask = (freqs >= self.whistle_min_hz) & (freqs <= self.whistle_max_hz)
        band_energy = stft[band_mask, :].mean(axis=0)

        # 4) Threshold: RMS energy spike + centroid in whistle band
        rms_thresh = np.percentile(rms, self.rms_percentile)
        band_thresh = np.percentile(band_energy, self.rms_percentile)

        # Use an absolute minimum energy floor so that:
        #   a) Silence (RMS ≈ 0) is never flagged as a spike.
        #   b) Whistles embedded in mostly-silent audio still get detected
        #      (the percentile-based threshold collapses to near-zero when the
        #      majority of the recording is quiet).
        MIN_ENERGY = 1e-3
        rms_thresh = max(float(rms_thresh), MIN_ENERGY)
        band_thresh = max(float(band_thresh), MIN_ENERGY)

        is_spike = (rms >= rms_thresh) & (band_energy >= band_thresh)

        # 5) Group consecutive spike frames into intervals
        raw_intervals: List[tuple] = []
        in_event = False
        event_start = 0
        for i, spike in enumerate(is_spike):
            t = i * self._frame_duration
            if spike and not in_event:
                event_start = t
                in_event = True
            elif not spike and in_event:
                raw_intervals.append((event_start, t))
                in_event = False
        if in_event:
            raw_intervals.append((event_start, len(is_spike) * self._frame_duration))

        # 6) Merge nearby intervals and filter short ones
        merged = _merge_intervals(raw_intervals, gap_s=0.5)
        merged = [(s, e) for s, e in merged
                  if (e - s) >= self.min_duration_s]

        # 7) Score each event
        results: List[Dict[str, Any]] = []
        for start, end in merged:
            s_frame = int(start / self._frame_duration)
            e_frame = min(int(end / self._frame_duration), len(rms) - 1)
            # Confidence: how far above threshold the peak energy is
            peak_rms = rms[s_frame:e_frame + 1].max()
            raw_conf = min(1.0, float(peak_rms / rms_thresh) - 1.0)
            # Scale to [0.5, 1.0] range for detected events
            confidence = 0.5 + 0.5 * min(raw_conf, 1.0)
            results.append({
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "confidence": round(confidence, 4),
                "whistle_confidence": round(confidence, 4),
            })

        logger.info("AudioAnalyzer: %d whistle events detected in %s",
                    len(results), wav_path)
        return results
