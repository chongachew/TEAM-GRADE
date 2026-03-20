#!/usr/bin/env python3
"""
TEAM-GRADE Local Processing Pipeline - Quick Start Examples
Runnable example scripts for common use cases
"""

import sys
from pathlib import Path

# Add processing to path
sys.path.insert(0, str(Path(__file__).parent))

from processing import (
    PoseEstimator, TorsoCropper, JerseyOCR, RepExtractor,
    extract_frames_from_video, load_json, setup_logging,
    get_project_structure, ensure_directory, Frame
)
import cv2
import logging

logger = logging.getLogger(__name__)


def example_1_single_frame_pose():
    """Example 1: Process a single frame for pose estimation."""
    print("\n" + "=" * 60)
    print("Example 1: Single Frame Pose Estimation")
    print("=" * 60)

    # Load a frame
    dirs = get_project_structure()
    frame_files = sorted(dirs["extracted_frames"].glob("frame_*.jpg"))

    if not frame_files:
        print("No frames found. Run frame extraction first.")
        return

    frame_path = frame_files[0]
    frame = cv2.imread(str(frame_path))

    # Initialize pose estimator
    estimator = PoseEstimator("mediapipe", device="cpu")

    # Estimate pose
    result = estimator.estimate_pose(frame)
    print(f"✓ Estimated pose for {frame_path.name}")
    print(f"  Keypoints detected: {len(result['keypoints'])}")
    print(f"  Confidence avg: {result['confidence']:.3f}")

    estimator.close()


def example_2_batch_pose():
    """Example 2: Process multiple frames in batch."""
    print("\n" + "=" * 60)
    print("Example 2: Batch Pose Estimation")
    print("=" * 60)

    dirs = get_project_structure()
    frame_files = sorted(dirs["extracted_frames"].glob("frame_*.jpg"))[:10]

    if not frame_files:
        print("No frames found. Run frame extraction first.")
        return

    # Load frames
    frames = []
    for fp in frame_files:
        frame = cv2.imread(str(fp))
        if frame is not None:
            frames.append(frame)

    # Batch estimate
    estimator = PoseEstimator("mediapipe", device="cpu")
    results = estimator.batch_estimate(frames)
    print(f"✓ Estimated poses for {len(results)} frames")
    for i, r in enumerate(results):
        print(f"  Frame {i}: {len(r['keypoints'])} keypoints")

    estimator.close()


def example_3_torso_crop():
    """Example 3: Crop torso from frame."""
    print("\n" + "=" * 60)
    print("Example 3: Torso Extraction")
    print("=" * 60)

    dirs = get_project_structure()

    # Get pose first
    frame_files = sorted(dirs["extracted_frames"].glob("frame_*.jpg"))
    if not frame_files:
        print("No frames found.")
        return

    frame = cv2.imread(str(frame_files[0]))
    estimator = PoseEstimator("mediapipe", device="cpu")
    pose_result = estimator.estimate_pose(frame)
    estimator.close()

    # Crop torso
    cropper = TorsoCropper()
    crop = cropper.crop_torso(frame, pose_result["keypoints"])

    if crop is not None:
        saved = cropper.save_crop(crop, dirs["torso_crops"], 0)
        print(f"✓ Torso cropped and saved: {saved}")
    else:
        print("✗ Failed to extract torso (low confidence keypoints?)")


def example_4_jersey_ocr():
    """Example 4: Read jersey number from crop."""
    print("\n" + "=" * 60)
    print("Example 4: Jersey Number Detection")
    print("=" * 60)

    dirs = get_project_structure()
    crop_files = sorted(dirs["torso_crops"].glob("torso_*.jpg"))

    if not crop_files:
        print("No torso crops found. Run torso cropping first.")
        return

    ocr = JerseyOCR("easyocr", language="en")
    crop = cv2.imread(str(crop_files[0]))

    jersey, confidence = ocr.read_jersey_number(crop)
    print(f"✓ Jersey detected: {jersey} (confidence: {confidence:.3f})")

    # Show all detections for debugging
    print("\nAll text detections:")
    all_text = ocr.read_all_text(crop)
    for text_item in all_text[:5]:  # First 5
        print(f"  - '{text_item}'")

    ocr.close()


def example_5_rep_extraction():
    """Example 5: Extract reps from frames."""
    print("\n" + "=" * 60)
    print("Example 5: Rep Extraction")
    print("=" * 60)

    dirs = get_project_structure()
    frame_files = sorted(dirs["extracted_frames"].glob("frame_*.jpg"))[:50]

    if not frame_files:
        print("No frames found.")
        return

    # Generate frame objects with pose
    estimator = PoseEstimator("mediapipe", device="cpu")
    frame_objects = []

    for i, fp in enumerate(frame_files):
        frame = cv2.imread(str(fp))
        pose = estimator.estimate_pose(frame)

        frame_obj = Frame(
            frame_id=i,
            timestamp=i / 30.0,  # 30fps
            track_id=None,
            jersey_number=None,
            jersey_confidence=0.0,
            position="QB",
            keypoints=pose["keypoints"]
        )
        frame_objects.append(frame_obj)

    estimator.close()

    # Extract reps
    extractor = RepExtractor()
    reps = extractor.extract_reps(frame_objects)

    print(f"✓ Extracted {len(reps)} reps from {len(frame_objects)} frames")
    for rep in reps[:5]:  # Show first 5
        print(f"  Rep {rep.rep_id}: frames {rep.start_frame}-{rep.end_frame} "
              f"({rep.duration_frames}f)")


def example_6_full_pipeline():
    """Example 6: Full pipeline on single video."""
    print("\n" + "=" * 60)
    print("Example 6: Full Pipeline (Video → Results)")
    print("=" * 60)

    # NOTE: This is a simplified demo. Run main.py for full implementation
    print("For full pipeline, use:")
    print("  python main.py path/to/video.mp4")
    print("\nOr with options:")
    print("  python main.py video.mp4 --max-frames 100 --log-level DEBUG")


def main():
    """Run examples."""
    import argparse

    parser = argparse.ArgumentParser("TEAM-GRADE Pipeline Examples")
    parser.add_argument(
        "example",
        type=int,
        nargs="?",
        default=0,
        help="Example to run (1-6, or 0 for all)"
    )

    args = parser.parse_args()

    # Setup logging
    dirs = get_project_structure()
    ensure_directory(dirs["logs"])
    setup_logging(dirs["logs"], "INFO")

    examples = {
        1: ("Single Frame Pose", example_1_single_frame_pose),
        2: ("Batch Pose Estimation", example_2_batch_pose),
        3: ("Torso Cropping", example_3_torso_crop),
        4: ("Jersey OCR", example_4_jersey_ocr),
        5: ("Rep Extraction", example_5_rep_extraction),
        6: ("Full Pipeline", example_6_full_pipeline),
    }

    if args.example == 0:
        print("\nTEAM-GRADE Pipeline Examples\n")
        print("Available examples:")
        for num, (name, _) in examples.items():
            print(f"  {num}. {name}")
        print("\nUsage: python examples.py [1-6]")
        print("       python examples.py   # Run all")
    else:
        if args.example not in examples:
            print(f"Unknown example: {args.example}")
            return 1

        name, func = examples[args.example]
        print(f"\nRunning: {name}")
        try:
            func()
            print("\n✓ Example completed successfully")
        except Exception as e:
            print(f"\n✗ Error: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
