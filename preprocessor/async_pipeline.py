"""
Async pipeline — CPU-GPU pipelining for the GTX 1650 / i3-9100F.

Problem:
  A naïve sequential pipeline stalls the GPU while FFmpeg decodes frames (CPU
  work) and stalls the CPU while the GPU runs inference.

Solution (producer-consumer with bounded queues):
  - Thread A (FFmpeg): decodes low-res frames → puts batches onto frame_queue
  - Thread B (GPU inference): pulls batches, runs field/motion; results → result_queue
  - Main thread: drains result_queue and accumulates signals

This keeps the GPU busy between I/O pauses and avoids OOM on 8 GB VRAM by
enforcing BATCH_SIZE=4.

Usage::

    pipeline = AsyncPipeline(video_path)
    signals = pipeline.run()
    # signals = {"field_scores": [...], "motion_bursts": [...]}
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise ImportError("opencv-python is required: pip install opencv-python") from exc

from preprocessor.config import (
    SAMPLE_WIDTH,
    SAMPLE_HEIGHT,
    SAMPLE_FPS,
    BATCH_SIZE,
    USE_FP16,
    FFMPEG_BIN,
    MOTION_DIFF_THRESHOLD,
    FIELD_HSV_LOWER,
    FIELD_HSV_UPPER,
)
from preprocessor.field_detector import green_coverage

logger = logging.getLogger(__name__)

_SENTINEL = None  # signals end-of-stream to consumer threads


def _decode_producer(
    video_path: str,
    frame_queue: "queue.Queue[Optional[List[np.ndarray]]]",
    batch_size: int,
) -> None:
    """FFmpeg → raw frames → batched onto *frame_queue* (runs in its own thread)."""
    vf = f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT},fps={SAMPLE_FPS}"
    cmd = [
        FFMPEG_BIN,
        "-i", video_path,
        "-vf", vf,
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-an",
        "pipe:1",
    ]
    frame_size = SAMPLE_WIDTH * SAMPLE_HEIGHT * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    batch: List[np.ndarray] = []
    try:
        while True:
            chunk = proc.stdout.read(frame_size)
            if len(chunk) < frame_size:
                break
            frame = np.frombuffer(chunk, dtype=np.uint8).reshape(
                (SAMPLE_HEIGHT, SAMPLE_WIDTH, 3)
            )
            batch.append(frame)
            if len(batch) >= batch_size:
                frame_queue.put(batch)
                batch = []
        if batch:
            frame_queue.put(batch)
    finally:
        proc.stdout.close()
        proc.wait()
        frame_queue.put(_SENTINEL)


def _inference_consumer(
    frame_queue: "queue.Queue[Optional[List[np.ndarray]]]",
    result_queue: "queue.Queue[Dict[str, Any]]",
) -> None:
    """Consume batches of frames; compute motion diff + field coverage."""
    prev_gray: Optional[np.ndarray] = None
    frame_idx = 0
    scoreboard_rows = max(1, int(SAMPLE_HEIGHT * 0.10))

    while True:
        batch = frame_queue.get()
        if batch is _SENTINEL:
            result_queue.put(_SENTINEL)
            break

        for frame in batch:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            cropped = gray[scoreboard_rows:, :]

            # Motion diff
            motion_score = 0.0
            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, cropped)
                motion_score = float(diff.mean())
            prev_gray = cropped

            # Field coverage (every frame — cheap HSV op)
            field_cov = green_coverage(frame, FIELD_HSV_LOWER, FIELD_HSV_UPPER)

            result_queue.put({
                "frame_idx": frame_idx,
                "time": frame_idx / SAMPLE_FPS,
                "motion_score": motion_score,
                "field_presence": round(field_cov, 4),
            })
            frame_idx += 1


class AsyncPipeline:
    """CPU-GPU pipeline that decodes and analyses frames concurrently."""

    def __init__(
        self,
        video_path: str,
        batch_size: int = BATCH_SIZE,
        queue_depth: int = 8,
    ) -> None:
        self.video_path = video_path
        self.batch_size = batch_size
        self.queue_depth = queue_depth

    def run(self) -> Dict[str, List[Any]]:
        """Run the pipeline and return {motion_scores, field_scores}.

        Results can be fed directly into MotionAnalyzer / FieldDetector
        aggregation methods.
        """
        frame_queue: queue.Queue = queue.Queue(maxsize=self.queue_depth)
        result_queue: queue.Queue = queue.Queue()

        producer = threading.Thread(
            target=_decode_producer,
            args=(self.video_path, frame_queue, self.batch_size),
            daemon=True,
            name="ffmpeg-producer",
        )
        consumer = threading.Thread(
            target=_inference_consumer,
            args=(frame_queue, result_queue),
            daemon=True,
            name="gpu-consumer",
        )

        producer.start()
        consumer.start()

        motion_scores: List[Dict[str, Any]] = []
        field_scores: List[Dict[str, Any]] = []

        while True:
            item = result_queue.get()
            if item is _SENTINEL:
                break
            motion_scores.append({
                "time": item["time"],
                "motion_score": item["motion_score"],
                "frame_idx": item["frame_idx"],
            })
            field_scores.append({
                "time": item["time"],
                "field_presence": item["field_presence"],
            })

        producer.join(timeout=600)
        consumer.join(timeout=60)

        logger.info(
            "AsyncPipeline: processed %d frames from %s",
            len(motion_scores), self.video_path,
        )
        return {
            "motion_scores": motion_scores,
            "field_scores": field_scores,
        }
