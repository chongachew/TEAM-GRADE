#!/usr/bin/env python3
"""
AWS Batch job entrypoint - runs one pipeline stage for one or more plays of
one video, then exits. Invoked by ingest/batch_dispatch.py's
submit_gpu_stage_job() as the Batch job's container command override
(`python run_batch_job.py --video-id ... --stage ... --items '<json>'`).

Per-play redesign: a single job covers multiple plays (see
ingest_pipeline_worker.py's GPU-routing branch, which batches sibling
plays' queue rows together before submitting) so the stage's model loads
once per job, not once per play - detection_stage.get_detection_model()/
tracking_stage.get_tracker() already cache their model in a module-level
global, so simply calling process_queue_item() once per item within this
one process is enough to get that reuse for free.

Unlike run_worker.py's always-on poll loop, this process does not dequeue -
the CPU worker already dequeued/claimed every item (flipping each to
"processing") and handed their fields directly as a CLI arg. This script
reconstructs the same PipelineWorker used by the CPU loop and calls its
process_queue_item() once per item, so each item's queue lifecycle
(mark_completed/mark_failed, self-enqueueing the next stage) is finalized
identically regardless of which process ran it.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="TEAM-GRADE AWS Batch stage runner")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--items", required=True,
                         help='JSON list of {"queue_doc_id": str, "play_index": int|null}')
    args = parser.parse_args()

    items = json.loads(args.items)

    from ingest.ingest_pipeline_worker import PipelineWorker

    worker = PipelineWorker()

    for item in items:
        queue_doc_id = item["queue_doc_id"]
        queue_item = {
            "video_id": args.video_id,
            "stage": args.stage,
            "_doc_id": queue_doc_id,
            "play_index": item.get("play_index"),
        }
        try:
            # force_inline=True: this process IS the offloaded Batch job -
            # it must actually run the stage handler here, not re-submit to
            # Batch again.
            worker.process_queue_item(queue_item, force_inline=True)
        except Exception as e:
            # process_queue_item already handles the *expected*
            # stage-handler-failure path itself (mark_failed inside
            # _process_stage's error branch) - this is a safety net for a
            # truly unexpected crash, so one item's failure can't leave the
            # REST of this batch's items stuck "processing" forever with no
            # process left to finalize them.
            logger.error(f"[{args.video_id}] Unhandled error processing item {queue_doc_id}: {e}", exc_info=True)
            try:
                worker.queue_manager.mark_failed(queue_doc_id, f"Batch job item failed: {e}", retry=True)
            except Exception as mark_error:
                logger.error(f"[{args.video_id}] Also failed to mark {queue_doc_id} failed: {mark_error}")


if __name__ == "__main__":
    main()
