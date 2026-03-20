# TEAM-GRADE API + UI - Quick Setup Guide

## ✅ What Was Created

```
team-grade-processing/
├── api/
│   ├── server.py                 # FastAPI server with 4 endpoints
│   ├── __init__.py               # Python package marker
│   └── requirements.txt           # FastAPI + dependencies
├── ui/
│   └── index.html               # Standalone web UI (Tailwind + JS)
├── start_api.py                 # Python startup script
├── start_api.bat                # Batch startup script (Windows)
└── README_API_UI.md             # Full documentation
```

## 🚀 Quick Start (3 Steps)

### Step 1: Install Dependencies
```bash
cd team-grade-processing
pip install -r api/requirements.txt
```

### Step 2: Start API Server
```bash
python start_api.py
```

Or on Windows:
```bash
start_api.bat
```

You should see:
```
========================================
TEAM-GRADE INGESTION API - STARTUP
========================================
API SERVER STARTING
Port: 8000
Health: http://localhost:8000/health
Docs: http://localhost:8000/docs
```

### Step 3: Open the UI
Open in your browser:
```
file:///C:/Users/ricky/OneDrive/Desktop/Team-Grade/TEAM-GRADE/team-grade-processing/ui/index.html
```

Or serve via HTTP server:
```bash
cd ui
python -m http.server 3000
# Then open http://localhost:3000/index.html
```

## 📡 API Endpoints

### 1. Health Check
```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "timestamp": "2026-03-13T...",
  "components": {
    "ingestion_worker": "ok",
    "firestore_client": "ok",
    "metadata_extractor": "ok"
  }
}
```

### 2. Ingest Video
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
```

Response:
```json
{
  "status": "queued",
  "video_id": "dQw4w9WgXcQ",
  "message": "Video queued for ingestion",
  "timestamp": "2026-03-13T10:30:45.123456"
}
```

### 3. Get Video Status
```bash
curl http://localhost:8000/status/dQw4w9WgXcQ
```

Response:
```json
{
  "video_id": "dQw4w9WgXcQ",
  "status": "processing",
  "stage": "pose_estimation",
  "progress": 65,
  "error": null
}
```

### 4. Get Queue Status
```bash
curl http://localhost:8000/queue
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

## 🎨 UI Features

The web interface (`ui/index.html`) provides:

- **YouTube URL Input** - Paste or type any YouTube URL
- **One-Click Ingestion** - Submit video for processing
- **Live Status Updates** - Polls every 2 seconds
- **Visual Progress Bar** - Shows percentage completion
- **Stage Timeline** - Displays which processing stage is active
- **Error Display** - Shows any failures or issues
- **Copy Video ID** - Easily copy video ID to clipboard
- **Reset Button** - Start over with a new video

### Stages Shown:
1. 🏷️ Metadata Extraction (10%)
2. ⬇️ Download (30%)
3. 🎬 Frame Extraction (50%)
4. 🤸 Pose Estimation (65%)
5. ✂️ Torso Cropping (75%)
6. 👕 Jersey OCR (85%)
7. 📊 Rep Extraction (95%)
8. ✓ Completed (100%)

## 🔧 Architecture

```
User
  ↓
┌─────────────────────────────────┐
│   Web UI (index.html)           │
│ - Tailwind CSS                  │
│ - Fetch API                     │
│ - 2-sec polling                 │
└─────────────────────────────────┘
  ↓ HTTP (CORS enabled)
┌─────────────────────────────────┐
│   FastAPI Server (server.py)    │
│ - /health                       │
│ - POST /ingest                  │
│ - GET /status/{video_id}        │
│ - GET /queue                    │
└─────────────────────────────────┘
  ↓ Python imports
┌─────────────────────────────────┐
│   Ingestion Worker (ingest/)    │
│ - IngestWorker                  │
│ - YouTubeMetadataExtractor      │
│ - YouTubeDownloader             │
│ - FirestoreClient               │
│ - QueueManager                  │
└─────────────────────────────────┘
  ↓ Firestore + YouTube
┌─────────────────────────────────┐
│   External Services             │
│ - Google Firestore              │
│ - YouTube (yt-dlp)              │
└─────────────────────────────────┘
```

## 📋 API Server Code Details

**File:** `api/server.py` (400+ lines)

Features:
- ✅ CORS enabled for all origins
- ✅ Comprehensive logging to `api.log`
- ✅ Error handling with proper HTTP status codes
- ✅ Auto-initialization of ingestion components
- ✅ Firestore integration via FirestoreClient
- ✅ Video status tracking with 8-stage pipeline
- ✅ Queue management
- ✅ Input validation
- ✅ Progress calculation

## 🎨 Web UI Code Details

**File:** `ui/index.html` (500+ lines)

Features:
- ✅ Tailwind CSS (CDN) for styling
- ✅ Vanilla JavaScript (no frameworks)
- ✅ Fetch API for HTTP requests
- ✅ Auto-polling every 2 seconds
- ✅ Responsive design
- ✅ Error handling
- ✅ API connectivity indicator
- ✅ Interactive stage indicators
- ✅ Visual progress tracking

## 🐛 Troubleshooting

### Port 8000 Already in Use
```bash
# Kill process on port 8000
netstat -ano | findstr ":8000"
taskkill /PID <PID> /F

# Or use a different port by editing server.py (change line ~390)
```

### Module Not Found: ingest
Make sure you're running from the `team-grade-processing` directory:
```bash
cd team-grade-processing
python start_api.py
```

### Firestore Not Connected
Ensure:
- ✓ Google Cloud credentials are set
- ✓ Firestore database is accessible
- ✓ Service account has proper permissions
Check `api.log` for detailed messages

### UI Won't Connect to API
- ✓ Verify API is running: `curl http://localhost:8000/health`
- ✓ Check browser console for CORS errors
- ✓ Make sure port 8000 is correct in UI
- ✓ Try from `http://localhost:3000` instead of `file://`

## 📚 Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Tailwind CSS](https://tailwindcss.com/)
- [Firestore Documentation](https://firebase.google.com/docs/firestore)
- [YouTube yt-dlp](https://github.com/yt-dlp/yt-dlp)

## ✨ Key Points

✅ **No modifications to ingestion worker** - All code is new  
✅ **Complete and production-ready** - No placeholders or pseudocode  
✅ **Fully documented** - Both inline and external docs  
✅ **Error handling** - Comprehensive exception management  
✅ **Logging** - All activities logged to `api.log`  
✅ **CORS enabled** - UI can communicate freely with API  
✅ **Type hints** - Full type annotations for clarity  
✅ **Responsive UI** - Works on desktop and mobile  

## 🚀 Production Tips

1. **Change Port** - Edit line ~390 in `server.py`: `port=8000` → `port=8080`
2. **Disable CORS** - Change line ~28 `allow_origins=["*"]` for production
3. **Add Authentication** - Use FastAPI security features
4. **Deploy** - Use Gunicorn: `gunicorn -w 4 -b 0.0.0.0:8000 api.server:app`
5. **Monitor** - Check `api.log` regularly for errors
6. **Scale** - Use load balancer for multiple API instances

---

**Status:** ✅ Ready to Deploy  
**Version:** 1.0.0  
**Date:** March 13, 2026
