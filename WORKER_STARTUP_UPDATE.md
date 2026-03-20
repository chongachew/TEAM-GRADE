# Ingestion Worker Startup Mode Update

## Summary

Your TEAM-GRADE ingestion worker has been updated to start in **listener/worker mode** when run with no arguments, instead of displaying the CLI help menu.

---

## What Changed

### 1. ✅ New Method: `start_worker_loop()`
**Added to IngestWorker class** (lines 358-392)

This method continuously:
- Polls the Firestore queue every 5 seconds
- Dequeues the next video job
- Processes the queue item
- Waits for the next job
- Gracefully stops on CTRL+C

**Features:**
- Automatic Firestore availability check
- Detailed logging ("Listening for jobs...")
- 5-second default poll interval (configurable)
- Exception handling with retry
- Keyboard interrupt support (CTRL+C)

### 2. ✅ Updated: `main()` Function
**Modified argparse setup** (lines 407-436)

**Key Changes:**
- Added `add_help=False` to disable default argparse help
- Manually added `--help` / `-h` argument for better control
- Added logic to show help only when explicitly requested

**Command Logic** (lines 438-464):
```
if --help:          → Show help menu
elif URL provided:  → Ingest single URL
elif --batch:       → Batch ingest multiple URLs
elif --status:      → Show queue status
else:               → Start worker loop (NEW!)
```

---

## How It Works Now

### Scenario 1: Run with No Arguments (Default - NEW!)
```bash
python -m ingest.ingest_worker
```

**Result:**
```
✓ Ingestion Worker Started (Listener Mode)
Listening for jobs in queue...

[Waits for queue items and processes them continuously]
```

**Behavior:**
- Continuously listens for jobs in the Firestore queue
- Processes videos as they arrive
- Runs until CTRL+C is pressed
- No CLI arguments needed

### Scenario 2: Request Help
```bash
python -m ingest.ingest_worker --help
```

**Result:** Shows the full argparse help menu

### Scenario 3: Ingest Single URL (CLI Mode)
```bash
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=ID"
```

**Result:** Ingests the single video and exits

### Scenario 4: Batch Ingest (CLI Mode)
```bash
python -m ingest.ingest_worker --batch URL1 URL2 URL3
```

**Result:** Ingests all three videos and exits

### Scenario 5: Check Queue Status (CLI Mode)
```bash
python -m ingest.ingest_worker --status
```

**Result:** Shows current queue statistics and exits

---

## Code Changes in Detail

### Addition: `start_worker_loop()` Method
```python
def start_worker_loop(self, poll_interval: int = 5) -> None:
    """
    Start worker listener loop - continuously processes queue items.
    This is the default mode when the worker is run with no arguments.
    """
    import time
    
    # Check Firestore is initialized
    if not self.firestore or not self.queue_manager:
        logger.error("Firestore not initialized - cannot start worker loop")
        return
    
    # Start logging
    logger.info("\n" + "=" * 60)
    logger.info("✓ Ingestion Worker Started (Listener Mode)")
    logger.info("Listening for jobs in queue...")
    logger.info("=" * 60 + "\n")
    
    # Worker loop
    try:
        while True:
            try:
                # Get next queue item
                queue_item = self.queue_manager.dequeue_next_video()
                
                if queue_item:
                    # Process it
                    logger.info(f"\nNew job: {queue_item.get('video_id')}")
                    self.process_queue_item(queue_item)
                else:
                    # No jobs, wait and retry
                    logger.debug(f"Queue empty, waiting {poll_interval}s...")
                    time.sleep(poll_interval)
            
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
                time.sleep(poll_interval)
    
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
```

### Modification: `main()` Function

**Before:**
```python
def main():
    parser = argparse.ArgumentParser(description="...")
    # ... args setup ...
    
    # No arguments
    else:
        parser.print_help()  # ❌ Printed help
        return 0
```

**After:**
```python
def main():
    parser = argparse.ArgumentParser(
        description="...",
        add_help=False  # ✅ Manual help control
    )
    # ... args setup ...
    parser.add_argument("--help", "-h", action="store_true", help="Show this help message")
    
    # ... URL/batch/status logic ...
    
    # No arguments - start worker listener loop
    else:
        worker.start_worker_loop()  # ✅ Start listening
        return 0
```

---

## What Was NOT Changed

✅ No changes to ingestion logic
✅ No changes to Firestore initialization
✅ No changes to queue processing logic
✅ No changes to youtube_downloader.py
✅ No changes to youtube_metadata.py
✅ No changes to queue_manager.py
✅ No changes to firestore_client.py
✅ All existing imports intact
✅ All existing methods unchanged

---

## Usage Examples

### Start Worker Listener (New Default)
```powershell
# Start with no arguments - begins listening for jobs
python -m ingest.ingest_worker

# Output:
# ✓ Ingestion Worker Started (Listener Mode)
# Listening for jobs in queue...
# [Processes jobs as they appear]
```

### Ingest Video via CLI
```powershell
# Still works as before
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=ABC123"
```

### Show Help
```powershell
# Now requires explicit --help flag
python -m ingest.ingest_worker --help
```

---

## Expected Log Output

When you run `python -m ingest.ingest_worker`:

```
============================================================
✓ Ingestion Worker Started (Listener Mode)
Listening for jobs in queue...
============================================================

New job: dQw4w9WgXcQ (stage: frame_extraction)
Processing dQw4w9WgXcQ at stage frame_extraction
✓ Completed processing dQw4w9WgXcQ

New job: jNQXAC9IVRw (stage: pose_estimation)
Processing jNQXAC9IVRw at stage pose_estimation
✓ Completed processing jNQXAC9IVRw

[Waits for more jobs...]
```

---

## Key Features of the Worker Loop

| Feature | Details |
|---------|---------|
| **Poll Interval** | 5 seconds (configurable) |
| **Firestore Check** | Verifies initialized before starting |
| **Error Handling** | Catches and logs exceptions, continues |
| **Graceful Shutdown** | CTRL+C stops cleanly |
| **Logging** | Detailed progress and error logging |
| **Queue Status** | Logs when queue is empty vs. when jobs arrive |

---

## Backward Compatibility

✅ All existing CLI commands still work:
- `python -m ingest.ingest_worker URL` → Single URL ingestion
- `python -m ingest.ingest_worker --batch URL1 URL2` → Batch ingestion
- `python -m ingest.ingest_worker --status` → Show queue status
- `python -m ingest.ingest_worker --help` → Show help

---

## Testing the Changes

### Test 1: Start Worker (No Arguments)
```bash
python -m ingest.ingest_worker
# Should show "✓ Ingestion Worker Started (Listener Mode)"
# And wait for queue items
# Press CTRL+C to stop
```

### Test 2: Verify Help Works
```bash
python -m ingest.ingest_worker --help
# Should show the argparse help menu
```

### Test 3: Verify Single URL Still Works
```bash
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=TEST"
# Should ingest the video as before
```

### Test 4: Verify Queue Status Still Works
```bash
python -m ingest.ingest_worker --status
# Should show queue statistics
```

---

## Summary

Your TEAM-GRADE ingestion worker now:

✅ **Starts in listener mode by default** - Just run `python -m ingest.ingest_worker` with no args
✅ **Continuously processes queue items** - Automatically picks up new jobs from Firestore
✅ **Maintains CLI functionality** - All existing commands still work
✅ **Has graceful error handling** - Continues running on errors
✅ **Logs detailed progress** - Full visibility into what's happening
✅ **Supports graceful shutdown** - CTRL+C stops cleanly

**Ready to deploy!** 🚀
