#!/usr/bin/env python3
"""
TEAM-GRADE Local Processing Pipeline - Main Entry Point
Example script demonstrating full workflow: video → frames → pose → reps
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import cv2

# Add processing to path
processing_path = Path(__file__).parent / "processing"
sys.path.insert(0, str(processing_path.parent))

from processing import (
    setup_logging, get_project_structure, extract_frames_from_video,
    PoseEstimator, TorsoCropper, JerseyOCR, RepExtractor, Frame,
    load_json, save_json, ensure_directory
)

logger = logging.getLogger(__name__)


def process_video(
    video_path: Path,
    output_dir: Optional[Path] = None,
    skip_steps: list = None,
    max_frames: Optional[int] = None
) -> dict:
    """
    Process video through full pipeline.

    Args:
        video_path: Path to input video
        output_dir: Output directory (default: project structure)
        skip_steps: List of steps to skip ('frames', 'pose', 'torso', 'ocr', 'reps')
        max_frames: Maximum frames to process

    Returns:
        Processing results dictionary
    """
    if skip_steps is None:
        skip_steps = []

    # Initialize
    dirs = output_dir or get_project_structure()
    results = {
        "video": str(video_path),
        "steps_completed": [],
        "errors": []
    }

    logger.info(f"Starting pipeline for {video_path.name}")

    try:
        # Step 1: Extract frames
        if "frames" not in skip_steps:
            logger.info("Step 1/5: Extracting frames...")
            frame_paths = extract_frames_from_video(
                video_path,
                dirs["extracted_frames"],
                stride=1,
                max_frames=max_frames
            )
            results["extracted_frames"] = len(frame_paths)
            results["steps_completed"].append("frame_extraction")
            logger.info(f"✓ Extracted {len(frame_paths)} frames")
        else:
            logger.info("Skipping frame extraction")
            frame_paths = sorted(dirs["extracted_frames"].glob("frame_*.jpg"))

        # Step 2: Pose estimation
        if "pose" not in skip_steps:
            logger.info("Step 2/5: Estimating poses...")
            pose_estimator = PoseEstimator("mediapipe", device="cpu")
            poses = []

            for i, frame_path in enumerate(frame_paths):
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    logger.warning(f"Failed to load frame: {frame_path}")
                    continue

                pose = pose_estimator.estimate_pose(frame)
                poses.append({
                    "frame_id": i,
                    "frame_path": str(frame_path),
                    "pose": pose
                })

                if (i + 1) % 50 == 0:
                    logger.info(f"Processed {i + 1}/{len(frame_paths)} frames")

            pose_estimator.close()
            results["pose_frames"] = len(poses)
            results["steps_completed"].append("pose_estimation")
            logger.info(f"✓ Estimated poses for {len(poses)} frames")
        else:
            logger.info("Skipping pose estimation")
            poses = []

        # Step 3: Torso cropping
        if "torso" not in skip_steps:
            logger.info("Step 3/5: Cropping torsos...")
            cropper = TorsoCropper()
            torso_count = 0

            for pose_data in poses:
                frame_path = Path(pose_data["frame_path"])
                frame = cv2.imread(str(frame_path))
                keypoints = pose_data["pose"]["keypoints"]

                crop = cropper.crop_torso(frame, keypoints)
                if crop is not None:
                    saved = cropper.save_crop(
                        crop,
                        dirs["torso_crops"],
                        pose_data["frame_id"]
                    )
                    if saved:
                        torso_count += 1

            results["torso_crops"] = torso_count
            results["steps_completed"].append("torso_cropping")
            logger.info(f"✓ Extracted {torso_count} torso crops")
        else:
            logger.info("Skipping torso cropping")

        # Step 4: Jersey OCR
        if "ocr" not in skip_steps:
            logger.info("Step 4/5: Reading jersey numbers...")
            ocr = JerseyOCR("easyocr", language="en")
            jerseys = {}

            for crop_path in sorted(dirs["torso_crops"].glob("torso_frame_*.jpg")):
                crop = cv2.imread(str(crop_path))
                if crop is None:
                    continue

                jersey, confidence = ocr.read_jersey_number(crop)
                jerseys[crop_path.name] = {
                    "jersey": jersey,
                    "confidence": float(confidence)
                }

            ocr.close()
            
            # Save jersey results
            jersey_output = dirs["torso_crops"] / "jersey_results.json"
            save_json(jerseys, jersey_output)
            results["jerseys_detected"] = len([j for j in jerseys.values() if j["jersey"]])
            results["steps_completed"].append("jersey_ocr")
            logger.info(f"✓ Detected jerseys in {results['jerseys_detected']} crops")
        else:
            logger.info("Skipping jersey OCR")

        # Step 5: Rep extraction (placeholder)
        if "reps" not in skip_steps:
            logger.info("Step 5/5: Extracting reps...")
            extractor = RepExtractor()
            
            # For demo, create Frame objects from pose data
            frame_objects = []
            for pose_data in poses:
                frame_obj = Frame(
                    frame_id=pose_data["frame_id"],
                    timestamp=pose_data["frame_id"] / 30.0,  # Assume 30fps
                    track_id=None,
                    jersey_number=None,
                    jersey_confidence=0.0,
                    position="UNKNOWN",
                    keypoints=pose_data["pose"]["keypoints"]
                )
                frame_objects.append(frame_obj)

            reps = extractor.extract_reps(frame_objects)
            results["reps_extracted"] = len(reps)
            results["steps_completed"].append("rep_extraction")
            logger.info(f"✓ Extracted {len(reps)} reps")
            
            # Save rep summary
            rep_output = dirs["pose_outputs"] / "reps_summary.json"
            rep_data = [
                {
                    "rep_id": r.rep_id,
                    "start_frame": r.start_frame,
                    "end_frame": r.end_frame,
                    "duration": r.duration_frames,
                    "player_id": r.player_id,
                    "jersey": r.jersey_number,
                    "position": r.position
                }
                for r in reps
            ]
            save_json({"reps": rep_data}, rep_output)
        else:
            logger.info("Skipping rep extraction")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        results["errors"].append(str(e))

    logger.info(f"Pipeline complete. Completed steps: {results['steps_completed']}")
    return results


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TEAM-GRADE Local Processing Pipeline"
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Path to input video file"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: ./team-grade-processing)"
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum frames to process"
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        default=[],
        choices=["frames", "pose", "torso", "ocr", "reps"],
        help="Steps to skip"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = parser.parse_args()

    # Setup logging
    dirs = get_project_structure() if args.output is None else {"logs": args.output / "logs"}
    ensure_directory(dirs["logs"])
    setup_logging(dirs["logs"], args.log_level)

    logger.info("=" * 60)
    logger.info("TEAM-GRADE Local Processing Pipeline")
    logger.info("=" * 60)

    # Validate video
    if not args.video.exists():
        logger.error(f"Video not found: {args.video}")
        return 1

    # Run pipeline
    results = process_video(
        args.video,
        output_dir=get_project_structure() if args.output is None else {"root": args.output},
        skip_steps=args.skip,
        max_frames=args.max_frames
    )

    # Summary
    logger.info("=" * 60)
    logger.info("Pipeline Summary:")
    logger.info(f"  Video: {results['video']}")
    logger.info(f"  Completed steps: {len(results['steps_completed'])}")
    if results["steps_completed"]:
        logger.info(f"  Details: {results}")
    if results["errors"]:
        logger.warning(f"  Errors: {results['errors']}")
    logger.info("=" * 60)

    return 0 if not results["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
