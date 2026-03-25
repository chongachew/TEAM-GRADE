import cv2
import numpy as np
from yolov5 import YOLOv5

# Load the YOLO model
model = YOLOv5.load('yolov5s.pt')

# Function to test YOLO detection on frames
def test_yolo_on_frames(video_path, num_frames=10):
    cap = cv2.VideoCapture(video_path)

    frame_count = 0
    while cap.isOpened() and frame_count < num_frames:
        ret, frame = cap.read()
        if not ret:
            print("Could not read frame from video.")
            break

        # Perform inference
        results = model.predict(frame)

        # Display frame with detections
        model.show_results(frame, results)
        frame_count += 1

    cap.release()

if __name__ == '__main__':
    video_path = 'path/to/your/video.mp4'  # Change this to your video path
    test_yolo_on_frames(video_path)