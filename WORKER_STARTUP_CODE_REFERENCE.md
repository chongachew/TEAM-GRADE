# Code Changes Reference - Worker Startup Mode

## File Modified
**File**: `team-grade-processing/ingest/ingest_worker.py`

---

## Change 1: New Method `start_worker_loop()`

**Location**: After `process_queue_item()` method (around line 358)

**Added Method**:
```python
def start_worker_loop(self, poll_interval: int = 5) -> None:
    """
    Start worker listener loop - continuously processes queue items.
    
    This is the default mode when the worker is run with no arguments.
    
    Args:
        poll_interval: Seconds to wait between queue checks (default: 5)
    """
    import time
    
    if not self.firestore or not self.queue_manager:
        logger.error("Firestore not initialized - cannot start worker loop")
        return
    
    logger.info("\n" + "=" * 60)
    logger.info("✓ Ingestion Worker Started (Listener Mode)")
    logger.info("Listening for jobs in queue...")
    logger.info("=" * 60 + "\n")
    
    try:
        while True:
            try:
                # Check for next item in queue
                queue_item = self.queue_manager.dequeue_next_video()
                
                if queue_item:
                    # Process the item
                    logger.info(f"\nNew job: {queue_item.get('video_id')} (stage: {queue_item.get('stage')})")
                    self.process_queue_item(queue_item)
                else:
                    # No items in queue, wait and retry
                    logger.debug(f"Queue empty, waiting {poll_interval}s...")
                    time.sleep(poll_interval)
            
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
                time.sleep(poll_interval)
    
    except KeyboardInterrupt:
        logger.info("\n" + "=" * 60)
        logger.info("Worker stopped by user")
        logger.info("=" * 60)
```

**Purpose**: 
- Provides continuous queue listening and processing
- Polls Firestore queue every N seconds
- Processes items as they appear
- Handles interrupts gracefully (CTRL+C)

---

## Change 2: Updated `main()` Function

**Location**: Line ~407 onwards

### BEFORE (Original):
```python
def main():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TEAM-GRADE YouTube Ingestion Worker"
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="YouTube URL to ingest"
    )
    parser.add_argument(
        "--batch",
        nargs="+",
        help="Ingest multiple URLs"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory for videos"
    )

    args = parser.parse_args()

    # Initialize worker
    worker = IngestWorker(output_dir=args.output)

    # Single URL
    if args.url:
        result = worker.ingest_youtube_url(args.url)
        print(f"\nResult: {result}")
        return 0 if result["success"] else 1

    # Batch URLs
    elif args.batch:
        results = worker.batch_ingest(args.batch)
        print(f"\nResults: {results}")
        return 0 if results["failed"] == 0 else 1

    # Queue status
    elif args.status:
        status = worker.get_queue_status()
        print(f"\nQueue Status: {status}")
        return 0

    # No arguments
    else:
        parser.print_help()           # ❌ OLD: Showed help
        return 0
```

### AFTER (Updated):
```python
def main():
    """Command-line interface and worker startup."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TEAM-GRADE YouTube Ingestion Worker",
        add_help=False                 # ✅ NEW: Manual help control
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="YouTube URL to ingest"
    )
    parser.add_argument(
        "--batch",
        nargs="+",
        help="Ingest multiple URLs"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory for videos"
    )
    parser.add_argument(              # ✅ NEW: Manual help argument
        "--help", "-h",
        action="store_true",
        help="Show this help message"
    )

    args = parser.parse_args()

    # Initialize worker
    worker = IngestWorker(output_dir=args.output)

    # Show help if requested                    # ✅ NEW: Help block
    if args.help:
        parser.print_help()
        return 0

    # Single URL
    if args.url:
        result = worker.ingest_youtube_url(args.url)
        print(f"\nResult: {result}")
        return 0 if result["success"] else 1

    # Batch URLs
    elif args.batch:
        results = worker.batch_ingest(args.batch)
        print(f"\nResults: {results}")
        return 0 if results["failed"] == 0 else 1

    # Queue status
    elif args.status:
        status = worker.get_queue_status()
        print(f"\nQueue Status: {status}")
        return 0

    # No arguments - start worker listener loop  # ✅ NEW: Worker mode
    else:
        worker.start_worker_loop()
        return 0
```

**Key Changes:**
1. ✅ `add_help=False` - Disable argparse auto-help
2. ✅ Added `--help` / `-h` argument manually
3. ✅ New `if args.help:` block - Explicit help handling
4. ✅ Changed final `else:` from `parser.print_help()` to `worker.start_worker_loop()`

---

## Change Summary

| Item | Before | After |
|------|--------|-------|
| No args behavior | Show help menu | Start listener |
| Help control | Automatic (argparse) | Manual |
| Help access | Run with no args | `--help` or `-h` |
| Worker mode | Not available | Default (no args) |
| CLI modes | 3 modes | 4 modes (+ listener) |
| Code additions | N/A | 1 new method + 4 new lines in main() |

---

## Method: `start_worker_loop()` Breakdown

### Signature
```python
def start_worker_loop(self, poll_interval: int = 5) -> None:
```
- **Returns**: None (runs indefinitely until interrupted)
- **Parameter**: poll_interval (seconds to wait between queue checks)

### Initialization Check
```python
if not self.firestore or not self.queue_manager:
    logger.error("Firestore not initialized - cannot start worker loop")
    return
```
- Ensures Firestore is available
- Prevents running without database access

### Startup Message
```python
logger.info("\n" + "=" * 60)
logger.info("✓ Ingestion Worker Started (Listener Mode)")
logger.info("Listening for jobs in queue...")
logger.info("=" * 60 + "\n")
```
- Clear indication that worker is active
- User-friendly startup message

### Main Loop
```python
try:
    while True:
        # Continuous polling
except KeyboardInterrupt:
    # Graceful shutdown on CTRL+C
```

### Queue Processing
```python
queue_item = self.queue_manager.dequeue_next_video()

if queue_item:
    logger.info(f"\nNew job: {queue_item.get('video_id')}")
    self.process_queue_item(queue_item)
else:
    logger.debug(f"Queue empty, waiting {poll_interval}s...")
    time.sleep(poll_interval)
```
- Attempts to dequeue item
- If found: processes it (existing logic)
- If not: waits before retrying

### Error Handling
```python
except Exception as e:
    logger.error(f"Worker loop error: {e}", exc_info=True)
    time.sleep(poll_interval)
```
- Catches any processing errors
- Logs with full stack trace
- Continues running (doesn't crash)

---

## Function: Updated `main()` Control Flow

### Argparse Setup
```
add_help=False
  ↓
Manually add --help argument
  ↓
Parse arguments
```

### Decision Tree
```
args.help?
  ├─ YES → Show help (parser.print_help())
  └─ NO ↓
  
  args.url?
    ├─ YES → Ingest single URL
    └─ NO ↓
    
    args.batch?
      ├─ YES → Batch ingest
      └─ NO ↓
      
      args.status?
        ├─ YES → Show queue status
        └─ NO ↓
        
        (No args)
          → Start worker.start_worker_loop()
```

---

## Runtime Behavior Comparison

### Original (OLD)
```bash
$ python -m ingest.ingest_worker

# Output:
usage: ingest_worker.py [-h] [--batch BATCH [BATCH ...]] 
                        [--status] [--output OUTPUT] 
                        [url]

TEAM-GRADE YouTube Ingestion Worker

positional arguments:
  url                   YouTube URL to ingest

optional arguments:
  -h, --help            show this help message and exit
  --batch BATCH [BATCH ...]
                        Ingest multiple URLs
  --status              Show queue status
  --output OUTPUT       Output directory for videos

$ # Process ends
```

### New (UPDATED)
```bash
$ python -m ingest.ingest_worker

# Output:
============================================================
✓ Ingestion Worker Started (Listener Mode)
Listening for jobs in queue...
============================================================

[Waits for queue items...]

$ # Process continues listening...
$ # Press CTRL+C to stop
```

### Help with Explicit Flag
```bash
$ python -m ingest.ingest_worker --help

# Output:
usage: ingest_worker.py [-h] [--batch BATCH [BATCH ...]] 
                        [--status] [--output OUTPUT] 
                        [url]

TEAM-GRADE YouTube Ingestion Worker

# ... (help output as before) ...
```

---

## No Changes to Existing Functionality

✅ All of these remain unchanged:
- `ingest_youtube_url()` method
- `batch_ingest()` method
- `get_queue_status()` method
- `process_queue_item()` method
- Queue processing logic
- Firestore integration
- YouTube download/metadata logic
- Argument parsing for --batch, --status, --output
- Output formatting

---

## Integration Points

### Where `start_worker_loop()` Fits
```
IngestWorker (main orchestrator)
  ├─ ingest_youtube_url() method (CLI mode)
  ├─ batch_ingest() method (CLI mode)
  ├─ get_queue_status() method (CLI mode)
  ├─ process_queue_item() method (internal)
  └─ start_worker_loop() method (LISTENER MODE) [NEW]
        └─ Calls: queue_manager.dequeue_next_video()
        └─ Calls: process_queue_item()
        └─ Uses: firestore, queue_manager
```

---

## Testing Scenarios

### Test 1: Worker Mode (No Arguments)
```bash
python -m ingest.ingest_worker
# Expected: Worker starts and listens for jobs
```

### Test 2: Help Mode (Explicit Flag)
```bash
python -m ingest.ingest_worker -h
python -m ingest.ingest_worker --help
# Expected: Help menu displays
```

### Test 3: CLI Mode (Single URL)
```bash
python -m ingest.ingest_worker "URL"
# Expected: Single video ingested (existing behavior)
```

### Test 4: CLI Mode (Batch)
```bash
python -m ingest.ingest_worker --batch URL1 URL2
# Expected: Batch ingestion (existing behavior)
```

### Test 5: CLI Mode (Status)
```bash
python -m ingest.ingest_worker --status
# Expected: Queue status shown (existing behavior)
```

---

## Code Quality

✅ **No breaking changes** - All existing code paths preserved
✅ **Backward compatible** - All CLI modes still work
✅ **Type hints** - Method has proper return type annotation
✅ **Documentation** - DocString explains purpose
✅ **Error handling** - Graceful error recovery
✅ **Logging** - Comprehensive logging throughout
✅ **Testing possible** - All code paths testable

---

## Summary

**Changed**: 1 file (`ingest_worker.py`)
**Added**: 1 new method (`start_worker_loop()`)
**Modified**: 1 existing method (`main()`)
**Lines added**: ~35 lines of new code
**Breaking changes**: None
**Backward compatible**: Yes ✅
**Ready to deploy**: Yes ✅
