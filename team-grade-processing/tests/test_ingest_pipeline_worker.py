"""
Tests for ingest_pipeline_worker.py's process_queue_item() - the per-item
dispatch logic extracted from run_worker()'s poll loop so run_batch_job.py
(the AWS Batch job entrypoint, Phase 3) can share it.

Covers the actual behavior change this refactor introduces: detection/
tracking items get offloaded to AWS Batch instead of run inline when
GPU_BATCH_ENABLED is true, and the item is deliberately left unfinalized
(no mark_completed/mark_failed) for the Batch job itself to close out -
everything else must behave exactly as it did before the refactor.
"""

from unittest.mock import MagicMock

import pytest

from ingest.ingest_pipeline_worker import PipelineWorker
from config import settings


def _bare_worker():
    """A PipelineWorker with mocked internals, skipping __init__'s real
    Postgres connection and model preloading."""
    worker = object.__new__(PipelineWorker)
    worker.firestore = MagicMock()
    worker.queue_manager = MagicMock()
    worker.max_retries = 3
    worker.retry_backoff = 1.0
    return worker


@pytest.fixture(autouse=True)
def _gpu_batch_disabled_by_default(monkeypatch):
    """Match production's default - most tests shouldn't need to think about
    Batch routing at all unless they're specifically testing it."""
    monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", False)


class TestMalformedItems:
    def test_missing_required_field_does_not_raise(self):
        worker = _bare_worker()
        worker.process_queue_item({"video_id": "v1", "stage": "pose"})  # no _doc_id
        worker.queue_manager.mark_completed.assert_not_called()
        worker.queue_manager.mark_failed.assert_not_called()

    def test_non_string_video_id_does_not_raise(self):
        worker = _bare_worker()
        worker.process_queue_item({"video_id": 123, "stage": "pose", "_doc_id": "d1"})
        worker.queue_manager.mark_completed.assert_not_called()
        worker.queue_manager.mark_failed.assert_not_called()


class TestNonGpuStagesUnaffected:
    def test_normal_stage_processes_inline_and_marks_completed(self, monkeypatch):
        worker = _bare_worker()
        monkeypatch.setattr(worker, "_process_stage", lambda *a, **k: (True, None))

        worker.process_queue_item({"video_id": "v1", "stage": "pose", "_doc_id": "d1"})

        worker.queue_manager.mark_completed.assert_called_once_with("d1")
        worker.queue_manager.mark_failed.assert_not_called()

    def test_normal_stage_failure_marks_failed_with_retry(self, monkeypatch):
        worker = _bare_worker()
        monkeypatch.setattr(worker, "_process_stage", lambda *a, **k: (False, "boom"))

        worker.process_queue_item({"video_id": "v1", "stage": "pose", "_doc_id": "d1"})

        worker.queue_manager.mark_failed.assert_called_once_with("d1", "boom", retry=True)

    def test_detection_stage_processes_inline_when_gpu_batch_disabled(self, monkeypatch):
        worker = _bare_worker()
        monkeypatch.setattr(worker, "_process_stage", lambda *a, **k: (True, None))

        worker.process_queue_item({"video_id": "v1", "stage": "detection", "_doc_id": "d1"})

        worker.queue_manager.mark_completed.assert_called_once_with("d1")


class TestGpuBatchRouting:
    def test_detection_stage_submits_to_batch_instead_of_running_inline(self, monkeypatch):
        worker = _bare_worker()
        monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", True)
        process_stage_mock = MagicMock()
        monkeypatch.setattr(worker, "_process_stage", process_stage_mock)
        submit_mock = MagicMock(return_value="batch-job-1")
        monkeypatch.setattr(
            "ingest.ingest_pipeline_worker.submit_gpu_stage_job", submit_mock
        )

        worker.process_queue_item({"video_id": "v1", "stage": "detection", "_doc_id": "d1"})

        # No play_index on this item (whole-video-mode) - a single-item
        # batch, no sibling-claiming attempted.
        submit_mock.assert_called_once_with(
            "v1", "detection", [{"queue_doc_id": "d1", "play_index": None}]
        )
        process_stage_mock.assert_not_called()
        worker.queue_manager.claim_sibling_plays.assert_not_called()

    def test_play_scoped_item_claims_and_batches_sibling_plays(self, monkeypatch):
        # queue_doc_id must be a stringified int here (real production
        # shape, per queue_manager.dequeue_next_video's `str(result["id"])`)
        # since claim_sibling_plays' exclude_id needs a real int to compare
        # against the ingestion_queue.id integer column.
        worker = _bare_worker()
        monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", True)
        monkeypatch.setattr(settings, "GPU_BATCH_MAX_PLAYS_PER_JOB", 6)
        submit_mock = MagicMock(return_value="batch-job-1")
        monkeypatch.setattr("ingest.ingest_pipeline_worker.submit_gpu_stage_job", submit_mock)
        worker.queue_manager.claim_sibling_plays.return_value = [
            {"_doc_id": "2", "play_index": 1},
            {"_doc_id": "3", "play_index": 2},
        ]

        worker.process_queue_item({"video_id": "v1", "stage": "detection", "_doc_id": "1", "play_index": 0})

        worker.queue_manager.claim_sibling_plays.assert_called_once_with(
            "v1", "detection", exclude_id=1, max_extra=5
        )
        submit_mock.assert_called_once_with(
            "v1", "detection",
            [
                {"queue_doc_id": "1", "play_index": 0},
                {"queue_doc_id": "2", "play_index": 1},
                {"queue_doc_id": "3", "play_index": 2},
            ],
        )

    def test_failed_batch_submission_marks_every_claimed_item_failed(self, monkeypatch):
        """Not just the originally-dequeued item - every sibling play
        claimed into this same batch, or those rows would sit "processing"
        forever with no job left to finalize them."""
        worker = _bare_worker()
        monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", True)
        monkeypatch.setattr(
            "ingest.ingest_pipeline_worker.submit_gpu_stage_job",
            MagicMock(side_effect=RuntimeError("batch unavailable")),
        )
        worker.queue_manager.claim_sibling_plays.return_value = [
            {"_doc_id": "2", "play_index": 1},
        ]

        worker.process_queue_item({"video_id": "v1", "stage": "detection", "_doc_id": "1", "play_index": 0})

        assert worker.queue_manager.mark_failed.call_count == 2
        failed_ids = {call.args[0] for call in worker.queue_manager.mark_failed.call_args_list}
        assert failed_ids == {"1", "2"}
        for call in worker.queue_manager.mark_failed.call_args_list:
            assert call.kwargs.get("retry") is True

    def test_batch_item_is_left_unfinalized_for_the_batch_job_to_close_out(self, monkeypatch):
        worker = _bare_worker()
        monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", True)
        monkeypatch.setattr(
            "ingest.ingest_pipeline_worker.submit_gpu_stage_job",
            MagicMock(return_value="batch-job-1"),
        )

        worker.process_queue_item({"video_id": "v1", "stage": "tracking", "_doc_id": "d1"})

        worker.queue_manager.mark_completed.assert_not_called()
        worker.queue_manager.mark_failed.assert_not_called()

    def test_failed_batch_submission_marks_item_failed_with_retry(self, monkeypatch):
        worker = _bare_worker()
        monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", True)
        monkeypatch.setattr(
            "ingest.ingest_pipeline_worker.submit_gpu_stage_job",
            MagicMock(side_effect=RuntimeError("batch unavailable")),
        )

        worker.process_queue_item({"video_id": "v1", "stage": "detection", "_doc_id": "d1"})

        worker.queue_manager.mark_failed.assert_called_once()
        assert worker.queue_manager.mark_failed.call_args.args[0] == "d1"
        assert worker.queue_manager.mark_failed.call_args.kwargs.get("retry") is True

    def test_force_inline_bypasses_batch_even_when_enabled(self, monkeypatch):
        """This is exactly the case run_batch_job.py relies on: it IS the
        offloaded job, so it must run the stage here, not resubmit to Batch."""
        worker = _bare_worker()
        monkeypatch.setattr(settings, "GPU_BATCH_ENABLED", True)
        monkeypatch.setattr(worker, "_process_stage", lambda *a, **k: (True, None))
        submit_mock = MagicMock()
        monkeypatch.setattr(
            "ingest.ingest_pipeline_worker.submit_gpu_stage_job", submit_mock
        )

        worker.process_queue_item(
            {"video_id": "v1", "stage": "detection", "_doc_id": "d1"},
            force_inline=True,
        )

        submit_mock.assert_not_called()
        worker.queue_manager.mark_completed.assert_called_once_with("d1")
