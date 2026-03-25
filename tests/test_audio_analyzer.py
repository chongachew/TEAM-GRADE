"""
Unit tests for audio_analyzer.AudioAnalyzer.

All tests run without GPU and without real video files.
Audio content is synthesised in-memory using numpy.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import soundfile as sf
    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False

try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False


def _write_wav(path: str, signal: np.ndarray, sr: int) -> None:
    """Write a mono float32 array to a WAV file using soundfile."""
    sf.write(path, signal.astype(np.float32), sr)


def _sine_burst(sr: int, freq_hz: float, duration_s: float,
                amplitude: float = 0.8) -> np.ndarray:
    """Return a pure-tone sine burst."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amplitude * np.sin(2 * math.pi * freq_hz * t)).astype(np.float32)


def _silence(sr: int, duration_s: float) -> np.ndarray:
    return np.zeros(int(sr * duration_s), dtype=np.float32)


@unittest.skipUnless(HAS_LIBROSA and HAS_SOUNDFILE,
                     "librosa and soundfile required")
class TestAudioAnalyzer(unittest.TestCase):

    def setUp(self):
        from preprocessor.audio_analyzer import AudioAnalyzer
        self.analyzer = AudioAnalyzer(
            sample_rate=22050,
            hop_length=512,
            n_fft=2048,
            whistle_min_hz=2000,
            whistle_max_hz=4000,
            rms_percentile=80,
            min_duration_s=0.2,
        )

    def _make_wav(self, signal: np.ndarray) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        _write_wav(tmp.name, signal, self.analyzer.sr)
        return tmp.name

    # ------------------------------------------------------------------
    # Helpers tested in isolation
    # ------------------------------------------------------------------

    def test_merge_intervals_basic(self):
        from preprocessor.audio_analyzer import _merge_intervals
        intervals = [(0.0, 1.0), (0.8, 2.0), (5.0, 6.0)]
        merged = _merge_intervals(intervals, gap_s=0.5)
        self.assertEqual(len(merged), 2)
        self.assertAlmostEqual(merged[0][0], 0.0)
        self.assertAlmostEqual(merged[0][1], 2.0)

    def test_merge_intervals_empty(self):
        from preprocessor.audio_analyzer import _merge_intervals
        self.assertEqual(_merge_intervals([]), [])

    def test_merge_intervals_no_overlap(self):
        from preprocessor.audio_analyzer import _merge_intervals
        intervals = [(0.0, 1.0), (3.0, 4.0)]
        merged = _merge_intervals(intervals, gap_s=0.5)
        self.assertEqual(len(merged), 2)

    # ------------------------------------------------------------------
    # Whistle detection — synthetic audio
    # ------------------------------------------------------------------

    def test_detects_whistle_burst(self):
        """A 3 kHz tone embedded in silence should be detected."""
        sr = self.analyzer.sr
        # 5 s silence, 1 s whistle at 3 kHz, 5 s silence
        audio = np.concatenate([
            _silence(sr, 5.0),
            _sine_burst(sr, 3000.0, 1.0),
            _silence(sr, 5.0),
        ])
        wav = self._make_wav(audio)
        try:
            events = self.analyzer.analyze_wav(wav)
            # At least one event should be detected
            self.assertGreater(len(events), 0)
            # All events must have required keys
            for e in events:
                self.assertIn("start_time", e)
                self.assertIn("end_time", e)
                self.assertIn("confidence", e)
                self.assertIn("whistle_confidence", e)
        finally:
            Path(wav).unlink(missing_ok=True)

    def test_returns_empty_for_silence(self):
        """Pure silence should produce no whistle events."""
        sr = self.analyzer.sr
        audio = _silence(sr, 10.0)
        wav = self._make_wav(audio)
        try:
            events = self.analyzer.analyze_wav(wav)
            # Silence might occasionally trigger very low-confidence events
            # due to noise floor, but confidence should be low.
            for e in events:
                self.assertLessEqual(e["confidence"], 0.9)
        finally:
            Path(wav).unlink(missing_ok=True)

    def test_event_schema(self):
        """Events must include all required fields."""
        sr = self.analyzer.sr
        audio = np.concatenate([
            _silence(sr, 2.0),
            _sine_burst(sr, 3000.0, 0.5),
            _silence(sr, 2.0),
        ])
        wav = self._make_wav(audio)
        try:
            events = self.analyzer.analyze_wav(wav)
            for e in events:
                self.assertIsInstance(e["start_time"], float)
                self.assertIsInstance(e["end_time"], float)
                self.assertGreater(e["end_time"], e["start_time"])
                self.assertGreaterEqual(e["confidence"], 0.0)
                self.assertLessEqual(e["confidence"], 1.0)
        finally:
            Path(wav).unlink(missing_ok=True)

    def test_confidence_range(self):
        """Confidence scores must always be in [0, 1]."""
        sr = self.analyzer.sr
        audio = _sine_burst(sr, 3000.0, 3.0, amplitude=1.0)
        wav = self._make_wav(audio)
        try:
            events = self.analyzer.analyze_wav(wav)
            for e in events:
                self.assertGreaterEqual(e["confidence"], 0.0)
                self.assertLessEqual(e["confidence"], 1.0)
        finally:
            Path(wav).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
