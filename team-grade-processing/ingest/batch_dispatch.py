"""
AWS Batch Dispatch
Submits GPU-eligible pipeline stages (detection, tracking) to AWS Batch
instead of running them inline on the always-on CPU worker. See
run_batch_job.py for the job's own entrypoint, which finalizes the queue
item's status the same way ingest_pipeline_worker.py's poll loop does.
"""

import logging
from typing import Any, Dict, Optional

import boto3

from config import settings

logger = logging.getLogger(__name__)

_batch_client = None


def _get_batch_client():
    global _batch_client
    if _batch_client is None:
        _batch_client = boto3.client("batch")
    return _batch_client


def submit_gpu_stage_job(
    video_id: str,
    stage: str,
    queue_doc_id: str,
    play_index: Optional[int] = None,
) -> str:
    """Submit one pipeline stage as an AWS Batch job.

    Args:
        video_id: Video to process.
        stage: Stage name (expected: "detection" or "tracking").
        queue_doc_id: The dequeued item's queue row ID - the Batch job's own
            entrypoint (run_batch_job.py) uses this to mark the item
            completed/failed once it finishes.
        play_index: Which play this job is scoped to, or None for
            whole-video-mode - threaded through to run_batch_job.py via
            --play-index so the stage handler it runs receives it.

    Returns:
        The submitted Batch job ID.
    """
    command = ["python", "run_batch_job.py", "--video-id", video_id, "--stage", stage,
               "--queue-doc-id", queue_doc_id]
    if play_index is not None:
        command += ["--play-index", str(play_index)]

    response = _get_batch_client().submit_job(
        jobName=f"team-grade-{stage}-{video_id}"[:128],
        jobQueue=settings.AWS_BATCH_JOB_QUEUE,
        jobDefinition=settings.AWS_BATCH_JOB_DEFINITION,
        containerOverrides={"command": command},
    )
    return response["jobId"]
