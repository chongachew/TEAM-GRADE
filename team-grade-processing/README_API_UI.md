# TEAM-GRADE Ingestion API + UI

Complete web interface for YouTube ingestion system.

## Quick Start

### 1. Install API Dependencies
```bash
cd team-grade-processing
pip install -r api/requirements.txt
```

### 2. Start the API Server
```bash
cd team-grade-processing
python api/server.py
```

The API will start on `http://localhost:8000`

### 3. Open the UI
Open your browser and go to:
```
file:///[absolute-path]/team-grade-processing/ui/index.html
```

Or serve it via HTTP:
```bash
# From the ui directory
# Using Python
python -m http.server 3000

# Then open http://localhost:3000/index.html
```

## API Endpoints

### Health Check
```
GET /health
```
Returns API status and component health.

### Ingest Video
```
POST /ingest
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```

Response:
```json
{
  "status": "queued",
  "video_id": "VIDEO_ID",
  "message": "Video queued for ingestion",
  "timestamp": "2026-03-13T10:30:45.123456"
}
```

### Get Video Status
```
GET /status/{video_id}
```

Response:
```json
{
  "video_id": "VIDEO_ID",
  "status": "processing",
  "stage": "pose_estimation",
  "progress": 65,
  "error": null
}
```

### Get Queue Status
```
GET /queue
```

Response:
```json
{
  "queued": 5,
  "processing": 2,
  "total": 7,
  "timestamp": "2026-03-13T10:30:45.123456"
}
```

## Architecture

```
team-grade-processing/
├── ingest/                    # Ingestion worker (do not modify)
│   ├── ingest_worker.py
│   ├── youtube_downloader.py
│   ├── youtube_metadata.py
│   ├── firestore_client.py
│   └── queue_manager.py
├── api/                       # REST API layer
│   ├── server.py              # FastAPI server
│   └── requirements.txt
└── ui/                        # Web UI
    └── index.html             # Standalone HTML + JS + Tailwind
```

## UI Features

- **YouTube URL Input** - Paste any YouTube video URL
- **Live Status Updates** - Polls every 2 seconds
- **Progress Tracking** - Visual progress bar with percentage
- **Stage Timeline** - Shows completion of each processing stage
- **Error Display** - Shows any errors encountered
- **Queue Integration** - Works with Firestore-backed queue

## Status Values

- `pending` - Waiting to be processed
- `queued` - In processing queue
- `downloaded` - Video downloaded
- `processing` - Currently being ingested
- `completed` - Successfully completed
- `failed` - Failed during processing

## Stages

1. `metadata` - Extract video metadata (10%)
2. `download` - Download video file (30%)
3. `frame_extraction` - Extract frames from video (50%)
4. `pose_estimation` - Run pose detection (65%)
5. `torso_cropping` - Crop torso regions (75%)
6. `jersey_ocr` - Extract jersey numbers (85%)
7. `rep_extraction` - Extract rep information (95%)
8. `completed` - All processing done (100%)

## Logging

API logs are written to `api.log` in the current directory.

## CORS

CORS is enabled for all origins, allowing the UI to communicate with the API.

## Troubleshooting

### API won't start
- Make sure port 8000 is available
- Check that all ingestion worker modules are accessible
- Verify FastAPI is installed: `pip install fastapi uvicorn`

### UI won't connect to API
- Ensure API server is running on `http://localhost:8000`
- Check browser console for CORS errors
- Verify API is accessible: `curl http://localhost:8000/health`

### Videos not showing status
- Check Firestore connection in the ingestion worker
- Verify video_id is correct
- Check `api.log` for detailed error messages

## Development

To run API with auto-reload:
```bash
uvicorn api.server:app --reload --host 0.0.0.0 --port 8000
```

To view API documentation:
```
http://localhost:8000/docs
```
