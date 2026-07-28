"""
Tests for processing/play_boundary_detection.py's OCR frame-sampling
(real production fix, 2026-07-27): running OCR on every extracted frame
instead of a 1fps sample was the actual bottleneck in play_detection on a
real video (4,681 EasyOCR calls for a signal that only changes a few dozen
times) - this was never how Phase A validated the signal in the first
place.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from processing import play_boundary_detection as pbd


def _fake_paths(n):
    return [Path(f"frame_{i:06d}.jpg") for i in range(n)]


class _FakePool:
    def __init__(self, calls, processes=None, initializer=None):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def imap(self, func, iterable, chunksize=4):
        items = list(iterable)
        self._calls.append(items)
        # Real _ocr_one_frame return shape: (frame_index, down_distance, clock).
        return [(pbd.frame_index_from_name(Path(p)), None, None) for p in items]


def test_ocr_only_scans_a_1fps_sample_not_every_frame(monkeypatch):
    # 150 frames at the real production default (15fps) = 10 real seconds
    # of video - at the default OCR_SAMPLE_FPS=1, only 10 frames should
    # actually get OCR'd, not all 150.
    monkeypatch.setattr(pbd, "OCR_SAMPLE_FPS", 1.0)
    frame_paths = _fake_paths(150)
    calls = []
    monkeypatch.setattr(pbd, "Pool", lambda *a, **k: _FakePool(calls, *a, **k))

    pbd.extract_ocr_candidates(frame_paths, fps=15.0)

    assert len(calls) == 1
    assert len(calls[0]) == 10  # 150 frames / (15fps / 1fps sample) = 10


def test_ocr_sample_fps_matching_frame_rate_scans_every_frame(monkeypatch):
    """OCR_SAMPLE_FPS == fps means step=1 - every frame, same as before
    this change existed (a real opt-out, not just a default)."""
    monkeypatch.setattr(pbd, "OCR_SAMPLE_FPS", 15.0)
    frame_paths = _fake_paths(30)
    calls = []
    monkeypatch.setattr(pbd, "Pool", lambda *a, **k: _FakePool(calls, *a, **k))

    pbd.extract_ocr_candidates(frame_paths, fps=15.0)

    assert len(calls[0]) == 30


def test_empty_frame_paths_returns_empty_without_touching_pool(monkeypatch):
    pool_mock = MagicMock()
    monkeypatch.setattr(pbd, "Pool", pool_mock)

    assert pbd.extract_ocr_candidates([], fps=15.0) == []
    pool_mock.assert_not_called()
