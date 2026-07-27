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


def _make_analysis_doc(overall_grade):
    doc = MagicMock()
    doc.to_dict.return_value = {"overall_grade": overall_grade}
    return doc


def test_single_athlete_mode_computes_overall_grade():
    """play_index is None - falls straight through to the unconditional
    completion path, which now also computes the video-level grade."""
    mock_firestore, video_ref = _mock_firestore()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.stream.return_value = [
        _make_analysis_doc(80.0), _make_analysis_doc(90.0),
    ]
    mock_queue = MagicMock()

    success, error = complete_stage.run_complete_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    args = video_ref.update.call_args[0][0]
    assert args["overall_grade"] == 85.0
    assert args["letter_grade"] == "B"


def test_last_play_computes_overall_grade_across_all_plays():
    mock_firestore, video_ref = _mock_firestore()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.stream.return_value = [
        _make_analysis_doc(60.0), _make_analysis_doc(100.0),
    ]
    mock_queue = MagicMock()

    with patch.object(complete_stage, "mark_play_status"), \
         patch.object(complete_stage, "count_incomplete_plays", return_value=0):
        success, error = complete_stage.run_complete_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    args = video_ref.update.call_args[0][0]
    assert args["overall_grade"] == 80.0
    assert args["letter_grade"] == "B"


def test_no_analysis_yet_writes_none_grade():
    mock_firestore, video_ref = _mock_firestore()
    mock_queue = MagicMock()

    success, error = complete_stage.run_complete_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    args = video_ref.update.call_args[0][0]
    assert args["overall_grade"] is None
    assert args["letter_grade"] is None


def test_first_play_completing_does_not_compute_grade_yet():
    """The no-op-for-pending-plays path must not touch the video row at
    all, so it must not compute/write a grade either."""
    mock_firestore, video_ref = _mock_firestore()
    mock_queue = MagicMock()

    with patch.object(complete_stage, "mark_play_status"), \
         patch.object(complete_stage, "count_incomplete_plays", return_value=1), \
         patch.object(complete_stage, "_compute_overall_grade") as mock_compute:
        success, error = complete_stage.run_complete_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 0}
        )

    assert success is True, error
    mock_compute.assert_not_called()
    video_ref.update.assert_not_called()
