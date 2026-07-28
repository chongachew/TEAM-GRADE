"""
Regression test: frame extraction's downsampling rate calculation used to be
`frame_skip_interval = int(video_fps / fps)`, which truncates rather than
rounds. For the single most common real-world camera/phone frame rate,
29.97fps (NTSC), `int(29.97 / 15) == 1` - the "skip interval" collapsed to 1,
meaning every extraction path silently stopped downsampling at all and wrote
frames at the full native ~30fps instead of the intended 15fps.

This didn't just waste storage/compute - it desynced the extracted
frame_index sequence from BoxOverlay.tsx's fixed
`Math.floor(currentTime * FRAME_EXTRACTION_FPS)` assumption on the frontend,
which is what made the live box overlay's boxes visibly lag behind the
players ("ghost boxes over empty background"): at 2x the intended sampling
density, the frontend was requesting roughly half the correct frame_index
for any playback position beyond the first instant.

Same bug, same fix (a real-elapsed-time accumulator instead of a truncated
integer stride), existed in three places - CPUFrameExtractor and
GPUFrameExtractor in ingest/gpu_utils/frame_extractor.py, and the
non-abstracted loop in ingest/stages/frame_extraction_stage.py.
"""

import cv2
import numpy as np

from ingest.gpu_utils.frame_extractor import CPUFrameExtractor


def _write_synthetic_video(path, num_frames, fps, size=(64, 64)):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    for i in range(num_frames):
        frame = np.full((size[1], size[0], 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


class TestFrameExtractionFpsSampling:
    def test_ntsc_29_97fps_source_actually_downsamples_to_15fps(self, tmp_path):
        """The bug's exact reproduction case: a 29.97fps source with a 15fps
        target must still downsample (extract roughly half as many frames as
        the source has), not silently extract every native frame."""
        video_path = tmp_path / "ntsc.mp4"
        num_source_frames = 90
        _write_synthetic_video(video_path, num_frames=num_source_frames, fps=29.97)
        output_dir = tmp_path / "frames"

        extractor = CPUFrameExtractor(video_path)
        frame_count, frames_metadata = extractor.extract_frames(fps=15, output_dir=output_dir)

        assert frame_count > 0
        # Under the old int(29.97/15)==1 bug this would equal num_source_frames
        # (no downsampling at all). A correct ~15fps sample of a ~3-second
        # 29.97fps clip should land close to 45, well under the source count.
        assert frame_count < num_source_frames * 0.75, (
            f"expected real downsampling toward 15fps, got {frame_count} frames "
            f"from a {num_source_frames}-frame 29.97fps source (no skip applied?)"
        )

    def test_sampled_frame_timestamps_stay_evenly_spaced_near_target_interval(self, tmp_path):
        """The accumulator must not drift: consecutive sampled frames' real
        timestamps should stay close to 1/fps apart throughout, not just on
        average - a fixed integer stride computed from a rounded ratio could
        still satisfy an average check while drifting frame-to-frame."""
        video_path = tmp_path / "odd_rate.mp4"
        _write_synthetic_video(video_path, num_frames=120, fps=24)
        output_dir = tmp_path / "frames"

        extractor = CPUFrameExtractor(video_path)
        frame_count, frames_metadata = extractor.extract_frames(fps=15, output_dir=output_dir)

        timestamps = [m["timestamp_seconds"] for m in frames_metadata]
        target_interval = 1.0 / 15
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        assert all(gap <= target_interval * 2 for gap in gaps), (
            f"sampled frame gaps drifted past 2x the target interval: {gaps}"
        )
