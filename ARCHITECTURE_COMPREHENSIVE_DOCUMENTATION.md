# TEAM-GRADE Architecture & Security Documentation

**Last Updated:** March 20, 2026  
**Version:** 2.0 (Post-Security-Audit)  
**Status:** ✅ PRODUCTION READY

---

## Table of Contents

1. [Executive Overview](#executive-overview)
2. [System Architecture](#system-architecture)
3. [Security Architecture](#security-architecture)
4. [Layer-by-Layer Design](#layer-by-layer-design)
5. [Data Flow](#data-flow)
6. [Module Organization](#module-organization)
7. [Key Components](#key-components)
8. [API Reference](#api-reference)
9. [Maintenance & Operations](#maintenance--operations)
10. [Troubleshooting Guide](#troubleshooting-guide)

---

## Executive Overview

### What is TEAM-GRADE?

TEAM-GRADE is a **9-stage video processing pipeline** that transforms raw YouTube football videos into biomechanics analysis data using AI/ML:

```
YouTube Video → Download → Extract Frames → Pose Estimation → Torso Crop 
  → Jersey OCR → Rep Extraction → Biomechanics Analysis → Complete
```

### Key Characteristics

- **Queue-Driven:** Firestore-based work queue, retry logic with exponential backoff
- **Multi-Stage:** 9 sequential processing stages, each implemented as independent module
- **Cloud-Native:** Google Cloud Firestore for data, supports cloud deployment
- **Asynchronous:** Worker processes video batches continuously
- **REST API:** FastAPI with 7 endpoints for ingestion and analysis
- **Web UI:** Single-page upload interface for video submission

### Highlights

| Aspect | Details |
|--------|---------|
| **Core Language** | Python 3.11+ |
| **Primary Data Store** | Google Cloud Firestore |
| **API Framework** | FastAPI |
| **Processing Library** | MediaPipe, OpenCV, custom ML models |
| **Video Format** | MP4 (YouTube downloads) |
| **Frame Rate** | 15 FPS (configurable) |
| **Output** | JSON/Firestore documents with pose, reps, traits |

---

## System Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                      WEB UI (localhost:8080)                     │
│                           upload.html                           │
└────────────────┬────────────────────────────────────────────────┘
                 │ HTTP POST (video_url, jersey, position)
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│              FastAPI Server (localhost:8000)                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  API Routes (7 endpoints)                                │  │
│  │  ├─ POST /api/ingest (queue video)                      │  │
│  │  ├─ GET /api/ingest/{video_id}/status (check progress) │  │
│  │  ├─ GET /api/ingest/{video_id}/info (video details)    │  │
│  │  ├─ GET /api/analysis/{video_id} (results)             │  │
│  │  ├─ GET /api/analysis/{video_id}/reps (reps list)      │  │
│  │  ├─ GET /api/analysis/{video_id}/summary (agg stats)   │  │
│  │  └─ GET /api/analysis/{video_id}/metrics (detailed)    │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Core Components                                         │  │
│  │  ├─ Firestore Client (DB connection)                   │  │
│  │  ├─ Queue Manager (ingestion_queue ops)                │  │
│  │  └─ Configuration (centralized settings)               │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                 │ Reads/Writes
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│            Google Cloud Firestore (Remote)                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Collections:                                            │  │
│  │  ├─ videos/{video_id} (root document)                   │  │
│  │  │  ├─ frames/ (subcollection)                          │  │
│  │  │  ├─ pose/ (subcollection)                            │  │
│  │  │  ├─ reps/ (subcollection)                            │  │
│  │  │  ├─ torso_crops/ (subcollection)                     │  │
│  │  │  └─ analysis/ (subcollection)                        │  │
│  │  ├─ ingestion_queue/ (work queue)                       │  │
│  │  └─ tracking/ (progress and logs)                       │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                 │ Dequeues & Processes
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│         Pipeline Worker (separate process)                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Worker Loop (continuous)                               │  │
│  │  1. Poll ingestion_queue for jobs                        │  │
│  │  2. Dequeue video (video_id, stage_name)                │  │
│  │  3. Dispatch to appropriate stage handler                │  │
│  │  4. Execute stage (download, pose, etc.)               │  │
│  │  5. Write results to Firestore                          │  │
│  │  6. Enqueue next stage                                  │  │
│  │  7. Repeat from step 1                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  9 Stage Handlers (imported as module)                  │  │
│  │  1. metadata_stage.py (initialize)                      │  │
│  │  2. download_stage.py (YouTube DL)                      │  │
│  │  3. frame_extraction_stage.py (FFmpeg)                  │  │
│  │  4. pose_stage.py (MediaPipe)                           │  │
│  │  5. torso_crop_stage.py (region crop)                   │  │
│  │  6. jersey_ocr_stage.py (jersey number detect)          │  │
│  │  7. rep_extraction_stage.py (play segmentation)         │  │
│  │  8. biomechanics_stage.py (trait scoring)               │  │
│  │  9. complete_stage.py (finalization)                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                 │ Reads/Analyzes
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│          Local Processing Resources                              │
│  ├─ raw_videos/ (downloaded MP4 files)                          │
│  ├─ Cache (pose models, MediaPipe)                              │
│  └─ Temp frames/ (extracted JPEG/PNG files)                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Security Architecture

### Threat Model

**TEAM-GRADE Security assumes 3 threat actors:**

1. **Untrusted User Input**
   - Malicious video_ids
   - Path traversal attempts
   - Injection attacks via API

2. **Log Injection**
   - Newline characters in logs
   - Control character injection
   - Private data leakage

3. **Resource Exhaustion**
   - Billion-frame videos
   - Impossible frame rates
   - Out-of-bounds parameters

### Security Defense Layers

```
Layer 1: Input Validation (Firewall)
  ├─ Validate video_id format (alphanumeric, max 100 chars)
  ├─ Validate numeric parameters (fps, batch size, thresholds)
  ├─ Validate path parameters (relative, no escape sequences)
  └─ Validate enum values (stage names, formats)
  
Layer 2: Data Sanitization (Scrubbing)
  ├─ Sanitize video_id before any use
  ├─ Remove control characters from logs
  ├─ Encode paths safely
  └─ Whitelist executable paths
  
Layer 3: Configuration Centralization (Single Source of Truth)
  ├─ COLLECTION_* constants (no hardcoded strings)
  ├─ RANGE_* constants (numeric bounds)
  ├─ PATH_* helpers (path generation)
  └─ MODEL_* settings (ML model selection)
  
Layer 4: Graceful Degradation (Resilience)
  ├─ Try/except on non-critical operations
  ├─ Retry logic with exponential backoff
  ├─ Clear error messages without sensitive data
  └─ Fallback modes when resources unavailable
  
Layer 5: Defensive Programming (Paranoia)
  ├─ Early validation of all inputs
  ├─ Assertions on internal assumptions
  ├─ Sanitized logging everywhere
  └─ Type hints for clarity
```

### Security Patterns (38 Applied)

**Cross-File Pattern 1: Early Validation**
```python
safe_video_id = settings.sanitize_id(video_id)
if not safe_video_id:
    return {"error": "Invalid video_id"}, 400
```

**Cross-File Pattern 2: Collection Constants**
```python
db.collection(settings.COLLECTION_VIDEOS).document(safe_video_id)
```

**Cross-File Pattern 3: Secure Logging**
```python
logger.info(f"[STAGE] Processing video: {safe_video_id}")
```

**Cross-File Pattern 4: Path Validation**
```python
if path.is_absolute():
    raise ValueError("Path must be relative")
```

**Cross-File Pattern 5: Command Execution Allowlist**
```python
if ffmpeg_path not in allowed_paths:
    raise ValueError("Custom path not allowed")
```

---

## Layer-by-Layer Design

### Layer 1: Configuration (Horizontal - All Layers Depend)

**File:** `config/settings.py`  
**Responsibility:** Centralized configuration, validation functions, path helpers

**Key Functions:**
```python
sanitize_id(video_id)               # Video ID validation
validate_fps(fps)                    # Frame rate bounds check
validate_batch_size(size)           # Batch size range check
validate_confidence(threshold)      # Confidence threshold bounds
get_video_path(video_id)            # Get video file path
get_frames_dir(video_id)            # Get frames directory
# + 50+ configuration constants
```

**Constants Defined:**
- COLLECTION_VIDEOS, COLLECTION_FRAMES, COLLECTION_POSE, etc.
- FRAME_EXTRACTION_FPS = 15.0
- POSE_CONFIDENCE_THRESHOLD = 0.7
- OCR_STOP_ON_CONFIDENCE = 0.8
- EXTRACTION_TIMEOUT = 3600

---

### Layer 2: Data Access (Vertical - Firestore Operations)

**File:** `ingest/utils/firestore_utils.py`  
**Responsibility:** All Firestore read/write operations, batch operations

**Key Functions:**
```python
write_frames_batch(db, video_id, frames)           # Batch write frames
write_pose_batch(db, video_id, pose_results)       # Batch write pose data
write_torso_crops_batch(db, video_id, crops)       # Batch write crops
write_reps_batch(db, video_id, reps)               # Batch write reps
write_analysis_results(db, video_id, analysis)     # Write analysis
update_rep_analysis(db, video_id, rep_id, traits)  # Update rep traits
```

**Security Pattern:** All functions sanitize video_id, use COLLECTION_* constants

---

### Layer 3: Orchestration (Horizontal - Workflow Management)

**Files:** 
- `ingest/ingest_pipeline_worker.py` (main worker loop)
- `ingest/queue_manager.py` (queue operations)

**Responsibility:** Coordinate stages, manage queue, handle retries

**Worker Loop:**
```python
while True:
    queue_item = dequeue_job()  # Gets {video_id, stage_name, payload}
    validate(queue_item)  # Validate fields
    handler = get_handler(stage_name)
    success, error = handler(firestore_client, video_id, queue_manager, payload)
    if success:
        enqueue_next_stage(video_id, next_stage)
    elif should_retry(attempt):
        enqueue_retry(video_id, stage_name, attempt+1)
    # Sleep and poll again
```

---

### Layer 4: Processing Stages (Vertical - Video Pipeline)

**Files:** `ingest/stages/*.py` (9 files)

**Stage Sequence:**
```
1. metadata_stage.py       → Initialize Firestore document, create stages tracking
2. download_stage.py       → YouTube video download, save to raw_videos/
3. frame_extraction_stage  → FFmpeg frame extraction at 15 FPS
4. pose_stage.py           → MediaPipe BlazePose keypoint detection
5. torso_crop_stage.py     → Region cropping from high-confidence frames
6. jersey_ocr_stage.py     → Jersey number detection
7. rep_extraction_stage.py → Play/rep segmentation from pose trajectory
8. biomechanics_stage.py   → Trait scoring via trench_engine
9. complete_stage.py       → Pipeline finalization and queue cleanup
```

**Each Stage:**
- Takes (firestore_client, video_id, queue_manager, payload)
- Returns (success: bool, error_message: Optional[str])
- Reads from Firestore (previous stage results)
- Writes to Firestore (current stage results)
- Enqueues next stage on success

---

### Layer 5: Utility Modules (Horizontal - Cross-Cutting Concerns)

**Files:**
- `ingest/utils/logging_utils.py` → Structured logging
- `ingest/utils/ffmpeg_utils.py` → FFmpeg frame extraction wrapper
- `ingest/utils/firestore_utils.py` → Firestore operations (Layer 2)

**Key Classes:**
```python
StructuredLogger              # ASCII-safe, injection-proof logging
FFmpegFrameExtractor         # FFmpeg wrapper with path validation
```

---

### Layer 6: API Interface (Horizontal - Public Contract)

**Files:**
- `api/server.py` → Main FastAPI app, initialization
- `api/routes/ingest_routes.py` → 3 ingestion endpoints
- `api/routes/analysis_routes.py` → 4 analysis endpoints

**7 Total Endpoints:**

```
POST /api/ingest
  Input: {video_url, team_id, player_number, position}
  Output: {video_id, status: "queued"}
  Validation: URL format, integer fields

GET /api/ingest/{video_id}/status
  Output: {video_id, status, stages: {...}, progress: 45}
  Validation: video_id format

GET /api/ingest/{video_id}/info
  Output: {video_id, title, jersey, frame_count}
  Validation: video_id format

GET /api/analysis/{video_id}
  Output: {video_id, jersey, analysis: {...}}
  Validation: video_id format

GET /api/analysis/{video_id}/reps
  Output: [{rep_id, reps_per_set, start_frame, end_frame, ...}]
  Validation: video_id format

GET /api/analysis/{video_id}/summary
  Output: {total_reps, avg_depth, avg_speed, ...}
  Validation: video_id format

GET /api/analysis/{video_id}/metrics
  Output: {traits: {power: {...}, depth: {...}, ...}}
  Validation: video_id format
```

---

## Data Flow

### End-to-End User Journey

```
1. USER UPLOADS VIDEO
   ├─ Opens http://localhost:8080/upload.html
   ├─ Enters: YouTube URL, Jersey #, Position
   ├─ Clicks "Submit"
   └─ Browser sends HTTP POST to /api/ingest

2. API RECEIVES REQUEST
   ├─ Validates input (URL format, number ranges)
   ├─ Sanitizes video_id
   ├─ Creates Firestore video document
   ├─ Enqueues "metadata" stage
   └─ Returns {video_id, status: "queued"} to browser

3. BROWSER POLLS STATUS
   ├─ Every 2 seconds: GET /api/ingest/{video_id}/status
   ├─ Shows progress bar (0% → 100%)
   ├─ When status == "complete", redirects to analysis view
   └─ User clicks "View Results"

4. WORKER PROCESSES VIDEO (Backend - Asynchronous)
   Loop:
     a) Worker polls ingestion_queue (every 5 seconds)
     b) Dequeues next job: {video_id: "abc123", stage_name: "download"}
     c) Gets handler for stage
     d) Handler executes:
        - Read from Firestore (if needed)
        - Process (download, FFmpeg, ML, etc.)
        - Write results to Firestore
        - Enqueue next stage
     e) Sleep and repeat
   
   Stages Process Sequentially (per video):
     Stage 1 (metadata) → Firestore initialized
     Stage 2 (download) → Video saved to raw_videos/
     Stage 3 (frame_extraction) → 1000 frames extracted
     Stage 4 (pose) → Keypoints for each frame
     Stage 5 (torso_crop) → Cropped regions
     Stage 6 (jersey_ocr) → Jersey number detected
     Stage 7 (rep_extraction) → 20 reps identified
     Stage 8 (biomechanics) → Traits calculated
     Stage 9 (complete) → Pipeline finished

5. USER VIEWS RESULTS
   ├─ GET /api/analysis/{video_id}
   ├─ Returns: {jersey: "42", analysis: {...}}
   ├─ Uses /api/analysis/{video_id}/reps for rep details
   ├─ Uses /api/analysis/{video_id}/summary for aggregates
   └─ Displays: Jersey, Reps, Traits, Graphs

6. BACKGROUND: DATA PERSISTED IN FIRESTORE
   └─ videos/abc123/
      ├─ Basic document (status, jersey, player_number)
      ├─ frames/ subcollection (1000 documents)
      ├─ pose/ subcollection (30k keypoints)
      ├─ reps/ subcollection (20 reps + traits)
      ├─ torso_crops/ subcollection (crop metadata)
      └─ analysis/ subcollection (aggregated results)
```

---

## Module Organization

### Directory Structure

```
TEAM-GRADE/
├── config/
│   └── settings.py              # Centralized configuration (50+ parameters)
│
├── team-grade-processing/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── server.py            # FastAPI app, initialization
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── ingest_routes.py # 3 ingestion endpoints
│   │       └── analysis_routes.py # 4 analysis endpoints
│   │
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── firestore_client.py  # Firestore connection manager
│   │   ├── queue_manager.py     # Queue operations
│   │   ├── ingest_pipeline_worker.py # Main worker loop
│   │   ├── ingest_worker.py     # Legacy worker (backward compat)
│   │   ├── youtube_downloader.py # YouTube download logic
│   │   │
│   │   ├── utils/
│   │   │   ├── __init__.py
│   │   │   ├── logging_utils.py # Structured logging
│   │   │   ├── ffmpeg_utils.py # FFmpeg wrapper
│   │   │   └── firestore_utils.py # Firestore operations
│   │   │
│   │   └── stages/              # 9 stage handlers
│   │       ├── __init__.py      # STAGE_HANDLERS dict, dispatch
│   │       ├── metadata_stage.py
│   │       ├── download_stage.py
│   │       ├── frame_extraction_stage.py
│   │       ├── pose_stage.py
│   │       ├── torso_crop_stage.py
│   │       ├── jersey_ocr_stage.py
│   │       ├── rep_extraction_stage.py
│   │       ├── biomechanics_stage.py
│   │       └── complete_stage.py
│   │
│   ├── processing/              # Analysis modules (pre-existing)
│   │   ├── pose_estimation.py
│   │   ├── jersey_ocr.py
│   │   ├── rep_extraction.py
│   │   ├── torso_cropper.py
│   │   └── utils.py
│   │
│   ├── raw_videos/              # Downloaded MP4 files (local cache)
│   ├── logs/                    # Rotating logs
│   │   ├── ingest.log
│   │   └── pipeline_worker.log
│   └── examples.py              # Example usage
│
├── Analysis/
│   ├── trench_engine/           # Biomechanics analysis engine
│   │   ├── engine.py            # Main scoring logic
│   │   ├── scoring/trait_scorer.py
│   │   └── config/              # Position-specific configs
│   └── ...
│
├── credentials/
│   └── service-account.json     # Google Cloud credentials
│
└── Documentation
    ├── SECURITY_AUDIT_SUMMARY.md       # Phase 1: 38 fixes
    ├── OPTION_2_VERIFICATION_REPORT.md # Phase 1: Validation
    ├── OPTION_4_STAGE_AUDIT_REPORT.md  # Phase 2: Stage audit
    └── This file: ARCHITECTURE_DOCUMENTATION.md
```

---

## Key Components

### Component 1: Firestore Client

**File:** `ingest/firestore_client.py`  
**Responsibility:** Manage connection to Google Cloud Firestore

```python
class FirestoreClient:
    def __init__(self, project_id, json_path):
        # Authenticate with service account
        # Initialize Firestore client
    
    @property
    def db(self):
        # Return Firestore client
```

---

### Component 2: Queue Manager

**File:** `ingest/queue_manager.py`  
**Responsibility:** Manage ingestion_queue collection in Firestore

```python
class QueueManager:
    def enqueue_video(video_id, stage_name, attempt=1)
        # Add job to ingestion_queue
    
    def dequeue_job()
        # Get next job from queue
    
    def mark_stage_attempt(video_id, stage_name, success)
        # Update attempt counter
```

---

### Component 3: Pipeline Worker

**File:** `ingest/ingest_pipeline_worker.py`  
**Responsibility:** Main processing loop, stage dispatch

```python
class PipelineWorker:
    def run_worker(poll_interval=5.0)
        while True:
            queue_item = dequeue_job()
            validate(queue_item)
            handler = STAGE_HANDLERS[queue_item['stage_name']]
            success, error = handler(...)
            if success:
                enqueue_next_stage()
            sleep(poll_interval)
```

---

### Component 4: Stage Dispatcher

**File:** `ingest/stages/__init__.py`  
**Responsibility:** Map stage names to handler functions

```python
STAGE_SEQUENCE = [
    "metadata", "download", "frame_extraction", "pose",
    "torso_crop", "jersey_ocr", "rep_extraction",
    "biomechanics", "complete"
]

STAGE_HANDLERS = {
    "metadata": run_metadata_stage,
    "download": run_download_stage,
    "frame_extraction": run_frame_extraction_stage,
    # ... etc
}
```

---

## API Reference

### Endpoint 1: POST /api/ingest

**Purpose:** Queue a video for processing

**Request:**
```json
{
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "team_id": "team123",
  "player_number": 42,
  "position": "linebacker"
}
```

**Response:**
```json
{
  "video_id": "dQw4w9WgXcQ",
  "status": "queued",
  "message": "Video queued for processing"
}
```

**Validation:**
- `video_url`: Valid YouTube URL format
- `player_number`: Integer, 1-99
- `position`: Enum (db, dl, lb, qb, rb, wr, ol)

---

### Endpoint 2: GET /api/ingest/{video_id}/status

**Purpose:** Get processing progress

**Response:**
```json
{
  "video_id": "dQw4w9WgXcQ",
  "status": "processing",
  "progress": 45,
  "current_stage": "pose",
  "stages": {
    "metadata": "completed",
    "download": "completed",
    "frame_extraction": "completed",
    "pose": "in-progress",
    "torso_crop": "pending",
    ...
  }
}
```

---

### Endpoint 3-7: Analysis Endpoints

**GET /api/analysis/{video_id}** → Full results  
**GET /api/analysis/{video_id}/reps** → Rep details  
**GET /api/analysis/{video_id}/summary** → Aggregated stats  
**GET /api/analysis/{video_id}/metrics** → Trait statistics  

---

## Maintenance & Operations

### Daily Operations Checklist

- [ ] Worker is running and polling (check logs)
- [ ] Firestore quotas not exceeded (check GCP console)
- [ ] No stuck videos (check ingestion_queue collection)
- [ ] Disk space sufficient for raw_videos/
- [ ] API responding to health checks

### Common Tasks

#### Monitor Running Worker

```bash
tail -f team-grade-processing/logs/pipeline_worker.log
```

#### Manually Requeue Video

```python
from ingest.queue_manager import QueueManager
from ingest.firestore_client import FirestoreClient

db = FirestoreClient("project-id", "creds.json").db
qa = QueueManager(db)
qa.enqueue_video("video123", "download", attempt=1)
```

#### Check Queue Depth

```python
db = FirestoreClient(...).db
queue_size = len(db.collection("ingestion_queue").stream())
print(f"Videos in queue: {queue_size}")
```

#### Clean Stuck Jobs

```python
# Mark as failed so it doesn't block others
db.collection("ingestion_queue").document("video123_stage").delete()
```

---

## Troubleshooting Guide

### Problem: Worker Not Processing Videos

**Symptoms:** Videos stuck in "queued" status

**Diagnosis:**
1. Check if worker process is running
2. Check worker logs for errors
3. Check Firestore connectivity (credentials)
4. Check queue collection exists

**Solution:**
```bash
# Restart worker with debug output
python -m ingest.ingest_pipeline_worker --verbose
```

---

### Problem: API Errors

**Symptoms:** "Invalid video_id" errors

**Likely Cause:** Video ID contains special characters  
**Solution:** Verify URL format is valid YouTube URL

---

### Problem: Firestore Quota Exceeded

**Symptoms:** "Resource exhausted" errors

**Cause:** Too many concurrent writes (batch operations)  
**Solution:**
- Reduce batch size: `BATCH_SIZE = 50` (config/settings.py)
- Wait for quota reset (24-hour cycle)
- Upgrade Firestore tier

---

### Problem: Running Out of Disk Space

**Symptoms:** Frame extraction fails with "permission denied"

**Cause:** raw_videos/ directory full  
**Solution:**
```bash
# Delete old videos
rm team-grade-processing/raw_videos/youtube_*.mp4
# Or expand disk
```

---

## Security Checklist

✅ **Pre-Deployment:**
- [ ] Sanitize all user inputs (video_id, parameters)
- [ ] All logging uses safe_video_id (never raw input)
- [ ] Collection names use constants (no hardcoded strings)
- [ ] All paths validated (no traversal allowed)
- [ ] FFmpeg path restricted to allowlist
- [ ] Error messages don't leak sensitive data
- [ ] Credentials secured (never in code)
- [ ] Rate limiting configured (to prevent abuse)

✅ **Ongoing Monitoring:**
- [ ] Check logs for injection attempts
- [ ] Monitor API error rates
- [ ] Audit Firestore access patterns
- [ ] Review failed stage retries

---

## Configuration Reference

**Key Settings** (in `config/settings.py`):

```python
# Processing
FRAME_EXTRACTION_FPS = 15.0             # Frames per second
POSE_MODEL_NAME = "mediapipe"           # Pose model
POSE_DEVICE = "cpu"                     # "cpu" or "cuda"
POSE_CONFIDENCE_THRESHOLD = 0.7         # Keypoint confidence filter
OCR_STOP_ON_CONFIDENCE = 0.8            # Jersey detection confidence

# Queue
QUEUE_POLL_INTERVAL = 5.0               # Worker poll interval (seconds)
QUEUE_RETRY_BACKOFF = 0.1               # Initial backoff (seconds)
QUEUE_MAX_RETRIES = 3                   # Max retry attempts

# Firestore
BATCH_SIZE = 100                        # Batch write size
COLLECTION_VIDEOS = "videos"
COLLECTION_FRAMES = "frames"
# + 10+ more collection constants

# Paths
VIDEO_BASE_DIR = "team-grade-processing/raw_videos"
FRAMES_BASE_DIR = "team-grade-processing/frames"
```

---

## Conclusion

TEAM-GRADE combines:
- ✅ **Robust Architecture:** 9-stage pipeline with queue-based coordination
- ✅ **Security-First Design:** 38 security fixes applied across 8 layers
- ✅ **Cloud-Native:** Firestore for scalability, REST API for integration
- ✅ **Production-Ready:** Error handling, retry logic, monitoring

**Status:** ✅ Ready for production deployment

---

**Document Version:** 2.0  
**Last Security Audit:** March 20, 2026  
**Next Audit Due:** TBD (after 6 months or major changes)

