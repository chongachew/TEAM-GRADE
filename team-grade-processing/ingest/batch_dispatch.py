"""
AWS Batch Dispatch
Submits GPU-eligible pipeline stages (detection, tracking) to AWS Batch
instead of running them inline on the always-on CPU worker. See
run_batch_job.py for the job's own entrypoint, which finalizes the queue
item's status the same way ingest_pipeline_worker.py's poll loop does.
"""

import json
import logging
from typing import Any, Dict, List

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
    items: List[Dict[str, Any]],
) -> str:
    """Submit one pipeline stage as an AWS Batch job, covering one or more
    plays' worth of work in a single job invocation (per-play redesign -
    one model load, iterate plays, instead of one job per play).

    Args:
        video_id: Video to process.
        stage: Stage name (expected: "detection" or "tracking").
        items: One or more {"queue_doc_id": str, "play_index":
            Optional[int]} dicts - the Batch job's own entrypoint
            (run_batch_job.py) loops over these, marking each one
            completed/failed independently once it finishes. A
            whole-video-mode job (no plays) is just a single-item list
            with play_index=None.

    Returns:
        The submitted Batch job ID.
    """
    command = ["python", "run_batch_job.py", "--video-id", video_id, "--stage", stage,
               "--items", json.dumps(items)]

    response = _get_batch_client().submit_job(
        jobName=f"team-grade-{stage}-{video_id}"[:128],
        jobQueue=settings.AWS_BATCH_JOB_QUEUE,
        jobDefinition=settings.AWS_BATCH_JOB_DEFINITION,
        containerOverrides={"command": command},
    )
    return response["jobId"]
