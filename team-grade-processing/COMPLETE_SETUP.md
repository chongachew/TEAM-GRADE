# TEAM-GRADE Local Processing Pipeline - Complete Setup & Usage

Comprehensive guide for setting up and using the local video processing pipeline.

## Table of Contents

1. [Installation](#installation)
2. [Quick Start (5 minutes)](#quick-start)
3. [Scripts Overview](#scripts-overview)
4. [Usage Examples](#usage-examples)
5. [Module Reference](#module-reference)
6. [Configuration](#configuration)
7. [Troubleshooting](#troubleshooting)
8. [Performance](#performance)

---

## Installation

### System Requirements

- **Python**: 3.8+ (tested on 3.10+)
- **RAM**: 4GB minimum, 16GB recommended
- **GPU**: Optional but recommended (NVIDIA CUDA 11.0+)
- **Disk**: 10GB+ for video and frame storage

### Step 1: Set up Python Environment

```bash
# Navigate to team-grade-processing directory
cd team-grade-processing

# Create virtual environment
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Step 2: Install Dependencies

```bash
# Install all packages
pip install -r requirements.txt

# Verify installation
python -c "import cv2, mediapipe, easyocr; print('✓ Dependencies OK')"
```

### Step 3: GPU Setup (Optional)

For NVIDIA GPU acceleration:

```bash
# Install CUDA-enabled PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Verify GPU
python -c "import torch; print(f'GPU Available: {torch.cuda.is_available()}')"
```

---

## Quick Start

### 1. Prepare Video

```bash
# Copy your video to raw_videos/
cp /path/to/game.mp4 raw_videos/
```

### 2. Extract Frames

```bash
# Extract frames (skip other steps)
python main.py raw_videos/game.mp4 --skip pose torso ocr reps
```

**Output**: 30-50 frames per second extracted to `extracted_frames/`

### 3. Test Pose Estimation

```bash
# Process first 100 frames for pose
python main.py raw_videos/game.mp4 --max-frames 100 --skip torso ocr reps
```

**Output**: JSON files with keypoints in `pose_outputs/`

### 4. Run Full Pipeline

```bash
# Process with all steps
python main.py raw_videos/game.mp4 --max-frames 500
```

**Output**: Frames, poses, torso crops, jerseys, and reps summary

---

## Scripts Overview

### `main.py` - Main Pipeline Orchestrator

**Purpose**: Execute full video processing pipeline with configurable steps

**Usage**:
```bash
# Basic
python main.py video.mp4

# With options
python main.py video.mp4 \
  --max-frames 1000 \
  --skip frames \
  --log-level DEBUG \
  --output /custom/output/dir

# Skip specific steps
python main.py game.mp4 --skip torso ocr
```

**Options**:
- `video`: Video file path (required)
- `--output`: Output directory (default: ./team-grade-processing)
- `--max-frames`: Maximum frames to process (default: all)
- `--skip`: Steps to skip (choices: frames, pose, torso, ocr, reps)
- `--log-level`: Logging level (DEBUG, INFO, WARNING, ERROR)

**Output**:
```
Processing Results:
  - Extracted frames: 3000
  - Pose frames: 3000
  - Torso crops: 2850
  - Jerseys detected: 2200
  - Reps extracted: 45
```

### `examples.py` - Runnable Examples

**Purpose**: Demonstrate individual module functionality

**Usage**:
```bash
# Run single example
python examples.py 1
python examples.py 2
python examples.py 3

# Available examples
python examples.py   # Lists all

# Run all examples
python examples.py 0
```

**Examples Available**:

1. **Single Frame Pose Estimation**
   - Loads one frame and estimates pose
   - Shows keypoint count and average confidence
   - `python examples.py 1`

2. **Batch Pose Estimation**
   - Process 10 frames in batch
   - Compares individual vs batch processing
   - `python examples.py 2`

3. **Torso Extraction**
   - Extract torso from frame
   - Save crop to disk
   - `python examples.py 3`

4. **Jersey OCR**
   - Read jersey number from crop
   - Show confidence and all detections
   - `python examples.py 4`

5. **Rep Extraction**
   - Group frames into reps
   - Show rep boundaries
   - `python examples.py 5`

6. **Full Pipeline**
   - Reference for main.py usage
   - `python examples.py 6`

---

## Usage Examples

### Example 1: Process Single Video

```bash
# Extract and process 300 frames
python main.py raw_videos/game_1.mp4 --max-frames 300

# Check logs
tail -f logs/pipeline.log
```

### Example 2: Just Extract Frames (No Processing)

```bash
# Fast frame extraction
python main.py raw_videos/game_1.mp4 --skip pose torso ocr reps
```

### Example 3: Pose Only (For Testing)

```bash
# Test pose estimation on 50 frames
python main.py raw_videos/game_1.mp4 --max-frames 50 --skip torso ocr reps
```

### Example 4: Batch Multiple Videos

```bash
# Create batch script
for video in raw_videos/*.mp4; do
  echo "Processing $video..."
  python main.py "$video" \
    --max-frames 500 \
    --log-level INFO
done
```

### Example 5: Use GPU for Speed

```python
# In Python code
from processing import PoseEstimator

# GPU version (30-50 FPS)
estimator = PoseEstimator("mediapipe", device="cuda")

# CPU version (2-5 FPS)
estimator = PoseEstimator("mediapipe", device="cpu")
```

### Example 6: Custom Output Directory

```bash
# Save to different location
python main.py game.mp4 --output /mnt/fast_drive/processing

# Structure created:
/mnt/fast_drive/processing/
  ├── extracted_frames/
  ├── pose_outputs/
  ├── torso_crops/
  └── logs/
```

---

## Module Reference

### PoseEstimator (`pose_estimation.py`)

**Initialize**:
```python
from processing import PoseEstimator

estimator = PoseEstimator(
    model_name="mediapipe",  # or "yolo", "openpose"
    device="cpu"              # or "cuda", "mps"
)
```

**Single Frame**:
```python
import cv2
frame = cv2.imread("frame.jpg")
result = estimator.estimate_pose(frame)
# Returns:
# {
#   "keypoints": {33 keypoints with x,y,z,confidence},
#   "confidence": 0.95,
#   "timestamp": 0.033
# }
```

**Batch Processing**:
```python
results = estimator.batch_estimate([frame1, frame2, ...])
for i, result in enumerate(results):
    print(f"Frame {i}: {len(result['keypoints'])} keypoints")
```

**Keypoints** (MediaPipe):
- 33 total keypoints
- Body: 13 points (shoulders, elbows, wrists, hips, knees, ankles)
- Face: 10 points (eyes, ears, nose, mouth)
- Hands: 10 points per hand (palmweld, fingers)

**Cleanup**:
```python
estimator.close()
```

### TorsoCropper (`torso_cropper.py`)

**Initialize**:
```python
from processing import TorsoCropper

cropper = TorsoCropper()
```

**Get Bounding Box**:
```python
bbox = cropper.get_torso_box(
    keypoints=pose_result["keypoints"],
    frame_shape=(h, w)
)
# Returns: (x1, y1, x2, y2) or None
```

**Crop Torso**:
```python
crop = cropper.crop_torso(frame, pose_result["keypoints"])
if crop is not None:
    print(f"Crop size: {crop.shape}")
```

**Save Crop**:
```python
saved = cropper.save_crop(
    crop,
    output_dir=dirs["torso_crops"],
    frame_id=0,
    player_id="player_1"  # Optional
)
```

**Visualize**:
```python
annotated = cropper.visualize_torso_box(
    frame,
    pose_result["keypoints"]
)
cv2.imshow("Torso Box", annotated)
```

### JerseyOCR (`jersey_ocr.py`)

**Initialize**:
```python
from processing import JerseyOCR

# EasyOCR (fast, good accuracy)
ocr = JerseyOCR("easyocr", language="en")

# PaddleOCR (accurate, slower)
ocr = JerseyOCR("paddleocr", language="en")
```

**Read Jersey**:
```python
crop = cv2.imread("torso_crop.jpg")
jersey, confidence = ocr.read_jersey_number(
    crop,
    confidence_threshold=0.3
)
print(f"Jersey: {jersey}, Confidence: {confidence:.2%}")
```

**Debug All Detections**:
```python
all_text = ocr.read_all_text(crop)
for text_item in all_text:
    print(f"- '{text_item}'")
```

**Preprocess**:
```python
# Improve image quality before OCR
preprocessed = ocr.preprocess_jersey_crop(crop)
jersey, conf = ocr.read_jersey_number(preprocessed)
```

**Cleanup**:
```python
ocr.close()
```

### RepExtractor (`rep_extraction.py`)

**Create Frame Objects**:
```python
from processing import Frame

frame = Frame(
    frame_id=0,
    timestamp=0.033,          # Seconds
    track_id=1,               # From tracking
    jersey_number="42",
    jersey_confidence=0.95,
    position="QB",
    keypoints=pose["keypoints"]
)
```

**Extract Reps**:
```python
from processing import RepExtractor

extractor = RepExtractor(
    motion_threshold=0.05,     # Motion sensitivity
    min_rep_duration=5,        # Minimum frames
    max_gap=10                 # Frame gap to bridge
)

frames = [frame1, frame2, ...]  # List of Frame objects
reps = extractor.extract_reps(frames)
```

**Reps Structure**:
```python
for rep in reps:
    print(f"Rep {rep.rep_id}:")
    print(f"  Frames: {rep.start_frame}-{rep.end_frame}")
    print(f"  Duration: {rep.duration_frames}")
    print(f"  Player: {rep.jersey_number}")
    print(f"  Keypoints: {len(rep.keypoint_sequence)}")
```

**Map Track to Player**:
```python
track_map = extractor.map_track_id_to_player(reps)
# Returns: {track_id: player_id, ...}
```

### Utility Functions (`utils.py`)

**Setup Logging**:
```python
from processing import setup_logging

setup_logging(
    log_dir="logs",
    level="INFO"  # DEBUG, INFO, WARNING, ERROR
)
```

**Get Project Structure**:
```python
from processing import get_project_structure

dirs = get_project_structure()
# Returns:
# {
#   "root": Path(...),
#   "raw_videos": Path(...),
#   "extracted_frames": Path(...),
#   "pose_outputs": Path(...),
#   "torso_crops": Path(...),
#   "logs": Path(...)
# }
```

**Extract Frames**:
```python
from processing import extract_frames_from_video

frames = extract_frames_from_video(
    video_path="game.mp4",
    output_dir="extracted_frames",
    stride=1,           # Every frame
    max_frames=None,    # All frames
    format="jpg"
)
print(f"Extracted {len(frames)} frames")
```

**JSON Utilities**:
```python
from processing import load_json, save_json

# Load
data = load_json("pose_outputs/poses.json")

# Save
save_json(data, "output.json")
```

**Keypoint Processing**:
```python
from processing import (
    normalize_keypoints,
    denormalize_keypoints,
    filter_keypoints_by_confidence
)

# Normalize to 0-1 range
normalized = normalize_keypoints(keypoints, (h, w))

# Filter by confidence
filtered = filter_keypoints_by_confidence(
    keypoints,
    threshold=0.5
)
```

---

## Configuration

Edit `config.yaml` to customize behavior:

```yaml
pose_estimation:
  model: "mediapipe"        # mediapipe, yolo, openpose
  device: "cpu"             # cpu, cuda, mps
  mediapipe:
    model_complexity: 1     # 0=lite, 1=full, 2=heavy
    confidence_threshold: 0.5

torso_cropping:
  keypoint_confidence: 0.3  # Minimum confidence
  min_width: 50             # Minimum crop width
  min_height: 80            # Minimum crop height

jersey_ocr:
  engine: "easyocr"         # easyocr, paddleocr
  min_confidence: 0.3

rep_extraction:
  motion_threshold: 0.05
  min_rep_duration: 5
  max_gap: 10

processing:
  max_frames: -1            # -1 = unlimited
  save_poses: true
  save_crops: true
```

---

## Troubleshooting

### Issue: ModuleNotFoundError

**Problem**: `ModuleNotFoundError: No module named 'mediapipe'`

**Solution**:
```bash
# Reinstall all dependencies
pip install --upgrade -r requirements.txt

# Or install individually
pip install mediapipe opencv-python easyocr
```

### Issue: CUDA Out of Memory

**Problem**: CUDA out of memory error

**Solutions**:
```bash
# Use CPU instead
python main.py video.mp4 --device cpu

# Process fewer frames
python main.py video.mp4 --max-frames 300

# Reduce batch size in config.yaml
processing:
  pose_batch_size: 1
```

### Issue: No Keypoints Detected

**Problem**: Poses not detected or very low confidence

**Causes**:
- Video quality too low
- Frame resolution < 640×480
- Players too small in frame
- Poor lighting

**Solutions**:
- Increase confidence threshold in config
- Use higher resolution video
- Ensure clear visibility of players

### Issue: Poor Jersey Detection

**Problem**: Jersey numbers not detected accurately

**Solutions**:
```python
# Try different OCR engine
ocr = JerseyOCR("paddleocr")

# Preprocess image
crop = ocr.preprocess_jersey_crop(crop)
jersey, conf = ocr.read_jersey_number(crop)

# Adjust confidence threshold
jersey, conf = ocr.read_jersey_number(crop, confidence_threshold=0.5)
```

### Issue: Slow Processing

**Problem**: Processing is very slow

**Solutions**:
1. **Enable GPU**: Set `device: "cuda"` in config
2. **Skip frames**: Use `--skip frames` option
3. **Reduce max_frames**: `--max-frames 500`
4. **Use MediaPipe**: Faster than YOLO
5. **Batch processing**: Process multiple frames at once

---

## Performance

### Benchmarks (1080p video)

| Configuration | FPS | Memory | Quality |
|---------------|-----|--------|---------|
| MediaPipe + CPU | 2-5 | 2-4 GB | Good |
| MediaPipe + GPU | 30-50 | 4-6 GB | Good |
| YOLO + GPU | 15-25 | 6-8 GB | Better |

### Optimization Tips

1. **GPU First**: Enables real-time processing
2. **Frame Stride**: Use `stride=2` to process every 2nd frame
3. **Batch Size**: Larger batches = better GPU utilization
4. **Model Complexity**: Lower complexity = faster, less memory

### Memory Usage

```
GPU Memory (MediaPipe):
- Model: 500 MB
- Single frame: 100-200 MB
- Batch (16 frames): 1-2 GB

CPU Memory:
- Model: 200 MB
- Single frame: 50-100 MB
- Batch (4 frames): 300-500 MB
```

---

## Next Steps

1. ✅ Install dependencies
2. ✅ Place video in `raw_videos/`
3. ✅ Run pipeline: `python main.py raw_videos/game.mp4`
4. ✅ Check outputs in respective directories
5. ✅ Review logs: `logs/pipeline.log`
6. ✅ Send results to backend API (optional)

## Support

- **Logs**: Check `logs/pipeline.log` for detailed output
- **Examples**: Run `python examples.py [1-6]`
- **Configuration**: Edit `config.yaml`
- **Debugging**: Use `--log-level DEBUG`

---

**Happy processing! 🏈**
