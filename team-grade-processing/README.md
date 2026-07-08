# TEAM-GRADE LOCAL PROCESSING PIPELINE

Production-ready local processing pipeline for TEAM-GRADE video analysis. Handles YouTube → frames → pose → torso crops → OCR → rep extraction.

## Directory Structure

```
team-grade-processing/
│
├── raw_videos/              # Input video files
├── extracted_frames/        # Extracted frames from videos
├── pose_outputs/           # Pose estimation JSON outputs
├── torso_crops/            # Cropped torso regions for OCR
├── logs/                   # Processing logs
│
└── processing/             # Python processing modules
    ├── pose_estimation.py  # Pose model inference
    ├── torso_cropper.py    # Torso crop extraction
    ├── jersey_ocr.py       # Jersey number detection
    ├── rep_extraction.py   # Rep segmentation & tracking
    └── utils.py            # Common utilities
```

## Requirements

```bash
pip install opencv-python numpy mediapipe easyocr ultralytics
```

## Module Overview

### `pose_estimation.py`
- **PoseEstimator**: Load and run pose models (MediaPipe, YOLO)
- Supports both image and batch processing
- Outputs normalized keypoints in standard format

### `torso_cropper.py`
- **TorsoCropper**: Extract torso regions from frames
- Production functions: `get_torso_box()`, `crop_torso()`, `save_crop()`
- Handles edge cases and validates keypoint confidence

### `jersey_ocr.py`
- **JerseyOCR**: Read jersey numbers from torso crops
- Multi-backend support (EasyOCR, PaddleOCR)
- Includes preprocessing and filtering utilities

### `rep_extraction.py`
- **RepExtractor**: Group frames into discrete reps (plays)
- Motion-based segmentation with trajectory analysis
- Track ID → Player ID mapping via jersey number
- **Frame & Rep** dataclasses for structured data

### `utils.py`
- Path management (project structure helper)
- Frame loading (single and batch)
- JSON serialization for poses and metadata
- Keypoint normalization utilities
- Logging setup

## Quick Start

```python
from processing.utils import setup_logging, get_project_structure, extract_frames_from_video
from processing.pose_estimation import PoseEstimator
from processing.torso_cropper import TorsoCropper
from processing.jersey_ocr import JerseyOCR
from processing.rep_extraction import RepExtractor, Frame

# Setup
setup_logging(get_project_structure()["logs"])
dirs = get_project_structure()

# 1. Extract frames from video
video_path = dirs["raw_videos"] / "game.mp4"
frames = extract_frames_from_video(video_path, dirs["extracted_frames"])

# 2. Estimate poses
pose_estimator = PoseEstimator("mediapipe")
poses = pose_estimator.batch_estimate([cv2.imread(str(f)) for f in frames])

# 3. Crop torsos
cropper = TorsoCropper()
for pose, frame_id in zip(poses, range(len(frames))):
    crop = cropper.crop_torso(cv2.imread(str(frames[frame_id])), pose["keypoints"])
    if crop is not None:
        cropper.save_crop(crop, dirs["torso_crops"], frame_id)

# 4. Read jerseys
ocr = JerseyOCR("easyocr")
for crop_path in dirs["torso_crops"].glob("*.jpg"):
    crop = cv2.imread(str(crop_path))
    jersey, conf = ocr.read_jersey_number(crop)
    print(f"{crop_path.name}: {jersey} ({conf:.3f})")

# 5. Extract reps
extractor = RepExtractor()
frame_objs = [Frame(...) for ...]  # Construct Frame objects
reps = extractor.extract_reps(frame_objs)
track_map = extractor.map_track_id_to_player(reps)
```

## Key Design Decisions

1. **Modular**: Each module is independent; can use individual functions
2. **Production-ready**: Full error handling, logging, validation
3. **No UI**: CLI/script-based processing only
4. **No Backend Modifications**: Standalone pipeline compatible with TEAM-GRADE API
5. **Structured Data**: Use dataclasses (Frame, Rep) for type safety

## Configuration

Adjust parameters in extractors:

```python
# Rep extraction
extractor = RepExtractor(
    max_static_frames=30,      # Frames without motion before rep ends
    max_gap_frames=10,         # Frames to bridge trajectory gaps
    min_rep_duration=15,       # Minimum rep length
    max_rep_duration=300       # Maximum rep length
)

# Torso cropping
cropper = TorsoCropper(
    torso_height_ratio=0.6,    # Height fraction of bbox
    torso_width_ratio=0.5      # Width fraction of bbox
)

# Jersey OCR
ocr = JerseyOCR(
    ocr_engine="easyocr",      # or "paddle", "tesseract"
    language="en"
)
```

## Output Format

### Pose JSON
```json
{
  "timestamp": "2026-03-11T20:30:00Z",
  "data": {
    "keypoints": {
      "nose": {"x": 0.5, "y": 0.4, "z": 0.0, "confidence": 0.95},
      "left_shoulder": {...}
    },
    "num_persons": 1
  }
}
```

### Rep Structure
```python
Rep(
    rep_id=0,
    start_frame=100,
    end_frame=150,
    duration_frames=50,
    player_id="23",
    track_id=1,
    jersey_number="23",
    position="RB",
    keypoint_sequence=[...],
    metadata={...}
)
```

## Performance Tips

1. **GPU**: Use GPU-enabled models (CUDA) for 5-10x speedup
2. **Batching**: Process frames in batches instead of one-by-one
3. **Stride**: Use frame stride (every 2nd/3rd frame) for faster processing
4. **Caching**: Save pose JSONs to disk to avoid re-computation

## Troubleshooting

- **MediaPipe not installed**: `pip install mediapipe`
- **EasyOCR slow on first run**: Initial model download takes time
- **GPU out of memory**: Reduce batch size or use CPU
- **Low jersey detection**: Use `preprocess_jersey_crop()` before OCR

## Testing

### Quick Test Summary
- ✅ **69/69 core tests passing** (100% of non-legacy tests)
- ✅ **Endpoint tests:** API routing, HTTP methods, validation
- ✅ **Exception tests:** Error hierarchy, retry logic
- ✅ **Validator tests:** URL parsing, video ID validation

### Run Tests
```bash
# Run all passing tests (recommended)
pytest tests/test_endpoints_simplified.py tests/test_exceptions.py tests/test_validators.py -v

# Run specific test category
pytest tests/test_validators.py -v              # URL & ID validation
pytest tests/test_exceptions.py -v              # Exception handling
pytest tests/test_endpoints_simplified.py -v    # API endpoints

# Run with coverage report
pytest tests/test_endpoints_simplified.py tests/test_exceptions.py tests/test_validators.py --cov=api --cov=ingest --cov-report=term-missing

# Run all tests (includes legacy tests with known issues)
pytest tests/ -v
```

### Test Details
See [tests/README_TEST_STATUS.md](tests/README_TEST_STATUS.md) for:
- Detailed test breakdown by category
- Explanation of legacy test failures
- Architecture notes on mocking
- Component coverage matrix

## Production Checklist

- [ ] Install all dependencies
- [ ] Test on sample video
- [ ] Verify GPU usage (if available)
- [ ] Configure logging level
- [ ] Set output directories
- [ ] Validate pose format against backend
- [ ] Test jersey OCR accuracy
- [ ] Validate rep extraction parameters
- [ ] Run test suite: `pytest tests/test_endpoints_simplified.py tests/test_exceptions.py tests/test_validators.py`
- [ ] Verify API routes with simplified endpoint tests
