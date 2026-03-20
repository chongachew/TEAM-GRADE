"""
INGESTION API FIX - COMPLETE IMPLEMENTATION SUMMARY

This document summarizes the fix for the ingestion API logic bug and the three 
new reliability tools added to the system.

=============================================================================
1. THE BUG AND THE FIX
=============================================================================

SYMPTOM:
--------
The API endpoint /ingest was refusing to enqueue jobs when a video document 
existed with status="pending", even if no queue entry existed. This caused:
- Worker never receives the job
- UI gets stuck at "pending / metadata"
- Fresh ingest attempts are blocked
- No recovery mechanism

ROOT CAUSE:
-----------
The /ingest endpoint code checked:
    if existing_status and existing_status.get("status") != "failed"
        → block enqueue and return "already_queued"

This blocked ALL non-failed statuses, including "pending".
But the correct behavior is:
- If status == "complete" → already processed (block)
- If status == "processing" → already processing (block)
- If status == "pending" AND queue_exists → already queued (block)
- If status == "pending" AND NO queue_exists → RECOVERY case (enqueue!)
- If no video exists → fresh ingest (enqueue!)

THE FIX:
--------
Modified /ingest endpoint (api/server.py, lines 131-200) to:

1. Check queue integrity for the video:
    integrity = firestore_client.check_queue_integrity(video_id)
    
   This returns:
    {
        "video_exists": bool,
        "queue_exists": bool,
        "status": "pending|processing|completed|failed",
        "stage": "metadata|download|...",
        "error": optional_error_msg
    }

2. Apply decision logic:
    - status == "completed" → block (not recoverable)
    - status == "processing" AND queue_exists → block (already processing)
    - status == "pending" AND queue_exists → block (already queued)
    - status == "pending" AND NOT queue_exists → ENQUEUE (recovery!)
    - video doesn't exist → ENQUEUE (fresh)

3. Log clearly:
    "Enqueuing job for <video_id>"
    "Skipping enqueue: already complete"
    "Skipping enqueue: already processing"
    "Recovery enqueue: pending but no queue entry exists"

=============================================================================
2. NEW FIRESTORE CLIENT METHODS
=============================================================================

Location: team-grade-processing/ingest/firestore_client.py

Added Methods:

(A) queue_entry_exists(video_id: str) -> bool
    Purpose: Check if a queue entry exists for a video
    Logic: Queries ingestion_queue collection for matching video_id
    Returns: True if queue entry found, False otherwise
    
(B) check_queue_integrity(video_id: str) -> Dict[str, Any]
    Purpose: Check both video and queue state in one call
    Returns: {
        "video_exists": bool,
        "queue_exists": bool,
        "status": str (pending|processing|completed|failed),
        "stage": str (metadata|download|...),
        "error": optional error message
    }
    Used by: API endpoint, watchdog, cleanup script
    
(C) get_pending_videos(max_age_minutes: Optional[int]) -> List[Dict]
    Purpose: Find all videos stuck in "pending" status
    Optional: Filter to only videos older than specified minutes
    Returns: List of video documents
    Used by: Watchdog, cleanup script
    
(D) get_stale_queue_entries(max_age_minutes: int) -> List[Dict]
    Purpose: Find queue entries older than specified minutes
    Returns: List of queue documents with status="queued"
    Used by: Cleanup script
    
(E) delete_queue_entry_by_video_id(video_id: str) -> bool
    Purpose: Delete all queue entries for a video
    Returns: True if deleted, False if not found or error
    Used by: Cleanup script, recovery logic

=============================================================================
3. QUEUE INTEGRITY CHECKER
=============================================================================

Location: Built into firestore_client.py as check_queue_integrity()

Functionality:
- Verifies whether a queue entry exists for a given video_id
- Verifies whether the video document exists
- Returns both video status and queue status in structured format
- Returns: {
    "video_exists": bool,
    "queue_exists": bool,
    "status": "...",
    "stage": "...",
    "error": optional
}

Usage in Code:
    integrity = firestore_client.check_queue_integrity(video_id)
    if integrity["queue_exists"]:
        # Handle case where job is already queued
    elif integrity["video_exists"] and integrity["status"] == "pending":
        # Recovery case: re-enqueue
    else:
        # Fresh ingest: create new video and enqueue

=============================================================================
4. SELF-HEALING INGESTION WATCHDOG
=============================================================================

Location: team-grade-processing/ingest/ingestion_watchdog.py

Purpose:
- Automatically detect videos stuck in "pending" status with missing queue entries
- Re-enqueue jobs for stuck videos
- Prevent UI from remaining stuck
- Minimal interference with normal ingestion flow

Key Functions:

(A) check_and_repair_video(firestore_client, video_id) -> Dict
    Purpose: Check single video and repair if needed
    Returns: {
        "video_id": str,
        "repaired": bool,
        "reason": str (why it needed repair),
        "action": str (what was done)
    }
    
    Repair rules:
    - If video is pending AND no queue entry → re-enqueue with priority=7
    - If video is orphaned (no queue) AND in download/processing → re-enqueue
    - Mark recovered jobs with metadata: recovery=True, recovered_at=timestamp
    
(B) repair_all_pending_videos(firestore_client, max_pending_minutes=30) -> Dict
    Purpose: Scan all pending videos and repair stuck ones
    Returns: {
        "total_pending": int,
        "total_repaired": int,
        "repairs": [list of repair results],
        "timestamp": str
    }

CLI Usage:
    python -m ingest.ingestion_watchdog
    
Code Usage:
    from ingest.ingestion_watchdog import repair_all_pending_videos
    summary = repair_all_pending_videos(firestore_client, max_pending_minutes=30)
    print(f"Repaired {summary['total_repaired']} videos")

Logging Output:
    ⚠ Found stuck video: VIDEO_ID (pending=pending, queue_exists=False)
    ✓ Watchdog: repaired missing queue entry for VIDEO_ID

=============================================================================
5. FIRESTORE CLEANUP SCRIPT
=============================================================================

Location: team-grade-processing/ingest/ingestion_cleanup.py

Purpose:
- Remove videos stuck in "pending" for too long
- Remove queue entries older than retention period
- Prevent database from accumulating stale data
- Provide dry-run mode for testing

Key Function:

cleanup_stale_ingestion_entries(
    firestore_client,
    max_pending_minutes=60,      # Videos pending > 1 hour
    max_queue_age_minutes=120,   # Queue entries > 2 hours
    dry_run=False                 # If True, don't actually delete
) -> Dict

Returns: {
    "pending_videos": {
        "stale_video_count": int,
        "cleaned_count": int,
        "cleaned_videos": [list]
    },
    "queue_entries": {
        "stale_queue_count": int,
        "cleaned_count": int,
        "cleaned_entries": [list]
    },
    "timestamp": str,
    "dry_run": bool
}

Sub-functions:
- cleanup_stale_pending_videos() → removes old pending videos
- cleanup_stale_queue_entries() → removes old queue entries

CLI Usage:
    # Dry run (preview changes without deleting)
    python -m ingest.ingestion_cleanup --dry-run
    
    # Actually delete stale entries
    python -m ingest.ingestion_cleanup

Code Usage:
    from ingest.ingestion_cleanup import cleanup_stale_ingestion_entries
    
    summary = cleanup_stale_ingestion_entries(
        firestore_client,
        max_pending_minutes=60,
        max_queue_age_minutes=120,
        dry_run=True  # Test first!
    )

Logging Output:
    Stale pending video: VIDEO_ID (age: 125.5 min, queue_exists: False)
      ✓ Deleted video document: VIDEO_ID
      ✓ Deleted queue entry: VIDEO_ID

=============================================================================
6. MODIFIED FILES
=============================================================================

File 1: team-grade-processing/ingest/firestore_client.py
--------
Changes:
  - Added 5 new methods (see section 2 above)
  - No changes to existing methods
  - All changes backward compatible
  - Total lines added: ~150

File 2: team-grade-processing/api/server.py
--------
Changes:
  - Modified /ingest endpoint (lines 131-234)
  - Added queue integrity checking
  - Added decision logic for recovery enqueue
  - Added clear logging
  - No changes to other endpoints
  - All changes backward compatible
  - Lines changed: ~50

Files Created:
  - team-grade-processing/ingest/ingestion_watchdog.py (~250 lines)
  - team-grade-processing/ingest/ingestion_cleanup.py (~280 lines)

=============================================================================
7. UNCHANGED FILES
=============================================================================

The following files were NOT modified:

  ✓ ingest_worker.py - Worker logic unchanged
  ✓ youtube_downloader.py - Download logic unchanged
  ✓ youtube_metadata.py - Metadata extraction unchanged
  ✓ queue_manager.py - Queue logic unchanged
  ✓ api/server.py - Other endpoints unchanged (only /ingest modified)
  ✗ /status endpoint - Still works as-is (queries video status)
  ✗ /queue endpoint - Still works as-is (queries queue stats)
  ✓ All CLI and startup code - Unchanged
  ✓ All package imports - Unchanged

=============================================================================
8. FLOW DIAGRAMS
=============================================================================

BEFORE (Broken Flow):
---------------------
UI → POST /ingest {url}
  ↓
API validates URL
  ↓
API queries: video = get_video_status(video_id)
  ↓
IF video exists AND status != "failed":
  → Return "already_queued" ❌ BLOCKS RECOVERY
ELSE:
  → ingest_youtube_url(url) ✓
  
Result: If video is stuck in "pending", it NEVER gets re-enqueued.

AFTER (Fixed Flow):
-------------------
UI → POST /ingest {url}
  ↓
API validates URL
  ↓
API checks: integrity = check_queue_integrity(video_id)
  ↓
IF video.status == "completed":
  → Return "already_processed" ✓
ELIF video.status == "processing" AND queue_exists:
  → Return "already_queued" ✓
ELIF video.status == "pending" AND queue_exists:
  → Return "already_queued" ✓
ELIF video.status == "pending" AND NOT queue_exists:
  → ingest_youtube_url(url)  ← RECOVERY PATH ✓✓✓
  → Return "recovery_enqueue"
ELIF NOT video_exists:
  → ingest_youtube_url(url) ✓
  → Return "queued"

Result: Stuck videos are automatically recovered and re-enqueued.

WATCHDOG FLOW (Continuous Healing):
------------------------------------
python -m ingest.ingestion_watchdog
  ↓
Query all videos where status == "pending"
  ↓
For each video:
  ↓
  IF NOT queue_exists:
    → Call add_to_queue() with priority=7 (higher)
    → Log "✓ Watchdog: repaired missing queue entry for {video_id}"
  ↓
Return summary: {total_pending, total_repaired, repairs}

Result: Any videos that slip through get automatically recovered.

CLEANUP FLOW (Database Maintenance):
------------------------------------
python -m ingest.ingestion_cleanup [--dry-run]
  ↓
Query videos where status == "pending" AND age > 1 hour
Query queue entries where age > 2 hours
  ↓
For each stale entry:
  ↓
  IF dry_run:
    → Log "[DRY RUN] would delete ..."
  ELSE:
    → Delete from database
    → Log "✓ Deleted ..."
  ↓
Return summary: {stale_count, cleaned_count}

Result: Database is cleaned of stale ingestion entries.

=============================================================================
9. TESTING THE FIX
=============================================================================

Test 1: Fresh Ingest
-------------------
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=ABC123"}'

Expected logs:
  ✓ Extracted video_id: ABC123
  ✓ Queue integrity check - video_exists: False, queue_exists: False
  ✓ Fresh enqueue: video does not exist yet
  ✓ Enqueuing job for video_id: ABC123
  ✓ Successfully enqueued ABC123

Expected response:
  {
    "status": "queued",
    "video_id": "ABC123",
    "message": "Video queued for ingestion",
    "timestamp": "..."
  }

Test 2: Recovery Enqueue (stuck video)
--------------------------------------
1. Manually create video in Firestore with status="pending"
   videos/ABC123 {status: "pending", stage: "metadata", ...}

2. DO NOT create queue entry

3. Call /ingest with same URL

Expected logs:
  ✓ Extracted video_id: ABC123
  ✓ Queue integrity check - video_exists: True, queue_exists: False, status: pending
  ⚠ Recovery enqueue: pending but no queue entry exists
  ✓ Enqueuing job for video_id: ABC123
  ✓ Successfully enqueued ABC123

Expected response:
  {
    "status": "recovery_enqueue",
    "video_id": "ABC123",
    "message": "Video stuck in pending - recovery enqueue",
    "timestamp": "..."
  }

Test 3: Watchdog Repair
-----------------------
1. Create multiple pending videos without queue entries
2. Run: python -m ingest.ingestion_watchdog

Expected logs:
  ⚠ Found stuck video: VIDEO_ID1 (pending=pending, queue_exists=False)
  ✓ Watchdog: repaired missing queue entry for VIDEO_ID1
  ⚠ Found stuck video: VIDEO_ID2 (pending=pending, queue_exists=False)
  ✓ Watchdog: repaired missing queue entry for VIDEO_ID2
  
  ✓ Watchdog scan complete: found 2 pending, repaired 2

Test 4: Cleanup Script
---------------------
1. Create videos stuck in "pending" for > 1 hour
2. Run: python -m ingest.ingestion_cleanup --dry-run

Expected output:
  [DRY RUN] would delete stale_video_1
  [DRY RUN] would delete stale_video_2
  
  Dry run complete - review output above

3. Run again WITHOUT --dry-run to actually delete

=============================================================================
10. API RESPONSE STATUSES
=============================================================================

The /ingest endpoint now returns these status values:

"queued"           - Fresh ingest, video queued normally
"already_queued"   - Video already has queue entry
"already_processed" - Video already completed (not reprocessed)
"recovery_enqueue" - Video was stuck, recovered and re-enqueued
"already_processing" - Video currently processing (not re-queued)

Logging Output Patterns:

✓ Enqueuing job for <video_id>
  → Job successfully added to queue

✓ Skipping enqueue: already complete
  → Video already processed, not reprocessing

✓ Skipping enqueue: already processing
  → Video currently being processed

⚠ Recovery enqueue: pending but no queue entry exists
  → Stuck video was detected and recovered

✓ Fresh enqueue: video does not exist yet
  → New video, normal enqueue

=============================================================================
11. SUMMARY
=============================================================================

PROBLEM FIXED:
  ✓ API no longer blocks recovery enqueues for stuck videos
  ✓ Stuck videos now automatically re-enqueue
  ✓ UI no longer gets stuck at "pending/metadata"

NEW FEATURES:
  ✓ Queue integrity checker for diagnosing issues
  ✓ Self-healing watchdog for continuous recovery
  ✓ Database cleanup script for maintenance
  ✓ Detailed logging for all operations
  ✓ Dry-run mode for testing cleanup

COMPATIBILITY:
  ✓ All changes backward compatible
  ✓ No breaking changes to existing APIs
  ✓ Existing endpoints unchanged
  ✓ Worker logic unchanged
  ✓ Can be deployed without coordination

DEPLOYMENT:
  1. Deploy firestore_client.py (new methods)
  2. Deploy server.py (/ingest fix)
  3. Deploy ingestion_watchdog.py (optional, for monitoring)
  4. Deploy ingestion_cleanup.py (optional, for maintenance)
  
All files can be deployed independently without breaking changes.

=============================================================================
"""
