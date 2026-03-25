"""
CLI entry point for the pre-ingestion segmentation pipeline.

Usage examples::

    # Local file
    python preprocessor/cli.py \\
        --video /data/games/game.mp4 \\
        --output_dir /data/segments/

    # GCS paths (requires gcloud auth)
    python preprocessor/cli.py \\
        --video gs://bucket/game.mp4 \\
        --output_dir gs://bucket/segments/ \\
        --game_id abc123 \\
        --use_firestore

    # Dry-run (detect only, no FFmpeg cuts)
    python preprocessor/cli.py --video game.mp4 --output_dir /tmp/out --dry_run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("preprocessor.cli")

# ---------------------------------------------------------------------------
# GCS helpers (optional — only needed for gs:// paths)
# ---------------------------------------------------------------------------

def _is_gcs(path: str) -> bool:
    return path.startswith("gs://")


def _gcs_download(gcs_path: str, local_path: str) -> bool:
    import subprocess
    result = subprocess.run(
        ["gsutil", "cp", gcs_path, local_path], capture_output=True
    )
    if result.returncode != 0:
        logger.error("gsutil download failed: %s", result.stderr.decode())
        return False
    return True


def _gcs_upload(local_path: str, gcs_path: str) -> bool:
    import subprocess
    result = subprocess.run(
        ["gsutil", "cp", local_path, gcs_path], capture_output=True
    )
    if result.returncode != 0:
        logger.error("gsutil upload failed: %s", result.stderr.decode())
        return False
    return True


# ---------------------------------------------------------------------------
# Core run logic
# ---------------------------------------------------------------------------

def run_pipeline(
    video_path: str,
    output_dir: str,
    game_id: str,
    dry_run: bool = False,
    use_firestore: bool = False,
    skip_audio: bool = False,
    skip_scene: bool = False,
) -> list:
    """Run full segmentation pipeline.  Returns list of metadata dicts."""

    from preprocessor.audio_analyzer import AudioAnalyzer
    from preprocessor.motion_analyzer import MotionAnalyzer
    from preprocessor.scene_analyzer import SceneAnalyzer
    from preprocessor.field_detector import FieldDetector
    from preprocessor.segmenter_fusion import SegmenterFusion
    from preprocessor.ffmpeg_cutter import FFmpegCutter
    from preprocessor.segment_metadata import SegmentMetadataWriter

    local_video = video_path
    tmp_dir = None

    # Download from GCS if needed
    if _is_gcs(video_path):
        tmp_dir = tempfile.mkdtemp(prefix="teamgrade_")
        local_video = os.path.join(tmp_dir, "game.mp4")
        logger.info("Downloading %s → %s", video_path, local_video)
        if not _gcs_download(video_path, local_video):
            sys.exit(1)

    local_output = output_dir
    if _is_gcs(output_dir):
        tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="teamgrade_")
        local_output = os.path.join(tmp_dir, "segments")
    os.makedirs(local_output, exist_ok=True)

    # --- Signal extraction (can run in any order) ----------------------
    logger.info("=== Audio analysis ===")
    whistle_events = []
    if not skip_audio:
        analyzer = AudioAnalyzer()
        whistle_events = analyzer.analyze(local_video)
        logger.info("Whistle events: %d", len(whistle_events))

    logger.info("=== Motion analysis ===")
    motion_analyzer = MotionAnalyzer()
    motion_bursts = motion_analyzer.analyze(local_video)
    logger.info("Motion bursts: %d", len(motion_bursts))

    logger.info("=== Scene analysis ===")
    scene_boundaries = []
    if not skip_scene:
        scene_analyzer = SceneAnalyzer()
        scene_boundaries = scene_analyzer.analyze(local_video)
        logger.info("Scene boundaries: %d", len(scene_boundaries))

    logger.info("=== Field detection ===")
    field_detector = FieldDetector()
    field_scores = field_detector.analyze(local_video)
    logger.info("Field frames scored: %d", len(field_scores))

    # --- Fusion --------------------------------------------------------
    logger.info("=== Signal fusion ===")
    fusion = SegmenterFusion()
    candidates = fusion.fuse(
        whistle_events, motion_bursts, scene_boundaries, field_scores
    )
    logger.info("Segment candidates after fusion: %d", len(candidates))

    if not candidates:
        logger.warning("No play segments detected.  Exiting.")
        return []

    gcs_base = output_dir.rstrip("/") if _is_gcs(output_dir) else ""
    writer = SegmentMetadataWriter(
        output_dir=local_output,
        game_id=game_id,
        gcs_base_path=gcs_base,
        use_firestore=use_firestore,
    )
    cand_dicts = [c.to_dict() for c in candidates]

    if not dry_run:
        logger.info("=== Cutting segments ===")
        cutter = FFmpegCutter()
        cutter.cut_all(local_video, local_output, cand_dicts)

    logger.info("=== Writing metadata ===")
    metadata_list = writer.write_all(cand_dicts)

    # Upload to GCS if needed
    if _is_gcs(output_dir) and not dry_run:
        logger.info("=== Uploading to GCS ===")
        for f in Path(local_output).glob("*"):
            _gcs_upload(str(f), f"{output_dir.rstrip('/')}/{f.name}")

    logger.info("=== Done: %d segments ===", len(metadata_list))
    return metadata_list


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="TEAM-GRADE pre-ingestion video segmenter"
    )
    p.add_argument("--video", required=True,
                   help="Input video path (local or gs://...)")
    p.add_argument("--output_dir", required=True,
                   help="Output directory for segments (local or gs://...)")
    p.add_argument("--game_id", default=None,
                   help="Game ID used for Firestore and file naming (default: stem of video filename)")
    p.add_argument("--use_firestore", action="store_true",
                   help="Enqueue segment metadata to Firestore")
    p.add_argument("--dry_run", action="store_true",
                   help="Detect segments but skip FFmpeg cutting")
    p.add_argument("--skip_audio", action="store_true",
                   help="Skip audio/whistle analysis")
    p.add_argument("--skip_scene", action="store_true",
                   help="Skip PySceneDetect scene boundary analysis")
    p.add_argument("--log_level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    game_id = args.game_id or Path(args.video).stem

    results = run_pipeline(
        video_path=args.video,
        output_dir=args.output_dir,
        game_id=game_id,
        dry_run=args.dry_run,
        use_firestore=args.use_firestore,
        skip_audio=args.skip_audio,
        skip_scene=args.skip_scene,
    )

    if results:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
