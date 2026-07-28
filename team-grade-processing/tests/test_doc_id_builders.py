"""
Unit tests for ingest/postgres_client.py's _DOC_ID_BUILDERS (pure functions,
no DB needed) - specifically the play_index suffix added to tracks_meta/
analysis/reps doc IDs.

track_id and rep_index both reset to 0 on every play (Phase C's per-play
redesign), so a doc-id built from those alone collided across plays - e.g.
two different plays' track_id=0 rows both produced doc.id "000". This was
the direct cause of GET /api/tracks's old int(doc.id) parsing merging/
dropping distinct players (fixed separately by reading track_id from the
row data instead).
"""

from ingest.postgres_client import _DOC_ID_BUILDERS


class TestPlaySuffixDisambiguation:
    def test_tracks_meta_doc_ids_differ_across_plays(self):
        row_a = {"track_id": 0, "play_index": 2}
        row_b = {"track_id": 0, "play_index": 6}
        assert _DOC_ID_BUILDERS["tracks_meta"](row_a) != _DOC_ID_BUILDERS["tracks_meta"](row_b)

    def test_analysis_doc_ids_differ_across_plays(self):
        row_a = {"rep_index": 0, "play_index": 2}
        row_b = {"rep_index": 0, "play_index": 6}
        assert _DOC_ID_BUILDERS["analysis"](row_a) != _DOC_ID_BUILDERS["analysis"](row_b)

    def test_reps_doc_ids_differ_across_plays(self):
        row_a = {"track_id": 3, "rep_index": 0, "play_index": 2}
        row_b = {"track_id": 3, "rep_index": 0, "play_index": 6}
        assert _DOC_ID_BUILDERS["reps"](row_a) != _DOC_ID_BUILDERS["reps"](row_b)

    def test_whole_video_mode_doc_ids_unchanged_when_play_index_none(self):
        """play_index=None (pre-per-play-redesign / whole-video-mode videos)
        must keep the exact old doc-id format - no other code should need
        to change to keep addressing these rows."""
        row = {"track_id": 5, "play_index": None}
        assert _DOC_ID_BUILDERS["tracks_meta"](row) == "005"

        row = {"rep_index": 3, "play_index": None}
        assert _DOC_ID_BUILDERS["analysis"](row) == "rep_3"

        row = {"track_id": 5, "rep_index": 3, "play_index": None}
        assert _DOC_ID_BUILDERS["reps"](row) == "005_0003"
