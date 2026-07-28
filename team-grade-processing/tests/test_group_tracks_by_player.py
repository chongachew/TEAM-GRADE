"""
Unit tests for api/server.py's _group_tracks_by_player - groups per-play
tracked-player rows (tracks_meta) into stable cross-play "player" identities
via confident jersey-number OCR reads, so a claimed player's cards/plays/
reps aren't scattered across one entry per (track_id, play_index) just
because tracking resets track_id to 0 on every play (Phase C).
"""

import pytest

from api.server import _group_tracks_by_player


class TestGroupTracksByPlayer:
    def test_empty_input_returns_empty_list(self):
        assert _group_tracks_by_player([]) == []

    def test_confident_jersey_merges_across_plays(self):
        rows = [
            {"track_id": 0, "play_index": 2, "jersey_number": "23", "jersey_confidence": 0.9,
             "total_frames_tracked": 100},
            {"track_id": 3, "play_index": 6, "jersey_number": "23", "jersey_confidence": 0.85,
             "total_frames_tracked": 50},
        ]

        groups = _group_tracks_by_player(rows)

        assert len(groups) == 1
        assert groups[0]["player_id"] == "jersey_23"
        assert groups[0]["jersey_number"] == "23"
        assert groups[0]["total_frames_tracked"] == 150
        instances = {(i["track_id"], i["play_index"]) for i in groups[0]["instances"]}
        assert instances == {(0, 2), (3, 6)}

    def test_low_confidence_jersey_never_merges(self):
        """A weak/unreliable jersey OCR read must never guess-merge two
        different real players into one claimable identity."""
        rows = [
            {"track_id": 0, "play_index": 2, "jersey_number": "23", "jersey_confidence": 0.3,
             "total_frames_tracked": 100},
            {"track_id": 3, "play_index": 6, "jersey_number": "23", "jersey_confidence": 0.3,
             "total_frames_tracked": 50},
        ]

        groups = _group_tracks_by_player(rows)

        assert len(groups) == 2
        assert all(g["jersey_number"] is None for g in groups)

    def test_missing_jersey_number_stays_singleton(self):
        rows = [
            {"track_id": 5, "play_index": 1, "jersey_number": None, "jersey_confidence": None,
             "total_frames_tracked": 40},
        ]

        groups = _group_tracks_by_player(rows)

        assert len(groups) == 1
        assert groups[0]["jersey_number"] is None
        assert groups[0]["instances"] == [{"track_id": 5, "play_index": 1}]

    def test_different_jersey_numbers_never_merge(self):
        rows = [
            {"track_id": 0, "play_index": 2, "jersey_number": "23", "jersey_confidence": 0.9,
             "total_frames_tracked": 100},
            {"track_id": 1, "play_index": 2, "jersey_number": "45", "jersey_confidence": 0.9,
             "total_frames_tracked": 100},
        ]

        groups = _group_tracks_by_player(rows)

        assert len(groups) == 2
        assert {g["jersey_number"] for g in groups} == {"23", "45"}

    def test_same_track_id_different_play_no_jersey_are_distinct_players(self):
        """track_id=0 in play 2 and track_id=0 in play 6 are almost always
        different real players (track_id resets per play) - without a
        confident jersey number to prove otherwise, they must never merge."""
        rows = [
            {"track_id": 0, "play_index": 2, "jersey_number": None, "jersey_confidence": None,
             "total_frames_tracked": 100},
            {"track_id": 0, "play_index": 6, "jersey_number": None, "jersey_confidence": None,
             "total_frames_tracked": 50},
        ]

        groups = _group_tracks_by_player(rows)

        assert len(groups) == 2
