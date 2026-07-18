#!/usr/bin/env python3
"""
AWS Batch job entrypoint - runs exactly ONE pipeline stage for ONE video, then
exits. Invoked by ingest/batch_dispatch.py's submit_gpu_stage_job() as the
Batch job's container command override
(`python run_batch_job.py --video-id ... --stage ... --queue-doc-id ...`).

Unlike run_worker.py's always-on poll loop, this process does not dequeue -
the CPU worker already dequeued the item (flipping it to "processing") and
handed its fields directly as CLI args. This script reconstructs the same
PipelineWorker used by the CPU loop and calls its process_queue_item(), so
the item's queue lifecycle (mark_completed/mark_failed, self-enqueueing the
next stage) is finalized identically regardless of which process ran it.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(description="TEAM-GRADE AWS Batch stage runner")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--queue-doc-id", required=True)
    args = parser.parse_args()

    from ingest.ingest_pipeline_worker import PipelineWorker

    worker = PipelineWorker()
    queue_item = {
        "video_id": args.video_id,
        "stage": args.stage,
        "_doc_id": args.queue_doc_id,
    }
    # force_inline=True: this process IS the offloaded Batch job - it must
    # actually run the stage handler here, not re-submit to Batch again.
    worker.process_queue_item(queue_item, force_inline=True)


if __name__ == "__main__":
    main()
