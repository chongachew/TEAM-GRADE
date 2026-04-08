import cv2
import numpy as np
from ultralytics import YOLO
import os
import sys

# Function to test YOLO detection on frames
def test_yolo_on_frames(video_path, model_path='yolov8n-pose.pt', num_frames=10):
    """
    Test YOLO pose detection on video frames.

    Args:
        video_path: Path to the video file
        model_path: Path to the YOLO model (default: yolov8n-pose.pt for pose detection)
        num_frames: Number of frames to process (default: 10)
    """
    # Validate video path
    if not os.path.exists(video_path):
        print(f"Error: Video file not found at '{video_path}'")
        return False

    # Load the YOLO model
    try:
        model = YOLO(model_path)
        print(f"Successfully loaded model: {model_path}")
    except Exception as e:
        print(f"Error loading YOLO model: {e}")
        return False

    # Open video capture
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video file '{video_path}'")
        return False

    print(f"Processing up to {num_frames} frames from '{video_path}'...")

    frame_count = 0
    try:
        while cap.isOpened() and frame_count < num_frames:
            ret, frame = cap.read()
            if not ret:
                print(f"Finished processing video (read {frame_count} frames)")
                break

            # Perform inference
            results = model(frame, verbose=False)

            # Render results on frame
            annotated_frame = results[0].plot()

            # Display frame with detections
            cv2.imshow('YOLO Pose Detection', annotated_frame)

            # Print detection info
            if results[0].keypoints is not None:
                num_people = len(results[0].keypoints)
                print(f"Frame {frame_count + 1}: Detected {num_people} person(s)")

            frame_count += 1

            # Press 'q' to quit early
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("Stopped by user")
                break

    except Exception as e:
        print(f"Error during video processing: {e}")
        return False
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"Successfully processed {frame_count} frames")
    return True

if __name__ == '__main__':
    # Get video path from command line argument or use default
    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    else:
        print("Usage: python test_yolo_only.py <video_path> [model_path] [num_frames]")
        print("Example: python test_yolo_only.py video.mp4 yolov8n-pose.pt 20")
        sys.exit(1)

    # Optional: Get model path from command line
    model_path = sys.argv[2] if len(sys.argv) > 2 else 'yolov8n-pose.pt'

    # Optional: Get number of frames from command line
    num_frames = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    success = test_yolo_on_frames(video_path, model_path, num_frames)
    sys.exit(0 if success else 1)