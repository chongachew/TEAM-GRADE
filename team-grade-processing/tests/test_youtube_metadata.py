"""
Unit Tests for YouTubeMetadataExtractor's URL validation / video-ID resolution

Covers the "Additional video sources" blueprint feature: YouTubeMetadataExtractor
now accepts Vimeo, TikTok, Google Drive shares, and direct video-file URLs in
addition to YouTube, while Instagram and Hudl remain deliberately unsupported.
No mocking here - these exercise the real regex/parsing logic directly.
"""

import re
import pytest

from ingest.youtube_metadata import YouTubeMetadataExtractor as Extractor

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TestValidateYoutubeUrlAcceptsSupportedSources:
    @pytest.mark.unit
    @pytest.mark.validators
    def test_accepts_valid_youtube_urls(self, valid_urls):
        for url in valid_urls:
            assert Extractor.validate_youtube_url(url) is True, url

    @pytest.mark.unit
    @pytest.mark.validators
    def test_accepts_non_youtube_sources(self, valid_non_youtube_urls):
        for url in valid_non_youtube_urls:
            assert Extractor.validate_youtube_url(url) is True, url

    @pytest.mark.unit
    @pytest.mark.validators
    @pytest.mark.parametrize("url", ["", "not a url", "https://instagram.com/reel/abc123"])
    def test_rejects_urls_from_unrecognized_sources(self, url):
        """A subset of `invalid_urls` that's invalid because the source itself
        isn't recognized at all - as opposed to `http://youtube.com`, which
        *is* a recognized YouTube host but yields no ID (see
        test_malformed_youtube_url_returns_none) - validate_youtube_url only
        gates on source, get_video_id is what actually enforces "has an ID"."""
        assert Extractor.validate_youtube_url(url) is False, url

    @pytest.mark.unit
    @pytest.mark.validators
    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/reel/Cabc123XYZ/",
        "https://instagram.com/p/Cabc123XYZ/",
        "https://www.hudl.com/video/3/123456/abc123",
    ])
    def test_rejects_instagram_and_hudl(self, url):
        """Deliberately unsupported per the blueprint: Instagram's extractor is
        flaky/high-maintenance, and Hudl has no scrapable stream at all (the
        direct-file-upload endpoint is the intended path for a Hudl export)."""
        assert Extractor.validate_youtube_url(url) is False


class TestGetVideoIdYoutubeUnchanged:
    @pytest.mark.unit
    @pytest.mark.validators
    @pytest.mark.parametrize("url,expected_id", [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=9bZkp7q19f0", "9bZkp7q19f0"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ])
    def test_extracts_real_youtube_id(self, url, expected_id):
        assert Extractor.get_video_id(url) == expected_id

    @pytest.mark.unit
    @pytest.mark.validators
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=",  # missing ID
        "http://youtube.com",  # no ID at all
    ])
    def test_malformed_youtube_url_returns_none(self, url):
        """A URL that's recognizably YouTube but has no extractable ID should
        return None, not fall through and get treated as some other source."""
        assert Extractor.get_video_id(url) is None


class TestGetVideoIdSynthesizesForOtherSources:
    @pytest.mark.unit
    @pytest.mark.validators
    def test_synthesizes_valid_11_char_id(self, valid_non_youtube_urls):
        for url in valid_non_youtube_urls:
            video_id = Extractor.get_video_id(url)
            assert video_id is not None, url
            assert VIDEO_ID_PATTERN.match(video_id), (url, video_id)

    @pytest.mark.unit
    @pytest.mark.validators
    def test_synthesized_ids_are_unique_per_call(self):
        """Re-submitting the same non-YouTube URL doesn't get recognized as a
        dupe (no content fingerprinting yet - that's the separate, larger
        "analysis receipt" feature) - each call synthesizes a fresh ID."""
        url = "https://vimeo.com/123456789"
        ids = {Extractor.get_video_id(url) for _ in range(5)}
        assert len(ids) == 5

    @pytest.mark.unit
    @pytest.mark.validators
    def test_returns_none_for_invalid_urls(self, invalid_urls):
        for url in invalid_urls:
            assert Extractor.get_video_id(url) is None, url
