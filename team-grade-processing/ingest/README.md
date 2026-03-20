# TEAM-GRADE YouTube Ingestion Worker

Video ingestion pipeline for college football games: **YouTube → Metadata → Download → Firestore → Processing Queue**

Runs on local gaming PC and handles automated video collection from YouTube with metadata parsing and queue management for downstream processing.

## Overview

The ingestion worker handles the complete workflow:

1. **Metadata Extraction** - Extract video ID, title, description from URL
2. **Metadata Parsing** - Parse football-specific data (teams, year, level, CFP round)
3. **Video Download** - Download full-quality video using yt-dlp
4. **Firestore Storage** - Save metadata and track video status
5. **Queue Management** - Add to processing queue with priority

## Architecture

```
ingest/
├── ingest_worker.py          # Main orchestrator
├── youtube_metadata.py       # URL/metadata parsing
├── youtube_downloader.py     # Video download (yt-dlp)
├── firestore_client.py       # Firestore CRUD operations
├── queue_manager.py          # Priority queue management
└── __init__.py               # Module exports
```

## Installation

### Prerequisites

- Python 3.8+
- Google Cloud Firestore credentials (already authenticated on your machine)
- YouTube videos to ingest

### Install Dependencies

```bash
# From team-grade-processing root
pip install -r requirements.txt

# Additional ingestion packages
pip install yt-dlp google-cloud-firestore
```

## Quick Start

### Single Video

```python
from ingest import IngestWorker

worker = IngestWorker(
    output_dir="raw_videos",      # Where to save videos
    log_dir="logs",                # Where to save logs
    firestore_project="the-bridge-athletics1"
)

result = worker.ingest_youtube_url("https://www.youtube.com/watch?v=...")

print(f"Success: {result['success']}")
print(f"Video ID: {result['video_id']}")
print(f"Message: {result['message']}")
```

### Command Line

```bash
# Single video
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=..."

# Batch videos
python -m ingest.ingest_worker --batch "URL1" "URL2" "URL3"

# Check queue status
python -m ingest.ingest_worker --status

# Custom output directory
python -m ingest.ingest_worker "URL" --output /path/to/videos
```

## Module Reference

### IngestWorker - Main Orchestrator

**Initialize:**
```python
from ingest import IngestWorker

worker = IngestWorker(
    output_dir="raw_videos",
    log_dir="logs",
    firestore_project="the-bridge-athletics1"
)
```

**Ingest single video:**
```python
result = worker.ingest_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
# Returns:
# {
#   "success": True,
#   "message": "Success",
#   "video_id": "dQw4w9WgXcQ",
#   "file_size_mb": 456.3,
#   "teams": "Alabama vs LSU",
#   "download_path": "raw_videos/youtube_dQw4w9WgXcQ_...",
#   "timestamp": "2026-03-11T..."
# }
```

**Batch ingest:**
```python
results = worker.batch_ingest([
    "https://youtube.com/watch?v=...",
    "https://youtube.com/watch?v=...",
])
# Returns:
# {
#   "total": 2,
#   "successful": 2,
#   "failed": 0,
#   "results": [...]
# }
```

**Queue status:**
```python
status = worker.get_queue_status()
# Returns:
# {
#   "queued": 5,
#   "processing": 2,
#   "total": 7,
#   "timestamp": "2026-03-11T..."
# }
```

### YouTubeMetadataExtractor - Metadata Parsing

**Extract video ID:**
```python
from ingest import get_video_id

video_id = get_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
# Returns: "dQw4w9WgXcQ"

video_id = get_video_id("https://youtu.be/dQw4w9WgXcQ")
# Returns: "dQw4w9WgXcQ"

video_id = get_video_id("dQw4w9WgXcQ")  # Also accepts raw ID
# Returns: "dQw4w9WgXcQ"
```

**Parse football metadata:**
```python
from ingest import parse_football_metadata

metadata = parse_football_metadata(
    title="Alabama vs LSU - College Football Playoff Semifinal 2024",
    description="Historic matchup between rivals...",
    video_id="abc123"
)
# Returns:
# {
#   "video_id": "abc123",
#   "title": "Alabama vs LSU - College Football Playoff Semifinal 2024",
#   "team1": "Alabama",
#   "team2": "LSU",
#   "year": 2024,
#   "level": "college",
#   "cfp_round": "CFP_SEMIFINAL",
#   "confidence": {"teams": 0.8, "year": 0.9, "level": 0.8, "cfp_round": 0.7},
#   "parsed_at": "2026-03-11T..."
# }
```

**Validate YouTube URL:**
```python
from ingest import validate_youtube_url

is_valid = validate_youtube_url("https://www.youtube.com/watch?v=...")
# Returns: True or False
```

### YouTubeDownloader - Video Downloads

**Download video:**
```python
from ingest import YouTubeDownloader
from pathlib import Path

downloader = YouTubeDownloader(output_dir="raw_videos")

video_path = downloader.download_video(
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    output_path=None,  # Auto-generated if None
    max_retries=3
)
# Returns: Path("raw_videos/youtube_dQw4w9WgXcQ_20260311_120000.mp4")
```

**Get video info without downloading:**
```python
info = downloader.get_video_info("https://www.youtube.com/watch?v=...")
# Returns:
# {
#   "title": "Video Title",
#   "duration": 7200,  # seconds
#   "upload_date": "20240101",
#   "description": "Video description...",
#   "uploader": "Channel Name",
#   "view_count": 1000000
# }
```

**List downloaded videos:**
```python
videos = downloader.list_downloads()
# Returns: [Path("raw_videos/youtube_...mp4"), ...]
```

### FirestoreClient - Database Operations

**Initialize:**
```python
from ingest import FirestoreClient

client = FirestoreClient(
    project_id="the-bridge-athletics1",
    database="(default)"
)
```

**Save metadata:**
```python
metadata = {
    "title": "Alabama vs LSU",
    "team1": "Alabama",
    "team2": "LSU",
    "year": 2024,
    "level": "college",
    "download_path": "raw_videos/..."
}

success = client.save_video_metadata("video_id", metadata)
```

**Get metadata:**
```python
metadata = client.get_video_metadata("video_id")
```

**Update status:**
```python
from ingest import VideoStatus

client.update_video_status(
    "video_id",
    VideoStatus.DOWNLOADED,
    error_message=None
)
```

**Add to queue:**
```python
from ingest import IngestStage

success = client.add_to_queue(
    video_id="video_id",
    stage=IngestStage.FRAME_EXTRACTION,
    priority=5,  # 1-10, lower=higher priority
    metadata={"key": "value"}
)
```

**Dequeue next video:**
```python
queue_item = client.dequeue_next_video()
# Returns:
# {
#   "video_id": "abc123",
#   "stage": "frame_extraction",
#   "priority": 5,
#   "status": "queued",
#   "_doc_id": "05_abc123_xyz..."
# }
```

### QueueManager - Queue Operations

**Initialize:**
```python
from ingest import QueueManager, FirestoreClient

client = FirestoreClient()
manager = QueueManager(client)
```

**Enqueue video:**
```python
success = manager.enqueue_video(
    video_id="video_id",
    stage="frame_extraction",
    priority=5,
    metadata={"key": "value"}
)
```

**Dequeue next:**
```python
queue_item = manager.dequeue_next_video()
```

**Mark completed:**
```python
success = manager.mark_completed(
    queue_doc_id="queue_item['_doc_id']",
    metadata={"result_key": "value"}
)
```

**Mark failed (with retry):**
```python
success = manager.mark_failed(
    queue_doc_id="queue_item['_doc_id']",
    error_message="Detailed error message",
    retry=True  # Re-queue for retry
)
```

**Get queue stats:**
```python
stats = manager.get_queue_stats()
# Returns:
# {
#   "queued": 5,
#   "processing": 2,
#   "total": 7,
#   "timestamp": "2026-03-11T..."
# }
```

## Firestore Collections

### `videos/` Collection

Stores video metadata and status.

**Document ID:** YouTube video ID

**Fields:**
```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Video Title",
  "description": "Video description...",
  "team1": "Alabama",
  "team2": "LSU",
  "year": 2024,
  "level": "college",
  "cfp_round": "CFP_SEMIFINAL",
  "download_path": "raw_videos/youtube_...",
  "file_size_mb": 456.3,
  "status": "downloaded",
  "created_at": "2026-03-11T12:00:00",
  "updated_at": "2026-03-11T12:05:00",
  "parsed_at": "2026-03-11T12:00:01"
}
```

### `ingestion_queue/` Collection

FIFO queue for processing stages.

**Document ID:** Auto-generated (priority_timestamp_video_id)

**Fields:**
```json
{
  "video_id": "dQw4w9WgXcQ",
  "stage": "frame_extraction",
  "priority": 5,
  "status": "queued",
  "created_at": "2026-03-11T12:00:00",
  "updated_at": "2026-03-11T12:05:00",
  "retry_count": 0,
  "max_retries": 3
}
```

## Status and Stages

### Video Status

- `pending` - Metadata saved, not yet processed
- `downloaded` - Video file downloaded
- `ingested` - Video ingested (frames extracted)
- `processing` - Currently being processed
- `completed` - Processing finished
- `failed` - Processing failed

### Ingestion Stages

- `metadata` - Metadata extraction
- `download` - Video download
- `frame_extraction` - Extract frames from video
- `pose_estimation` - Estimate poses
- `torso_cropping` - Crop torso regions
- `jersey_ocr` - Read jersey numbers
- `rep_extraction` - Extract reps/plays
- `completed` - All stages complete

## Priority Levels

Queue items support priority levels (1-10):

- `1` = Highest priority (urgent)
- `5` = Normal priority (default)
- `10` = Lowest priority (background)

## Workflow Example

```python
from ingest import IngestWorker

# Initialize worker
worker = IngestWorker()

# Ingest video
result = worker.ingest_youtube_url("https://youtube.com/watch?v=...")

if result["success"]:
    print(f"✓ Video ingested: {result['video_id']}")
    
    # Check queue status
    status = worker.get_queue_status()
    print(f"Queue: {status['queued']} queued, {status['processing']} processing")
    
    # Process next item (usually in separate worker)
    next_item = worker.queue_manager.dequeue_next_video()
    if next_item:
        worker.process_queue_item(next_item)
else:
    print(f"✗ Ingestion failed: {result['message']}")
```

## Error Handling

All modules include comprehensive error handling:

**YouTube URL errors:**
```python
# Invalid URL
result = worker.ingest_youtube_url("not-a-url")
# Returns: {"success": False, "message": "Invalid YouTube URL or video ID", ...}
```

**Firestore errors:**
```python
# Connection issues automatically logged
# Firestore authentication must be set up on machine
```

**Download errors:**
```python
# Automatic retry with exponential backoff
# Max 3 retries with partial file cleanup
```

## Logging

All operations logged to `logs/ingest.log`:

```
2026-03-11 12:00:00 - ingest_worker - INFO - Starting ingestion for: https://youtube.com/...
2026-03-11 12:00:01 - youtube_metadata - INFO - Video ID: dQw4w9WgXcQ
2026-03-11 12:00:02 - youtube_downloader - INFO - Starting download from https://youtube.com/...
2026-03-11 12:02:30 - youtube_downloader - INFO - ✓ Download successful: youtube_...mp4 (456.3 MB)
2026-03-11 12:02:31 - firestore_client - INFO - ✓ Saved metadata for dQw4w9WgXcQ
2026-03-11 12:02:32 - queue_manager - INFO - ✓ Added dQw4w9WgXcQ to queue
2026-03-11 12:02:32 - ingest_worker - INFO - ✓✓✓ Ingestion completed successfully ✓✓✓
```

## Troubleshooting

### "yt-dlp not installed"

```bash
pip install yt-dlp
```

### "google-cloud-firestore not installed"

```bash
pip install google-cloud-firestore
```

### "Failed to initialize Firestore"

Ensure Google Cloud credentials are set:

```bash
# Set environment variable
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"

# Or use Application Default Credentials
gcloud auth application-default login
```

### "Invalid YouTube URL"

Valid formats:
- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/embed/VIDEO_ID`
- `VIDEO_ID` (raw 11-character ID)

### Download very slow

- Check internet connection
- Verify yt-dlp is up to date: `pip install --upgrade yt-dlp`
- Check YouTube video availability
- Try again later

### Firestore quota exceeded

- Check Firestore usage dashboard
- Increase quota or wait for reset
- Reduce batch size

## Performance Tips

- **Batch ingestion** for multiple videos (use `--batch` or `worker.batch_ingest()`)
- **Priority levels** for important videos (set `priority=1`)
- **Monitor queue** regularly with `--status`
- **Check logs** for detailed diagnostics

## Next Steps

After ingestion completes:

1. **Frame Extraction** - Run processing pipeline on downloaded video
2. **Pose Estimation** - Use main.py for pose detection
3. **Reps Extraction** - Group frames into plays
4. **Backend Integration** - Send results to TEAM-GRADE backend (port 3001)

## Support

**Issues?** Check `logs/ingest.log` for detailed output

**Usage:** `python -m ingest.ingest_worker --help`

**Development:** All modules use docstrings for inline documentation
