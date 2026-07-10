"""
Tests for download_stage's video_url threading fix.

The stage now reads the originally-submitted URL off the Firestore video doc
and passes *that* to yt-dlp, instead of the bare internal video_id. Previously
it passed only the video_id, which happened to work for YouTube (yt-dlp's
YouTube extractor special-cases a bare 11-char ID as if it were a full URL)
but would silently fail to download anything from Vimeo/TikTok/Drive/a direct
file URL, none of which have an equivalent bare-ID shortcut.
"""

from unittest.mock import MagicMock, patch

from ingest.stages import download_stage


class FakeDoc:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


def _mock_firestore(video_doc_data, doc_exists=True):
    mock_firestore = MagicMock()
    doc_mock = MagicMock()
    doc_mock.get.return_value = FakeDoc(video_doc_data, exists=doc_exists)
    mock_firestore.db.collection.return_value.document.return_value = doc_mock
    return mock_firestore


def test_passes_stored_video_url_to_downloader_not_bare_video_id(tmp_path):
    video_id = "dQw4w9WgXcQ"
    video_path = tmp_path / f"{video_id}.mp4"
    video_path.write_bytes(b"fake video bytes")

    mock_firestore = _mock_firestore({"video_url": "https://vimeo.com/123456789"})
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    mock_downloader = MagicMock()
    mock_downloader.download_video.return_value = video_path

    with patch("config.settings.get_videos_dir", return_value=tmp_path), \
         patch("ingest.youtube_downloader.YouTubeDownloader", return_value=mock_downloader), \
         patch("ingest.utils.bandwidth_utils.BandwidthMeasurer.measure_bandwidth", return_value=10.0), \
         patch("ingest.utils.bandwidth_utils.BandwidthMeasurer.select_format_for_video", return_value="best"):
        success, error = download_stage.run_download_stage(mock_firestore, video_id, mock_queue)

    assert success is True, error
    mock_downloader.download_video.assert_called_once()
    call_kwargs = mock_downloader.download_video.call_args.kwargs
    assert call_kwargs["url"] == "https://vimeo.com/123456789"
    assert call_kwargs["url"] != video_id


def test_youtube_url_also_uses_stored_url_not_bare_id(tmp_path):
    """Confirms the fix applies uniformly - a YouTube submission's real URL is
    now used too, not just the bare ID it happened to work with before."""
    video_id = "dQw4w9WgXcQ"
    video_path = tmp_path / f"{video_id}.mp4"
    video_path.write_bytes(b"fake video bytes")

    stored_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    mock_firestore = _mock_firestore({"video_url": stored_url})
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    mock_downloader = MagicMock()
    mock_downloader.download_video.return_value = video_path

    with patch("config.settings.get_videos_dir", return_value=tmp_path), \
         patch("ingest.youtube_downloader.YouTubeDownloader", return_value=mock_downloader), \
         patch("ingest.utils.bandwidth_utils.BandwidthMeasurer.measure_bandwidth", return_value=10.0), \
         patch("ingest.utils.bandwidth_utils.BandwidthMeasurer.select_format_for_video", return_value="best"):
        success, error = download_stage.run_download_stage(mock_firestore, video_id, mock_queue)

    assert success is True, error
    assert mock_downloader.download_video.call_args.kwargs["url"] == stored_url


def test_missing_video_url_fails_validation():
    mock_firestore = _mock_firestore({})  # video doc exists but has no video_url
    mock_queue = MagicMock()

    success, error = download_stage.run_download_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is False
    assert error == "VALIDATION_ERROR"
    mock_queue.enqueue_video.assert_not_called()


def test_missing_video_doc_fails_validation():
    mock_firestore = _mock_firestore({}, doc_exists=False)
    mock_queue = MagicMock()

    success, error = download_stage.run_download_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is False
    assert error == "VALIDATION_ERROR"
    mock_queue.enqueue_video.assert_not_called()


def test_invalid_video_id_fails_before_firestore_lookup():
    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    success, error = download_stage.run_download_stage(mock_firestore, "***", mock_queue)

    assert success is False
    assert error is not None
    mock_firestore.db.collection.assert_not_called()
