"""
Tests for complete_stage.py's per-play completion guard (per-play redesign,
Phase C): biomechanics_stage_vectorized.py self-enqueues "complete" once per
play, so this stage now runs once per play for a multi-play video, not once
total. Only the LAST pending play should actually flip the video's status to
"completed" - earlier plays finishing must no-op instead of prematurely
marking the whole video done.
"""

from unittest.mock import MagicMock, patch

from ingest.stages import complete_stage


def _mock_firestore(video_exists=True):
    mock_firestore = MagicMock()
    doc_snapshot = MagicMock()
    doc_snapshot.exists = video_exists
    doc_snapshot.to_dict.return_value = {}
    video_ref = mock_firestore.db.collection.return_value.document.return_value
    video_ref.get.return_value = doc_snapshot
    return mock_firestore, video_ref


def test_whole_video_mode_completes_unconditionally():
    """play_index is None (single-athlete pipeline, or multi-player never
    ran play_detection) - existing unconditional behavior, unchanged."""
    mock_firestore, video_ref = _mock_firestore()
    mock_queue = MagicMock()

    success, error = complete_stage.run_complete_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    video_ref.update.assert_called_once()
    args = video_ref.update.call_args[0][0]
    assert args["status"] == "completed"


def test_first_of_two_plays_completing_does_not_finalize_video():
    mock_firestore, video_ref = _mock_firestore()
    mock_queue = MagicMock()

    with patch.object(complete_stage, "mark_play_status") as mock_mark_play, \
         patch.object(complete_stage, "count_incomplete_plays", return_value=1):
        success, error = complete_stage.run_complete_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 0}
        )

    assert success is True, error
    mock_mark_play.assert_called_once_with(mock_firestore, "dQw4w9WgXcQ", 0, "completed")
    # Video-level status update must NOT have happened - another play is
    # still pending.
    video_ref.update.assert_not_called()


def test_last_of_two_plays_completing_finalizes_video():
    mock_firestore, video_ref = _mock_firestore()
    mock_queue = MagicMock()

    with patch.object(complete_stage, "mark_play_status") as mock_mark_play, \
         patch.object(complete_stage, "count_incomplete_plays", return_value=0):
        success, error = complete_stage.run_complete_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    mock_mark_play.assert_called_once_with(mock_firestore, "dQw4w9WgXcQ", 1, "completed")
    # This was the last pending play - falls through to the real completion.
    video_ref.update.assert_called_once()
    args = video_ref.update.call_args[0][0]
    assert args["status"] == "completed"


def test_video_doc_not_found_fails_before_play_guard():
    mock_firestore, video_ref = _mock_firestore(video_exists=False)
    mock_queue = MagicMock()

    success, error = complete_stage.run_complete_stage(
        mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 0}
    )

    assert success is False
    assert error == "VIDEO_DOC_NOT_FOUND"
