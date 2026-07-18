"""
Tests for ingest/batch_dispatch.py - the AWS Batch submission helper used to
offload the detection/tracking stages (Phase 3, GPU compute path).
"""

from unittest.mock import MagicMock

from ingest import batch_dispatch


def test_submit_gpu_stage_job_builds_expected_batch_call(monkeypatch):
    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"jobId": "batch-job-123"}
    monkeypatch.setattr(batch_dispatch, "_get_batch_client", lambda: mock_client)
    monkeypatch.setattr(batch_dispatch.settings, "AWS_BATCH_JOB_QUEUE", "test-queue")
    monkeypatch.setattr(batch_dispatch.settings, "AWS_BATCH_JOB_DEFINITION", "test-job-def")

    job_id = batch_dispatch.submit_gpu_stage_job("vid123", "detection", "queue-doc-1")

    assert job_id == "batch-job-123"
    mock_client.submit_job.assert_called_once()
    call_kwargs = mock_client.submit_job.call_args.kwargs
    assert call_kwargs["jobQueue"] == "test-queue"
    assert call_kwargs["jobDefinition"] == "test-job-def"
    command = call_kwargs["containerOverrides"]["command"]
    assert command == [
        "python", "run_batch_job.py",
        "--video-id", "vid123",
        "--stage", "detection",
        "--queue-doc-id", "queue-doc-1",
    ]


def test_submit_gpu_stage_job_job_name_stays_within_batch_length_limit(monkeypatch):
    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"jobId": "job-1"}
    monkeypatch.setattr(batch_dispatch, "_get_batch_client", lambda: mock_client)

    long_video_id = "v" * 200
    batch_dispatch.submit_gpu_stage_job(long_video_id, "tracking", "doc-1")

    job_name = mock_client.submit_job.call_args.kwargs["jobName"]
    assert len(job_name) <= 128
