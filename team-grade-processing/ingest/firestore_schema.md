# Firestore Schema for TEAM-GRADE Ingestion Pipeline

This document defines the complete Firestore data model for the TEAM-GRADE video processing pipeline.

## Collections Overview

### 1. `videos` Collection

Root collection for video metadata and processing state.

**Document ID:** `{video_id}` (from YouTube)

```yaml
videos/{video_id}:
  # Core metadata
  title: string                    # Video title
  youtube_url: string              # Original YouTube URL
  channel_id: string               # YouTube channel ID
  upload_date: timestamp           # When uploaded to YouTube
  duration_seconds: number         # Video duration
  
  # Processing status
  status: enum[pending|processing|completed|failed]     # Overall pipeline status
  created_at: timestamp            # When ingestion started
  completed_at: timestamp?         # When pipeline finished
  
  # Stage-specific status
  stages: map<string, object>      # Status for each stage
    metadata:
      status: enum[pending|completed|failed|skipped]
      attempt: number
      error?: string
      completed_at?: timestamp
    download:
      status: enum[pending|completed|failed|skipped]
      path?: string                # Local file path
      file_size_bytes?: number
      attempt: number
      error?: string
      completed_at?: timestamp
    frame_extraction:
      status: enum[pending|completed|failed|skipped]
      total_frames: number?
      fps: number?
      attempt: number
      error?: string
      completed_at?: timestamp
    pose:
      status: enum[pending|completed|failed|skipped]
      frames_processed: number?
      high_confidence_frames: number?
      attempt: number
      error?: string
      completed_at?: timestamp
    torso_crop:
      status: enum[pending|completed|failed|skipped]
      crops_created: number?
      attempt: number
      error?: string
      completed_at?: timestamp
    jersey_ocr:
      status: enum[pending|completed|failed|skipped]
      jersey_number?: string
      confidence?: number[0.0-1.0]
      source_frame_id?: number
      attempt: number
      error?: string
      completed_at?: timestamp
    rep_extraction:
      status: enum[pending|completed|failed|skipped]
      total_reps: number?
      attempt: number
      error?: string
      completed_at?: timestamp
    biomechanics:
      status: enum[pending|completed|failed|skipped]
      traits_analyzed: number?
      attempt: number
      error?: string
      completed_at?: timestamp
  
  # Extracted data (populated as stages complete)
  jersey_number?: string           # Jersey number from OCR
  download_path?: string           # Local path to video file
  frame_count?: number             # Total frames extracted
  
  # Analysis results (populated by biomechanics stage)
  analysis?: object
    per_rep: map<string, object>   # Results keyed by rep_id
    summary: object                # Aggregated stats across all reps
```

---

### 2. `videos/{video_id}/frames` Subcollection

Frame-level metadata extracted from video.

**Document ID:** `{zero_padded_frame_number}` (e.g., "000001", "000042")

```yaml
videos/{video_id}/frames/{frame_id}:
  frame_index: number              # 0-based frame index
  timestamp_seconds: number        # Approx time in video
  path: string                     # Relative path to extracted JPEG
  width: number                    # Frame width (pixels)
  height: number                   # Frame height (pixels)
```

---

### 3. `videos/{video_id}/pose` Subcollection

Raw pose keypoints from BlazePose (33 points per frame).

**Document ID:** `{zero_padded_frame_number}` (matches frame collection)

```yaml
videos/{video_id}/pose/{frame_id}:
  frame_index: number
  model: string                    # "BlazePose" | "YOLO" | "OpenPose"
  keypoints: array[object]         # 33-point MediaPipe BlazePose
    - index: number                # 0-32 (MediaPipe keypoint index)
      name: string                 # "nose", "left_shoulder", etc.
      x: number[0.0-1.0]           # Normalized X coordinate
      y: number[0.0-1.0]           # Normalized Y coordinate
      z: number                    # Depth (normalized)
      confidence: number[0.0-1.0]  # Detection confidence
  confidence: number[0.0-1.0]      # Overall pose confidence
  processing_time_ms: number
```

**Keypoint Index Reference (MediaPipe BlazePose):**
- 0: nose
- 1: left_eye_inner, 2: left_eye, 3: left_eye_outer
- 4: right_eye_inner, 5: right_eye, 6: right_eye_outer
- 7: left_ear, 8: right_ear
- 9: mouth_left, 10: mouth_right
- 11: left_shoulder, 12: right_shoulder
- 13: left_elbow, 14: right_elbow
- 15: left_wrist, 16: right_wrist
- 17: left_pinky, 18: right_pinky
- 19: left_index, 20: right_index
- 21: left_thumb, 22: right_thumb
- 23: left_hip, 24: right_hip
- 25: left_knee, 26: right_knee
- 27: left_ankle, 28: right_ankle
- 29: left_heel, 30: right_heel
- 31: left_foot_index, 32: right_foot_index

---

### 4. `videos/{video_id}/torso` Subcollection

Torso crops for jersey OCR (only created for high-confidence pose frames).

**Document ID:** `{zero_padded_frame_number}`

```yaml
videos/{video_id}/torso/{frame_id}:
  frame_index: number
  crop_path: string                # Relative path to torso crop JPEG
  pose_confidence: number[0.0-1.0] # Confidence from pose stage
  bbox: object
    x_min: number                  # Bounding box top-left X
    y_min: number                  # Bounding box top-left Y
    width: number                  # Box width in pixels
    height: number                 # Box height in pixels
  processing_time_ms: number
```

---

### 5. `videos/{video_id}/reps` Subcollection

Discrete play reps (snap → whistle) extracted from pose trajectory.

**Document ID:** `{zero_padded_rep_id}` (e.g., "00001", "00042")

```yaml
videos/{video_id}/reps/{rep_id}:
  rep_index: number                # 0-based rep index
  start_frame: number              # First frame of rep
  end_frame: number                # Last frame of rep
  duration_frames: number          # end_frame - start_frame + 1
  duration_seconds: number         # approx duration in seconds
  
  # Metadata (optional)
  play_type?: string               # e.g., "run", "pass", "blocking"
  
  # Motion metrics (calculated during extraction)
  avg_velocity?: number
  max_velocity?: number
  movement_distance?: number
  
  # Biomechanics results (populated by analysis stage)
  analysis?: object
    position: string               # Position code (QB, OL, RB, etc.)
    traits: map<string, object>    # {trait_name: {score, details}}
    metrics: map<string, number>   # {metric_name: value}
```

---

### 6. `ingestion_queue` Collection

Queue for managing multi-stage ingestion jobs.

**Document ID:** Auto-generated

```yaml
ingestion_queue/{doc_id}:
  video_id: string                 # Reference to video being processed
  stage: string                    # Current stage name
  status: enum[pending|processing|completed|failed]
  priority: number[1-10]           # 1=highest, 10=lowest
  attempt: number                  # Retry count
  
  enqueued_at: timestamp
  started_at?: timestamp
  completed_at?: timestamp
  
  error?: string                   # Error message if failed
  payload?: object                 # Stage-specific data (e.g., fps for frame_extraction)
```

---

## Stage Flow and Data Dependencies

```
metadata
   ↓ (creates video doc, updates status)
download
   ↓ (stores video_path, file_size)
frame_extraction
   ↓ (creates /frames subcollection)
pose
   ↓ (creates /pose subcollection)
torso_crop
   ↓ (creates /torso subcollection, only for high-confidence frames)
jersey_ocr
   ↓ (stores jersey_number in video doc)
rep_extraction
   ↓ (creates /reps subcollection)
biomechanics
   ↓ (populates analysis in video doc and per-rep data)
complete
   ↓ (marks video.status = "completed", cleanup queue)
```

---

## Efficiency Notes

1. **Batch Writes:** Pose data is written in batches (e.g., 10-20 frames per batch write) to avoid transaction overhead.

2. **Lazy Subcollection Creation:** Subcollections are only created when stages complete successfully. If a stage is skipped, its subcollection is not created.

3. **High-Confidence Filtering:** Torso crops are only created for frames with pose confidence > 0.7 (configurable).

4. **No Full-Video In-Memory:** Frame paths are stored; frames are not cached in Firestore.

---

## Firestore Indexes (Recommended)

For efficient queries:

```yaml
# Query: Get videos by status
Index on: videos.status (Ascending)

# Query: Get videos by created_at (for time-based reports)
Index on: videos.created_at (Descending)

# Query: Get videos by status and created_at
Composite Index on: videos.status (Ascending), videos.created_at (Descending)

# Query: Get next queue item by priority
Index on: ingestion_queue.priority (Ascending), ingestion_queue.enqueued_at (Ascending)
```

---

## Example Document Instances

### Example Video Document (In Progress)

```json
{
  "title": "Texas A&M Offensive Line Drills - Week 1",
  "youtube_url": "https://www.youtube.com/watch?v=UJqTHZg99Jo",
  "channel_id": "UCxyz123",
  "upload_date": "2025-03-15T14:30:00Z",
  "duration_seconds": 1842,
  "status": "processing",
  "created_at": "2025-03-18T10:00:00Z",
  "stages": {
    "metadata": {"status": "completed", "completed_at": "2025-03-18T10:00:15Z"},
    "download": {"status": "completed", "path": "videos/UJqTHZg99Jo.mp4", "file_size_bytes": 125000000, "completed_at": "2025-03-18T10:05:00Z"},
    "frame_extraction": {"status": "completed", "total_frames": 450, "fps": 15, "completed_at": "2025-03-18T10:15:00Z"},
    "pose": {"status": "completed", "frames_processed": 450, "high_confidence_frames": 420, "completed_at": "2025-03-18T11:00:00Z"},
    "torso_crop": {"status": "completed", "crops_created": 420, "completed_at": "2025-03-18T11:10:00Z"},
    "jersey_ocr": {"status": "completed", "jersey_number": "73", "confidence": 0.94, "source_frame_id": 42, "completed_at": "2025-03-18T11:12:00Z"},
    "rep_extraction": {"status": "completed", "total_reps": 8, "completed_at": "2025-03-18T11:15:00Z"},
    "biomechanics": {"status": "in-progress", "traits_analyzed": 4}
  },
  "jersey_number": "73",
  "download_path": "videos/UJqTHZg99Jo.mp4",
  "frame_count": 450
}
```

### Example Pose Document

```json
{
  "frame_index": 42,
  "model": "BlazePose",
  "keypoints": [
    {"index": 0, "name": "nose", "x": 0.52, "y": 0.35, "z": 0.0, "confidence": 0.98},
    {"index": 11, "name": "left_shoulder", "x": 0.45, "y": 0.50, "z": -0.05, "confidence": 0.97},
    {"index": 12, "name": "right_shoulder", "x": 0.60, "y": 0.50, "z": -0.04, "confidence": 0.96}
  ],
  "confidence": 0.94,
  "processing_time_ms": 45
}
```

### Example Rep Document

```json
{
  "rep_index": 3,
  "start_frame": 120,
  "end_frame": 180,
  "duration_frames": 61,
  "duration_seconds": 4.07,
  "play_type": "run",
  "avg_velocity": 2.3,
  "max_velocity": 4.1,
  "movement_distance": 15.5,
  "analysis": {
    "position": "OL",
    "traits": {
      "hip_extension": {"score": 8.2, "details": "Good hip drive"},
      "knee_bend": {"score": 7.9, "details": "Adequate depth"}
    },
    "metrics": {
      "hip_angle_avg": 95.3,
      "knee_angle_avg": 78.1
    }
  }
}
```

---

## Implementation Notes

1. All timestamps are in UTC (Firestore Timestamp type or ISO 8601 string).
2. All numeric values for normalized coordinates are in range [0.0, 1.0].
3. All attempt counts start at 0 and increment on each retry.
4. Stage transitions are initiated by the previous stage enqueueing the next.
5. If a stage is skipped due to error, the skip reason is logged and subsequent stages are only run if they don't depend on the skipped stage's output.
