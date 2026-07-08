"""
Unit Tests for Validators

Tests VideoIdValidator and URLValidator classes with comprehensive edge cases.
"""

import pytest
from ingest.validation import VideoIdValidator, URLValidator
from ingest.exceptions import ValidationError


class TestVideoIdValidator:
    """Test VideoIdValidator class."""

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_valid_ids(self, valid_video_ids):
        """Test validation of valid YouTube video IDs."""
        for video_id in valid_video_ids:
            result = VideoIdValidator.validate(video_id)
            assert result == video_id, f"Expected {video_id}, got {result}"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_invalid_ids(self, invalid_video_ids):
        """Test that invalid IDs raise ValidationError."""
        for video_id in invalid_video_ids:
            with pytest.raises(ValidationError) as exc_info:
                VideoIdValidator.validate(video_id)
            
            # Verify error code is set
            assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_too_short(self):
        """Test IDs shorter than 11 characters."""
        with pytest.raises(ValidationError) as exc_info:
            VideoIdValidator.validate("short")
        assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_too_long(self):
        """Test IDs longer than 11 characters."""
        with pytest.raises(ValidationError) as exc_info:
            VideoIdValidator.validate("dQw4w9WgXcQXXXXXXXX")
        assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_empty_string(self):
        """Test empty string validation."""
        with pytest.raises(ValidationError):
            VideoIdValidator.validate("")

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_special_characters(self):
        """Test IDs with special characters."""
        invalid_ids = ["dQw4w9Wg@cQ", "dQw4w9Wg#cQ", "dQw4w9Wg$cQ", "dQw4w9Wg%cQ"]
        for video_id in invalid_ids:
            with pytest.raises(ValidationError):
                VideoIdValidator.validate(video_id)

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_spaces(self):
        """Test IDs with spaces."""
        with pytest.raises(ValidationError):
            VideoIdValidator.validate("dQw4w9Wg cQ")

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_case_sensitivity(self):
        """Test that validator accepts both upper and lowercase."""
        # Valid: YouTube IDs can contain both cases
        result = VideoIdValidator.validate("dQw4w9WgXcQ")
        assert result == "dQw4w9WgXcQ"
        
        # Another valid mixed case ID with exactly 11 characters
        result = VideoIdValidator.validate("AaBbCcDdEfG")
        assert result == "AaBbCcDdEfG"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_validate_returns_sanitized_id(self):
        """Test that validator returns the sanitized ID."""
        video_id = "dQw4w9WgXcQ"
        result = VideoIdValidator.validate(video_id)
        assert result == video_id
        assert isinstance(result, str)


class TestURLValidator:
    """Test URLValidator class."""

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_from_youtube_com_watch(self):
        """Test extracting ID from youtube.com/watch?v= URLs."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        result = URLValidator.extract_video_id(url)
        assert result == "dQw4w9WgXcQ"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_from_youtu_be(self):
        """Test extracting ID from youtu.be/ short URLs."""
        url = "https://youtu.be/dQw4w9WgXcQ"
        result = URLValidator.extract_video_id(url)
        assert result == "dQw4w9WgXcQ"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_from_direct_id(self):
        """Test passing direct video ID (no URL)."""
        video_id = "dQw4w9WgXcQ"
        result = URLValidator.extract_video_id(video_id)
        assert result == video_id

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_with_timestamp(self):
        """Test extracting ID from URL with timestamp."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"
        result = URLValidator.extract_video_id(url)
        assert result == "dQw4w9WgXcQ"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_with_playlist(self):
        """Test extracting ID from URL with playlist parameter."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxx"
        result = URLValidator.extract_video_id(url)
        assert result == "dQw4w9WgXcQ"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_invalid_url(self):
        """Test that invalid URLs raise ValidationError."""
        invalid_urls = [
            "https://vimeo.com/123456",
            "https://example.com",
            "not a url",
        ]
        for url in invalid_urls:
            with pytest.raises(ValidationError) as exc_info:
                URLValidator.extract_video_id(url)
            assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_empty_string(self):
        """Test extracting from empty string."""
        with pytest.raises(ValidationError):
            URLValidator.extract_video_id("")

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_youtube_com_without_v_param(self):
        """Test YouTube URL without video ID parameter."""
        url = "https://www.youtube.com"
        with pytest.raises(ValidationError):
            URLValidator.extract_video_id(url)

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_youtu_be_without_id(self):
        """Test youtu.be URL without ID."""
        url = "https://youtu.be/"
        with pytest.raises(ValidationError):
            URLValidator.extract_video_id(url)

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_url_formats(self, valid_urls):
        """Test various valid URL formats."""
        for url in valid_urls:
            result = URLValidator.extract_video_id(url)
            assert result is not None
            assert len(result) > 0

    @pytest.mark.unit
    @pytest.mark.validators
    def test_extract_case_insensitive(self):
        """Test that extraction works with lowercase URLs."""
        url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        url2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Same URL
        
        result1 = URLValidator.extract_video_id(url1)
        result2 = URLValidator.extract_video_id(url2)
        assert result1 == result2 == "dQw4w9WgXcQ"


class TestValidatorIntegration:
    """Test validators working together."""

    @pytest.mark.integration
    @pytest.mark.validators
    def test_url_extraction_then_validation(self, valid_urls):
        """Test extracting ID from URL and validating it."""
        for url in valid_urls:
            # Extract ID
            extracted_id = URLValidator.extract_video_id(url)
            
            # Validate extracted ID
            validated_id = VideoIdValidator.validate(extracted_id)
            
            assert extracted_id == validated_id

    @pytest.mark.integration
    @pytest.mark.validators
    def test_full_request_flow(self):
        """Test complete validation flow from URL to safe ID."""
        # Simulate API request with URL
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        
        # Step 1: Extract from URL
        video_id = URLValidator.extract_video_id(url)
        assert video_id == "dQw4w9WgXcQ"
        
        # Step 2: Validate ID format
        safe_id = VideoIdValidator.validate(video_id)
        assert safe_id == "dQw4w9WgXcQ"
        
        # Step 3: Use safe ID for database operations
        assert len(safe_id) == 11
        assert safe_id.replace("-", "").replace("_", "").isalnum()

    @pytest.mark.unit
    @pytest.mark.validators
    def test_error_code_propagation(self):
        """Test that error codes are properly set."""
        # Invalid URL
        with pytest.raises(ValidationError) as exc_info:
            URLValidator.extract_video_id("invalid")
        assert exc_info.value.error_code == "VALIDATION_ERROR"
        
        # Invalid ID
        with pytest.raises(ValidationError) as exc_info:
            VideoIdValidator.validate("invalid")
        assert exc_info.value.error_code == "VALIDATION_ERROR"
