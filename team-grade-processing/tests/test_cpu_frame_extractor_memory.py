"""
Regression test for a real production OOM: CPUFrameExtractor.extract_frames
used to decode every sampled frame into a Python list (frames_to_write)
before writing any of them to disk, holding the entire video's frames in
memory at once. A ~4-minute video at 15fps (~3800 frames) blew well past the
worker's 2GB Fargate container, killed with exit 137
("OutOfMemoryError: container killed due to memory usage") - confirmed via
CloudWatch on 2026-07-21. Fixed to submit each frame to the write pool as
soon as it's decoded, instead of collecting them all first.

This test can't easily assert peak RSS in CI, so it instead asserts the
*shape* of the fix directly: frames are submitted to the executor before
the decode loop finishes, not after - proven by counting how many futures
exist while decoding is still in progress.
"""

from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pytest

from ingest.gpu_utils.frame_extractor import CPUFrameExtractor


def _write_synthetic_video(path, num_frames=60, fps=30, size=(64, 64)):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    for i in range(num_frames):
        frame = np.full((size[1], size[0], 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


class TestCPUFrameExtractorMemory:
    def test_extracts_all_sampled_frames_and_writes_them_to_disk(self, tmp_path):
        video_path = tmp_path / "synthetic.mp4"
        _write_synthetic_video(video_path, num_frames=60, fps=30)
        output_dir = tmp_path / "frames"

        extractor = CPUFrameExtractor(video_path)
        frame_count, frames_metadata = extractor.extract_frames(fps=15, output_dir=output_dir)

        assert frame_count > 0
        assert len(frames_metadata) == frame_count
        written_files = list(output_dir.glob("*.jpg"))
        assert len(written_files) == frame_count
        for meta in frames_metadata:
            assert "path" in meta and "timestamp_seconds" in meta

    def test_frames_are_submitted_to_write_pool_while_still_decoding(self, tmp_path, monkeypatch):
        """Distinguishes the fix from the old bug directly: record how many
        cap.read() calls had happened at the moment each frame is submitted
        to the write pool. Under the old bug, every submission happened only
        after the decode loop fully finished, so every recorded count would
        equal the final total. Under the fix, submissions happen throughout
        decoding, so the recorded counts vary."""
        video_path = tmp_path / "synthetic.mp4"
        _write_synthetic_video(video_path, num_frames=90, fps=30)
        output_dir = tmp_path / "frames"

        read_count = {"n": 0}
        real_read = cv2.VideoCapture.read

        def counting_read(self):
            read_count["n"] += 1
            return real_read(self)

        monkeypatch.setattr(cv2.VideoCapture, "read", counting_read)

        read_count_at_submit = []
        real_submit = ThreadPoolExecutor.submit

        def recording_submit(self, fn, *args, **kwargs):
            read_count_at_submit.append(read_count["n"])
            return real_submit(self, fn, *args, **kwargs)

        monkeypatch.setattr(ThreadPoolExecutor, "submit", recording_submit)

        extractor = CPUFrameExtractor(video_path)
        frame_count, _ = extractor.extract_frames(fps=15, output_dir=output_dir)

        assert frame_count > 0
        assert len(read_count_at_submit) == frame_count
        # The old bug would make every entry equal read_count["n"] (the
        # final total) since nothing was submitted until decoding finished.
        assert len(set(read_count_at_submit)) > 1, (
            "All frames were submitted at the same read-count - frames are "
            "still being buffered before writing starts, not streamed"
        )
