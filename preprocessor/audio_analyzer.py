"""
Optimized audio whistle detection module for TEAM-GRADE Phase 2.

Algorithm sources:
- Librosa official onset detection (spectral flux)
- Academic: "Automated Referee Whistle Sound Detection for Extraction
  of Highlights from Sports Video" (2-4 kHz bandpass + STE, 92%+ accuracy)
- Production patterns from AudioClassification-Pytorch (modular design)
"""

from __future__ import annotations

import logging
import time
from typing import Any

import librosa
import numpy as np
import scipy.signal as signal

from preprocessor.config import (
    AUDIO_CHUNK_DURATION,
    AUDIO_FUSION_WEIGHTS,
    AUDIO_HOP_LENGTH,
    AUDIO_N_FFT,
    AUDIO_SAMPLE_RATE,
    CONFIDENCE_THRESHOLD,
    EVENT_MERGE_GAP,
    ONSET_DISTANCE,
    ONSET_THRESHOLD,
    WHISTLE_BANDPASS_HIGH,
    WHISTLE_BANDPASS_LOW,
    WHISTLE_BANDPASS_ORDER,
    WHISTLE_ENERGY_FLOOR,
    WHISTLE_ENERGY_PERCENTILE,
    WHISTLE_MAX_DURATION,
    WHISTLE_MIN_DURATION,
    WHISTLE_SPECTRAL_BAND,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature extraction helpers
# ---------------------------------------------------------------------------

def _compute_onset_strength(
    y: np.ndarray,
    sr: int,
    hop_length: int,
) -> np.ndarray:
    """Return the per-frame onset strength envelope (spectral flux).

    Uses librosa's built-in ``onset_strength`` which is normalized and
    suitable for ``peak_pick`` / ``onset_detect`` downstream.
    """
    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)


def _compute_spectral_centroid(
    y: np.ndarray,
    sr: int,
    hop_length: int,
) -> np.ndarray:
    """Return per-frame spectral centroid in Hz (1-D array, frames)."""
    centroid = librosa.feature.spectral_centroid(
        y=y, sr=sr, n_fft=AUDIO_N_FFT, hop_length=hop_length
    )
    return centroid[0]  # shape: (n_frames,)


def _get_silence_mask(
    y: np.ndarray,
    sr: int,
    top_db: float = 60.0,
) -> np.ndarray:
    """Return a boolean mask (True = non-silent) at the *sample* level.

    Internally uses ``librosa.effects.split`` to find non-silent intervals
    and reconstructs a sample-level boolean array.

    Uses ``ref=1.0`` so that the dB threshold is absolute (not relative to
    the signal peak), which avoids false non-silence on all-zero audio.
    """
    # Fast path: entirely silent by amplitude
    if float(np.abs(y).max()) < WHISTLE_ENERGY_FLOOR:
        return np.zeros(len(y), dtype=bool)

    intervals = librosa.effects.split(y, top_db=top_db, ref=1.0)
    mask = np.zeros(len(y), dtype=bool)
    for start, end in intervals:
        mask[start:end] = True
    return mask


# ---------------------------------------------------------------------------
# Whistle-specific feature extraction (academic + scipy)
# ---------------------------------------------------------------------------

def _apply_bandpass_filter(
    y: np.ndarray,
    sr: int,
    lowcut: float = WHISTLE_BANDPASS_LOW,
    highcut: float = WHISTLE_BANDPASS_HIGH,
    order: int = WHISTLE_BANDPASS_ORDER,
) -> np.ndarray:
    """Apply a Butterworth bandpass FIR/IIR filter to isolate the whistle band.

    Based on academic research showing referee whistles concentrate energy
    in the 2–4 kHz range.
    """
    nyquist = 0.5 * sr
    low = lowcut / nyquist
    high = highcut / nyquist
    # Clamp to valid range (0, 1) exclusive — avoid degenerate filters
    low = float(np.clip(low, 1e-4, 1.0 - 1e-4))
    high = float(np.clip(high, 1e-4, 1.0 - 1e-4))
    if low >= high:
        logger.warning("Bandpass low >= high after clamping; skipping filter.")
        return y
    b, a = signal.butter(order, [low, high], btype="band")
    return signal.filtfilt(b, a, y).astype(y.dtype)


def _compute_ste(y: np.ndarray, hop_length: int) -> np.ndarray:
    """Compute Short-Time Energy (STE) over non-overlapping frames.

    STE is the mean squared amplitude per frame — sensitive to energy spikes
    characteristic of whistle blows.
    """
    n_frames = len(y) // hop_length
    ste = np.array(
        [
            np.mean(y[i * hop_length : (i + 1) * hop_length] ** 2)
            for i in range(n_frames)
        ],
        dtype=np.float32,
    )
    return ste


def _detect_ste_peaks(
    ste: np.ndarray,
    threshold_percentile: float = WHISTLE_ENERGY_PERCENTILE,
    energy_floor: float = WHISTLE_ENERGY_FLOOR,
) -> list[int]:
    """Return frame indices where STE exceeds the percentile threshold.

    Both conditions must be satisfied:
    - STE > ``energy_floor``  (avoids silent-audio false positives)
    - STE > ``threshold_percentile``-th percentile of all STE values
    """
    if ste.max() < energy_floor:
        return []
    threshold = float(np.percentile(ste, threshold_percentile))
    threshold = max(threshold, energy_floor)
    peaks = np.where(ste > threshold)[0].tolist()
    return peaks


# ---------------------------------------------------------------------------
# Detection & fusion
# ---------------------------------------------------------------------------

def _spectral_score_per_frame(
    spectral_centroid: np.ndarray,
    band_low: float = WHISTLE_SPECTRAL_BAND[0],
    band_high: float = WHISTLE_SPECTRAL_BAND[1],
) -> np.ndarray:
    """Score each frame 0–1 based on whether its centroid is in the whistle band."""
    in_band = (spectral_centroid >= band_low) & (spectral_centroid <= band_high)
    return in_band.astype(np.float32)


def _fuse_signals(
    onset_frames: np.ndarray,
    ste_frames: list[int],
    spectral_scores: np.ndarray,
    onset_strength: np.ndarray,
    n_frames: int,
    weights: dict[str, float] | None = None,
) -> dict[int, dict[str, float]]:
    """Fuse onset, STE, and spectral centroid signals into per-frame scores.

    Returns a mapping ``{frame_index: {confidence, onset_strength, spectral_centroid_score}}``.
    Only frames in *onset_frames* are considered as candidate events.
    """
    if weights is None:
        weights = AUDIO_FUSION_WEIGHTS

    # Build STE score array (1 if frame is an STE peak, else 0)
    ste_mask = np.zeros(n_frames, dtype=np.float32)
    for f in ste_frames:
        if f < n_frames:
            ste_mask[f] = 1.0

    # Normalise onset strength to [0, 1]
    onset_norm = np.zeros(n_frames, dtype=np.float32)
    if onset_strength.max() > 0:
        vals = onset_strength[:n_frames]
        onset_norm[: len(vals)] = vals / vals.max()

    # Align spectral scores length
    spec = np.zeros(n_frames, dtype=np.float32)
    spec[: len(spectral_scores)] = spectral_scores[: n_frames]

    events: dict[int, dict[str, float]] = {}
    for f in onset_frames:
        if f >= n_frames:
            continue
        confidence = (
            weights.get("onset", 0.40) * float(onset_norm[f])
            + weights.get("ste", 0.35) * float(ste_mask[f])
            + weights.get("spectral", 0.25) * float(spec[f])
        )
        events[int(f)] = {
            "confidence": float(np.clip(confidence, 0.0, 1.0)),
            "onset_strength": float(onset_norm[f]),
            "spectral_score": float(spec[f]),
            "ste_score": float(ste_mask[f]),
        }
    return events


def _merge_adjacent_events(
    events: list[dict[str, Any]],
    min_gap: float = EVENT_MERGE_GAP,
) -> list[dict[str, Any]]:
    """Merge whistle events whose timestamps are closer than *min_gap* seconds.

    When merging, the highest-confidence event's timestamp is kept and
    confidence values are averaged.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e["timestamp"])
    merged: list[dict[str, Any]] = [sorted_events[0]]

    for evt in sorted_events[1:]:
        prev = merged[-1]
        gap = evt["timestamp"] - prev["timestamp"]
        if gap <= min_gap:
            # Keep timestamp from higher-confidence event; average scores
            if evt["confidence"] > prev["confidence"]:
                prev["timestamp"] = evt["timestamp"]
            prev["confidence"] = float(np.clip(
                (prev["confidence"] + evt["confidence"]) / 2.0, 0.0, 1.0
            ))
            prev["onset_strength"] = (prev["onset_strength"] + evt["onset_strength"]) / 2.0
            prev["spectral_centroid"] = (prev["spectral_centroid"] + evt["spectral_centroid"]) / 2.0
            prev["duration"] = max(prev["duration"], evt["duration"])
        else:
            merged.append(dict(evt))

    return merged


def _filter_false_positives(
    events: list[dict[str, Any]],
    min_confidence: float = CONFIDENCE_THRESHOLD,
    min_duration: float = WHISTLE_MIN_DURATION,
    max_duration: float = WHISTLE_MAX_DURATION,
) -> list[dict[str, Any]]:
    """Remove events that fail confidence or duration constraints."""
    return [
        e for e in events
        if (
            e["confidence"] >= min_confidence
            and e["duration"] >= min_duration
            and e["duration"] <= max_duration
        )
    ]


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class AudioAnalyzer:
    """Whistle detection pipeline using librosa onset detection + bandpass STE.

    The algorithm has three stages:
    1. Feature extraction — onset strength, spectral centroid, silence mask
    2. Whistle-band STE — bandpass filter → STE → peak detection
    3. Fusion & post-processing — merge adjacent, filter false positives
    """

    def __init__(
        self,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        hop_length: int = AUDIO_HOP_LENGTH,
        chunk_duration: float = AUDIO_CHUNK_DURATION,
    ) -> None:
        self.sample_rate = sample_rate
        self.hop_length = hop_length
        self.chunk_duration = chunk_duration
        logger.info(
            "AudioAnalyzer initialised (sr=%d, hop=%d, chunk=%.0fs)",
            sample_rate,
            hop_length,
            chunk_duration,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_file(self, video_path: str) -> dict[str, Any]:
        """Load audio from *video_path* and return all detected whistle events.

        Supports MP4, WAV, and any format readable by librosa/ffmpeg.

        Returns:
            A dict with ``"events"`` and ``"metadata"`` keys matching the
            specified output format.
        """
        t0 = time.perf_counter()
        logger.info("Loading audio from %s", video_path)

        y, sr = librosa.load(video_path, sr=self.sample_rate, mono=True)
        audio_duration = float(len(y)) / sr
        logger.info("Loaded %.1f s of audio (sr=%d)", audio_duration, sr)

        events = self._detect_whistles(y, sr)
        processing_time = time.perf_counter() - t0
        logger.info(
            "Detected %d whistle events in %.2f s", len(events), processing_time
        )

        return {
            "events": events,
            "metadata": {
                "sample_rate": sr,
                "duration": audio_duration,
                "num_events": len(events),
                "processing_time_sec": round(processing_time, 3),
                "algorithm": "librosa_onset + spectral + bandpass_ste",
            },
        }

    def process_audio_chunk(self, audio: np.ndarray, sr: int) -> dict[str, Any]:
        """Detect whistles in a raw audio array (streaming / batch usage).

        Args:
            audio: 1-D float32 audio samples.
            sr:    Sample rate of *audio*.

        Returns:
            Same format as :meth:`process_file`.
        """
        t0 = time.perf_counter()
        # Resample if needed
        if sr != self.sample_rate:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=self.sample_rate)
            sr = self.sample_rate

        audio_duration = float(len(audio)) / sr
        events = self._detect_whistles(audio, sr)
        processing_time = time.perf_counter() - t0

        return {
            "events": events,
            "metadata": {
                "sample_rate": sr,
                "duration": audio_duration,
                "num_events": len(events),
                "processing_time_sec": round(processing_time, 3),
                "algorithm": "librosa_onset + spectral + bandpass_ste",
            },
        }

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _detect_whistles(
        self, y: np.ndarray, sr: int
    ) -> list[dict[str, Any]]:
        """Core detection pipeline operating on a full audio array."""
        hop = self.hop_length

        # 1. Silence mask — skip completely silent regions early
        silence_mask = _get_silence_mask(y, sr)
        if not silence_mask.any():
            logger.debug("Audio is entirely silent — no events.")
            return []

        # 2. Librosa onset strength + onset frames
        onset_env = _compute_onset_strength(y, sr, hop)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop,
            normalize=True,
            sparse=True,
            backtrack=False,
            # peak_pick kwargs passed via **kwargs:
            delta=ONSET_THRESHOLD,
            wait=ONSET_DISTANCE,
        )

        if len(onset_frames) == 0:
            logger.debug("No onsets detected.")
            return []

        # 3. Spectral centroid
        spectral_centroid_hz = _compute_spectral_centroid(y, sr, hop)
        spectral_scores = _spectral_score_per_frame(spectral_centroid_hz)

        # 4. Bandpass filter → STE → peaks
        y_bandpass = _apply_bandpass_filter(y, sr)
        ste = _compute_ste(y_bandpass, hop)
        ste_peak_frames = _detect_ste_peaks(ste)

        # 5. Fuse signals
        n_frames = len(onset_env)
        fused = _fuse_signals(
            onset_frames=onset_frames,
            ste_frames=ste_peak_frames,
            spectral_scores=spectral_scores,
            onset_strength=onset_env,
            n_frames=n_frames,
        )

        # 6. Convert frame indices → timed events
        # Build a set of STE peak frames for fast lookup
        ste_peak_set = set(ste_peak_frames)
        events: list[dict[str, Any]] = []
        for frame, scores in fused.items():
            timestamp = librosa.frames_to_time(frame, sr=sr, hop_length=hop)

            # Estimate event duration from the span of adjacent STE peaks
            # around this onset frame. Fall back to one n_fft window.
            lo, hi = frame, frame
            while lo - 1 in ste_peak_set:
                lo -= 1
            while hi + 1 in ste_peak_set:
                hi += 1
            duration = float((hi - lo + 1) * hop) / sr
            # Ensure at least one STFT window worth of duration
            min_single_frame = float(AUDIO_N_FFT) / sr
            duration = max(duration, min_single_frame)

            centroid_hz = float(
                spectral_centroid_hz[frame]
                if frame < len(spectral_centroid_hz)
                else 0.0
            )

            events.append(
                {
                    "timestamp": round(float(timestamp), 4),
                    "confidence": scores["confidence"],
                    "onset_strength": round(scores["onset_strength"], 4),
                    "spectral_centroid": round(centroid_hz, 2),
                    "duration": round(duration, 4),
                    "type": "whistle_onset",
                }
            )

        # 7. Post-processing
        events = _merge_adjacent_events(events)
        events = _filter_false_positives(events)

        return events
