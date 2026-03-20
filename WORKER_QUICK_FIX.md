# Worker Loop Fix - Quick Reference

## 🐛 What Was Broken

Worker loop was silently failing due to **Windows Unicode encoding errors** in logging.

### The Silent Failure
```
Worker starts → Tries to log with "✓" character → Logger crashes with UnicodeEncodeError
→ Exception caught silently → Worker appears stuck but is actually in crash loop
→ Queue never polled → Jobs never processed → UI frozen at "pending/metadata"
```

## ✅ What's Fixed

### 1. Removed Unicode Characters (32 total)
- Replaced `✓` with `[OK]`
- Replaced `✗` with `[FAIL]`
- Replaced `⚠` with `[WARN]`
- Replaced `✓✓✓` with `[OK]`

**Files Fixed**:
- ✅ `ingest_worker.py` (15 instances)
- ✅ `queue_manager.py` (2 instances)
- ✅ `firestore_client.py` (16 instances)

### 2. Enhanced Worker Logging
Added clear, actionable log messages:
```
[DEBUG] Checking queue for jobs...
[INFO] [WORK] New job found: VIDEO_ID (stage: download)
[DEBUG] Processing job...
[INFO] [DONE] Job completed: VIDEO_ID
[DEBUG] Queue empty, waiting 5 seconds...
```

### 3. Verified Queue Integration
✅ QueueManager has `dequeue_next_video()` - **CORRECT**  
✅ FirestoreClient has `dequeue_next_video()` - **CORRECT**  
✅ Method chain is complete - **WORKING**  
✅ Worker loop properly processes jobs - **VERIFIED**  

## 🚀 How to Use

### Start Worker
```bash
cd team-grade-processing

# Start listening for jobs
python -m ingest.ingest_worker

# Output should show:
# [OK] Ingestion Worker Started (Listener Mode)
# Listening for jobs in queue...
# [DEBUG] Checking queue for jobs...
```

### Ingest Single Video
```bash
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=ABC123"
```

### Ingest Batch
```bash
python -m ingest.ingest_worker --batch URL1 URL2 URL3
```

### Check Queue Status
```bash
python -m ingest.ingest_worker --status
```

## 📊 Worker Flow

```
API → POST /ingest
  ↓
API creates videos/<video_id> in Firestore
  ↓
API calls ingest_youtube_url()
  ↓
Worker loop polls queue every 5 seconds
  ↓
Queue has new job: ingestion_queue/<video_id>
  ↓
Worker calls dequeue_next_video()
  ↓
Worker calls process_queue_item(queue_item)
  ↓
Worker updates video status → processing
  ↓
Worker processes video (download, extract, etc...)
  ↓
Worker updates video status → completed
  ↓
Worker removes from queue
  ↓
UI shows: Job complete! ✓
```

## 🔍 Verification Checklist

- [x] All Unicode characters removed (32 instances)
- [x] Syntax valid in all 3 files
- [x] QueueManager.dequeue_next_video() exists
- [x] FirestoreClient.dequeue_next_video() exists
- [x] Worker loop properly integrated
- [x] Enhanced logging for visibility
- [x] Error handling improved
- [x] Integration tests pass

## 🎯 Expected Behavior

### After Starting Worker
```
[OK] Ingestion Worker Started (Listener Mode)
Listening for jobs in queue...
[DEBUG] Checking queue for jobs...
[DEBUG] Queue empty, waiting 5 seconds...
... (waits in loop)
```

### When Job Arrives
```
[INFO] [WORK] New job found: ABC123 (stage: metadata)
[DEBUG] Processing job...
[INFO] [DONE] Job completed: ABC123
[DEBUG] Checking queue for jobs...
```

### On Error
```
[ERROR] [ERROR] Worker loop exception: Some error here
Retrying in 5 seconds...
```

## 📝 Files Modified

| File | Type | Changes |
|------|------|---------|
| ingest_worker.py | Core | Unicode removal + logging enhancement |
| queue_manager.py | Support | Unicode removal |
| firestore_client.py | Support | Unicode removal |

## ⚙️ Configuration

**Poll Interval**: 5 seconds (default, can be changed)
```python
worker.start_worker_loop(poll_interval=10)  # Change to 10 seconds
```

**Logging Level**: INFO (debug logs shown with -v flag)

## 🆘 Troubleshooting

### Worker stuck at "Checking queue..."
- Check if Firestore is accessible
- Check credentials file exists: `credentials/service-account.json`
- Check queue has items: use `--status` flag

### No jobs are being processed
- Verify jobs exist in Firestore `ingestion_queue` collection
- Check worker loop is running (see logs)
- Verify `process_queue_item()` doesn't throw exceptions

### Encoding errors in logs
- This is now FIXED - no more Unicode characters
- All logging is ASCII-safe for Windows

## 🎉 Status

**✅ FIXED AND VERIFIED**

Worker loop is now fully operational and will:
- ✓ Poll the queue continuously
- ✓ Detect new jobs immediately
- ✓ Process jobs correctly
- ✓ Update Firestore status
- ✓ Advance ingestion stages
- ✓ Log clearly without errors
