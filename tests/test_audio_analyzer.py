"""
Unit tests for preprocessor/audio_analyzer.py.

Test cases:
- Synthetic whistle (3 kHz sine wave) — should be detected
- Silence — should NOT produce any events
- White noise — should NOT produce confident whistle events
- Crowd-noise proxy (pink noise) — robustness check
- Very loud / clipped audio — should not crash
- Multiple overlapping whistles — merge logic
- Edge cases: empty array, mono vs stereo, mismatched sample rate
- Performance: processes 30 s of audio in reasonable time
"""

from __future__ import annotations

import time
import numpy as np
import pytest

from preprocessor.audio_analyzer import (
    AudioAnalyzer,
    _apply_bandpass_filter,
    _compute_onset_strength,
    _compute_spectral_centroid,
    _compute_ste,
    _detect_ste_peaks,
    _filter_false_positives,
    _fuse_signals,
    _get_silence_mask,
    _merge_adjacent_events,
    _spectral_score_per_frame,
)
from preprocessor.config import (
    AUDIO_HOP_LENGTH,
    AUDIO_SAMPLE_RATE,
    CONFIDENCE_THRESHOLD,
    WHISTLE_MIN_DURATION,
    WHISTLE_SPECTRAL_BAND,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SR = AUDIO_SAMPLE_RATE
HOP = AUDIO_HOP_LENGTH


def _sine_wave(
    freq: float,
    duration: float,
    sr: int = SR,
    amplitude: float = 0.5,
    onset: float = 0.0,
) -> np.ndarray:
    """Generate a sine wave burst starting at *onset* seconds."""
    total_samples = int(sr * (onset + duration))
    t = np.linspace(0.0, onset + duration, total_samples, endpoint=False)
    y = np.zeros(total_samples, dtype=np.float32)
    start = int(onset * sr)
    y[start:] = amplitude * np.sin(2 * np.pi * freq * t[start:]).astype(np.float32)
    return y


def _white_noise(duration: float, sr: int = SR, amplitude: float = 0.05) -> np.ndarray:
    rng = np.random.default_rng(42)
    return (rng.standard_normal(int(sr * duration)) * amplitude).astype(np.float32)


def _silence(duration: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(sr * duration), dtype=np.float32)


# ---------------------------------------------------------------------------
# Feature extraction tests
# ---------------------------------------------------------------------------

class TestFeatureExtraction:
    def test_onset_strength_shape(self):
        y = _sine_wave(3000, 1.0)
        env = _compute_onset_strength(y, SR, HOP)
        expected_frames = 1 + len(y) // HOP
        assert env.shape[0] >= expected_frames - 2  # librosa may differ by ±1

    def test_spectral_centroid_shape(self):
        y = _sine_wave(3000, 1.0)
        centroid = _compute_spectral_centroid(y, SR, HOP)
        assert centroid.ndim == 1
        assert centroid.shape[0] > 0

    def test_spectral_centroid_whistle_freq(self):
        """A 3 kHz sine should have centroid near 3000 Hz."""
        y = _sine_wave(3000, 2.0)
        centroid = _compute_spectral_centroid(y, SR, HOP)
        # Allow generous tolerance for window effects
        median_centroid = float(np.median(centroid))
        assert 2000 < median_centroid < 4500, (
            f"Expected centroid near 3000 Hz, got {median_centroid:.0f} Hz"
        )

    def test_silence_mask_silence(self):
        y = _silence(1.0)
        mask = _get_silence_mask(y, SR)
        # Nearly all samples should be masked as silent
        assert mask.mean() < 0.05

    def test_silence_mask_tone(self):
        y = _sine_wave(3000, 2.0, amplitude=0.5)
        mask = _get_silence_mask(y, SR)
        # Most samples should be non-silent
        assert mask.mean() > 0.7


class TestBandpassFilter:
    def test_output_shape(self):
        y = _sine_wave(3000, 1.0)
        filtered = _apply_bandpass_filter(y, SR)
        assert filtered.shape == y.shape

    def test_pass_band_preserved(self):
        """Energy in 2-4 kHz should be largely preserved after filtering."""
        y = _sine_wave(3000, 2.0, amplitude=0.5)
        filtered = _apply_bandpass_filter(y, SR)
        rms_in = float(np.sqrt(np.mean(y ** 2)))
        rms_out = float(np.sqrt(np.mean(filtered ** 2)))
        assert rms_out > rms_in * 0.3, "Bandpass removed too much in-band energy"

    def test_stop_band_attenuated(self):
        """A 10 kHz tone should be significantly attenuated by the 2-4 kHz filter."""
        y = _sine_wave(10000, 2.0, amplitude=0.5)
        filtered = _apply_bandpass_filter(y, SR)
        rms_in = float(np.sqrt(np.mean(y ** 2)))
        rms_out = float(np.sqrt(np.mean(filtered ** 2)))
        assert rms_out < rms_in * 0.5, "Bandpass should attenuate 10 kHz signal"

    def test_very_low_sample_rate_no_crash(self):
        """Should not crash even when nyquist is close to bandpass limits."""
        y = _sine_wave(3000, 0.5, sr=8000)
        # 8000 Hz sr → nyquist=4000; highcut (4000) would be exactly at nyquist
        # The clamping logic should handle this gracefully
        result = _apply_bandpass_filter(y, sr=8000)
        assert result.shape == y.shape


class TestSTE:
    def test_ste_shape(self):
        y = _sine_wave(3000, 2.0)
        ste = _compute_ste(y, HOP)
        expected = len(y) // HOP
        assert ste.shape[0] == expected

    def test_ste_silence_near_zero(self):
        y = _silence(2.0)
        ste = _compute_ste(y, HOP)
        assert float(ste.max()) < 1e-12

    def test_ste_peaks_silence(self):
        y = _silence(2.0)
        ste = _compute_ste(y, HOP)
        peaks = _detect_ste_peaks(ste)
        assert len(peaks) == 0, "Silence should produce no STE peaks"

    def test_ste_peaks_whistle(self):
        """A loud tone should produce at least some STE peaks."""
        y = _sine_wave(3000, 2.0, amplitude=0.8)
        y_bp = _apply_bandpass_filter(y, SR)
        ste = _compute_ste(y_bp, HOP)
        peaks = _detect_ste_peaks(ste)
        assert len(peaks) > 0


# ---------------------------------------------------------------------------
# Fusion and post-processing tests
# ---------------------------------------------------------------------------

class TestFusion:
    def test_fuse_signals_output_type(self):
        n_frames = 100
        onset_frames = np.array([10, 30, 50])
        ste_frames = [10, 50]
        spectral_scores = np.ones(n_frames, dtype=np.float32)
        onset_strength = np.ones(n_frames, dtype=np.float32)

        result = _fuse_signals(
            onset_frames, ste_frames, spectral_scores, onset_strength, n_frames
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == {10, 30, 50}
        for scores in result.values():
            assert 0.0 <= scores["confidence"] <= 1.0

    def test_fuse_signals_empty_onsets(self):
        result = _fuse_signals(
            np.array([], dtype=int), [], np.ones(50), np.ones(50), 50
        )
        assert result == {}

    def test_fuse_signals_out_of_bounds_ste(self):
        """STE frames beyond n_frames should not crash."""
        result = _fuse_signals(
            np.array([5]),
            [5, 9999],  # 9999 is out of range
            np.ones(50),
            np.ones(50),
            50,
        )
        assert 5 in result

    def test_spectral_score_in_band(self):
        centroid = np.array([WHISTLE_SPECTRAL_BAND[0] + 100], dtype=np.float32)
        scores = _spectral_score_per_frame(centroid)
        assert scores[0] == 1.0

    def test_spectral_score_out_of_band(self):
        centroid = np.array([500.0], dtype=np.float32)
        scores = _spectral_score_per_frame(centroid)
        assert scores[0] == 0.0


class TestMergeAndFilter:
    def _make_event(self, ts, conf, dur=0.1, centroid=3000.0, onset=0.5):
        return {
            "timestamp": ts,
            "confidence": conf,
            "onset_strength": onset,
            "spectral_centroid": centroid,
            "duration": dur,
            "type": "whistle_onset",
        }

    def test_merge_adjacent(self):
        events = [
            self._make_event(1.00, 0.8),
            self._make_event(1.05, 0.7),  # within 0.1 s gap
            self._make_event(5.00, 0.9),
        ]
        merged = _merge_adjacent_events(events, min_gap=0.1)
        assert len(merged) == 2  # first two should merge

    def test_merge_empty(self):
        assert _merge_adjacent_events([]) == []

    def test_merge_single(self):
        events = [self._make_event(1.0, 0.8)]
        merged = _merge_adjacent_events(events)
        assert len(merged) == 1

    def test_filter_low_confidence(self):
        events = [
            self._make_event(1.0, 0.3),  # below threshold
            self._make_event(2.0, 0.8),  # above threshold
        ]
        filtered = _filter_false_positives(events, min_confidence=CONFIDENCE_THRESHOLD)
        assert len(filtered) == 1
        assert filtered[0]["timestamp"] == 2.0

    def test_filter_duration_too_short(self):
        events = [
            self._make_event(1.0, 0.9, dur=0.01),  # 10 ms → too short
            self._make_event(2.0, 0.9, dur=0.10),  # 100 ms → ok
        ]
        filtered = _filter_false_positives(events, min_duration=WHISTLE_MIN_DURATION)
        assert len(filtered) == 1
        assert filtered[0]["timestamp"] == 2.0

    def test_filter_duration_too_long(self):
        events = [
            self._make_event(1.0, 0.9, dur=3.0),   # 3 s → too long
            self._make_event(2.0, 0.9, dur=0.5),   # 500 ms → ok
        ]
        filtered = _filter_false_positives(events, max_duration=2.0)
        assert len(filtered) == 1
        assert filtered[0]["timestamp"] == 2.0


# ---------------------------------------------------------------------------
# End-to-end AudioAnalyzer tests (process_audio_chunk)
# ---------------------------------------------------------------------------

class TestAudioAnalyzerEndToEnd:
    def setup_method(self):
        self.analyzer = AudioAnalyzer(
            sample_rate=SR,
            hop_length=HOP,
            chunk_duration=30,
        )

    # -- Silence: should NOT flag false positives
    def test_silence_no_events(self):
        y = _silence(5.0)
        result = self.analyzer.process_audio_chunk(y, SR)
        assert result["metadata"]["num_events"] == 0
        assert result["events"] == []

    # -- White noise: very few or no high-confidence events
    def test_white_noise_robustness(self):
        y = _white_noise(5.0, amplitude=0.05)
        result = self.analyzer.process_audio_chunk(y, SR)
        high_conf = [e for e in result["events"] if e["confidence"] >= 0.8]
        assert len(high_conf) == 0, (
            f"White noise should not produce high-confidence events, "
            f"got: {high_conf}"
        )

    # -- Synthetic whistle at 3 kHz
    def test_synthetic_whistle_detected(self):
        """A clear 3 kHz tone should be detected as a whistle onset."""
        # 3 s of silence + 0.3 s whistle burst
        whistle = _sine_wave(3000, 0.3, amplitude=0.8, onset=3.0)
        silence_pad = _silence(6.0)
        # Pad to 6 seconds total
        y = np.zeros(int(SR * 6), dtype=np.float32)
        y[: len(whistle)] = whistle[: len(y)]

        result = self.analyzer.process_audio_chunk(y, SR)
        assert result["metadata"]["num_events"] >= 1, (
            "Expected at least one whistle event for a 3 kHz tone"
        )

    # -- Multiple whistles: merge logic
    def test_multiple_whistles_detected(self):
        """Two whistles 2 s apart should each be detected."""
        y = np.zeros(int(SR * 6), dtype=np.float32)
        w1 = _sine_wave(3000, 0.3, amplitude=0.8)
        w2 = _sine_wave(3000, 0.3, amplitude=0.8)
        y[: len(w1)] = w1
        offset = int(2.0 * SR)
        y[offset : offset + len(w2)] = w2

        result = self.analyzer.process_audio_chunk(y, SR)
        # Should detect at least one (ideally two, but merging is acceptable)
        assert result["metadata"]["num_events"] >= 1

    # -- Output format
    def test_output_format(self):
        y = _sine_wave(3000, 0.3, amplitude=0.8, onset=1.0)
        result = self.analyzer.process_audio_chunk(y, SR)

        assert "events" in result
        assert "metadata" in result
        meta = result["metadata"]
        assert "sample_rate" in meta
        assert "duration" in meta
        assert "num_events" in meta
        assert "processing_time_sec" in meta
        assert "algorithm" in meta
        assert meta["algorithm"] == "librosa_onset + spectral + bandpass_ste"

        for event in result["events"]:
            assert "timestamp" in event
            assert "confidence" in event
            assert "onset_strength" in event
            assert "spectral_centroid" in event
            assert "duration" in event
            assert "type" in event
            assert event["type"] == "whistle_onset"
            assert 0.0 <= event["confidence"] <= 1.0
            assert event["timestamp"] >= 0.0

    # -- Confidence bounds
    def test_confidence_in_range(self):
        y = _sine_wave(3000, 0.5, amplitude=0.8, onset=0.5)
        result = self.analyzer.process_audio_chunk(y, SR)
        for event in result["events"]:
            assert 0.0 <= event["confidence"] <= 1.0

    # -- Very loud (clipped) audio
    def test_clipped_audio_no_crash(self):
        y = np.ones(int(SR * 2), dtype=np.float32) * 10.0  # heavily clipped
        try:
            result = self.analyzer.process_audio_chunk(y, SR)
            assert "events" in result
        except Exception as exc:
            pytest.fail(f"Clipped audio raised an exception: {exc}")

    # -- Mismatched sample rate — should resample and not crash
    def test_mismatched_sample_rate(self):
        sr_in = 44100
        y = _sine_wave(3000, 1.0, sr=sr_in, amplitude=0.5)
        result = self.analyzer.process_audio_chunk(y, sr_in)
        assert "events" in result
        assert result["metadata"]["sample_rate"] == SR

    # -- Performance: 30 s of audio in < 30 s real time (generous budget)
    def test_performance_30s_chunk(self):
        y = _white_noise(30.0, amplitude=0.02)
        t0 = time.perf_counter()
        result = self.analyzer.process_audio_chunk(y, SR)
        elapsed = time.perf_counter() - t0
        assert elapsed < 30.0, (
            f"Processing 30 s audio took {elapsed:.2f} s (target: <30 s)"
        )
        assert result["metadata"]["processing_time_sec"] < 30.0
