"""
Unit Tests for Exception Handling

Tests the custom exception hierarchy and error code mapping.
"""

import pytest
from ingest.exceptions import (
    PipelineException,
    ValidationError,
    FileNotFoundError,
    FirestoreError,
    StageExecutionError,
    ConfigurationError,
    DependencyError,
    QueueError,
    is_retryable,
    get_error_context,
    RETRYABLE_ERRORS,
    NON_RETRYABLE_ERRORS,
)


class TestValidationError:
    """Test ValidationError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_validation_error_creation(self):
        """Test creating ValidationError."""
        error = ValidationError("Invalid input", video_id="test_id")
        assert error.message == "Invalid input"
        assert error.error_code == "VALIDATION_ERROR"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_validation_error_attributes(self):
        """Test ValidationError has required attributes."""
        error = ValidationError("Test message", video_id="test_vid")
        assert error.message == "Test message"
        assert error.error_code == "VALIDATION_ERROR"
        assert error.video_id == "test_vid"
        assert isinstance(error, PipelineException)

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_validation_error_is_not_retryable(self):
        """Test that validation errors are not retryable."""
        error = ValidationError("Invalid input")
        assert not is_retryable(error)


class TestFirestoreError:
    """Test FirestoreError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_firestore_error_creation(self):
        """Test creating FirestoreError."""
        error = FirestoreError("Connection failed", video_id="vid_123")
        assert "Connection failed" in error.message
        assert error.error_code == "FIRESTORE_ERROR"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_firestore_error_is_retryable(self):
        """Test that Firestore errors are retryable."""
        error = FirestoreError("Connection failed", video_id="vid_123")
        assert is_retryable(error)

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_firestore_error_with_context(self):
        """Test FirestoreError with additional context."""
        error = FirestoreError(
            "Connection failed",
            video_id="vid_123",
            stage="download_stage",
            operation="write"
        )
        assert error.video_id == "vid_123"
        assert error.stage == "download_stage"


class TestStageExecutionError:
    """Test StageExecutionError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_stage_execution_error_creation(self):
        """Test creating StageExecutionError."""
        error = StageExecutionError(
            stage="pose_estimation",
            reason="Model failed to load",
            video_id="vid_456"
        )
        assert "Model failed to load" in error.message
        assert error.error_code == "STAGE_EXECUTION_ERROR"
        assert error.stage == "pose_estimation"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_stage_execution_error_not_retryable_by_default(self):
        """Test stage execution error retry logic."""
        error = StageExecutionError(
            stage="pose_estimation",
            reason="Model failed",
            video_id="vid_456"
        )
        # StageExecutionError not in RETRYABLE_ERRORS currently
        assert not is_retryable(error)


class TestFileNotFoundError_Custom:
    """Test custom FileNotFoundError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_file_not_found_creation(self):
        """Test creating FileNotFoundError."""
        error = FileNotFoundError(
            file_path="/path/to/video.mp4",
            video_id="vid_789"
        )
        assert "Required file not found" in error.message
        assert error.error_code == "FILE_NOT_FOUND"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_file_not_found_is_not_retryable(self):
        """Test file not found error retry status."""
        error = FileNotFoundError(
            file_path="/path/to/missing.mp4",
            video_id="vid_789"
        )
        # File not found errors are not retryable
        assert not is_retryable(error)


class TestQueueError:
    """Test QueueError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_queue_error_creation(self):
        """Test creating QueueError."""
        error = QueueError("Enqueue failed", video_id="vid_abc")
        assert error.message == "Enqueue failed"
        assert error.error_code == "QUEUE_ERROR"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_queue_error_is_retryable(self):
        """Test that queue errors are retryable."""
        error = QueueError("Dequeue failed", video_id="vid_abc")
        assert is_retryable(error)


class TestConfigurationError:
    """Test ConfigurationError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_configuration_error_creation(self):
        """Test creating ConfigurationError."""
        error = ConfigurationError("Missing FIRESTORE_PROJECT setting")
        assert error.message == "Missing FIRESTORE_PROJECT setting"
        assert error.error_code == "CONFIGURATION_ERROR"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_configuration_error_not_retryable(self):
        """Test that configuration errors are not retryable."""
        error = ConfigurationError("Invalid credentials path")
        assert not is_retryable(error)


class TestDependencyError:
    """Test DependencyError exception."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_dependency_error_creation(self):
        """Test creating DependencyError."""
        error = DependencyError(
            dependency="FFmpeg",
            reason="Not installed or not in PATH"
        )
        assert "FFmpeg" in error.message
        assert error.error_code == "DEPENDENCY_ERROR"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_dependency_error_is_retryable(self):
        """Test that dependency errors are retryable."""
        error = DependencyError(
            dependency="Google Firestore",
            reason="Service temporarily down"
        )
        assert is_retryable(error)


class TestExceptionHierarchy:
    """Test exception inheritance and hierarchy."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_all_custom_exceptions_inherit_from_base(self):
        """Test that all custom exceptions inherit from PipelineException."""
        exceptions = [
            ValidationError("test"),
            FileNotFoundError(file_path="/test"),
            FirestoreError("test"),
            StageExecutionError(stage="test", reason="test"),
            ConfigurationError("test"),
            DependencyError(dependency="test", reason="test"),
            QueueError("test"),
        ]

        for exc in exceptions:
            assert isinstance(exc, PipelineException), f"{type(exc).__name__} not subclass of PipelineException"
            assert isinstance(exc, Exception)

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_exception_has_error_code(self):
        """Test that all exceptions have error_code attribute."""
        exceptions = [
            ValidationError("msg"),
            FileNotFoundError(file_path="file"),
            FirestoreError("msg"),
            StageExecutionError(stage="stage", reason="reason"),
            ConfigurationError("msg"),
            DependencyError(dependency="dep", reason="reason"),
            QueueError("msg"),
        ]

        for exc in exceptions:
            assert hasattr(exc, "error_code"), f"{type(exc).__name__} missing error_code"
            assert exc.error_code is not None
            assert isinstance(exc.error_code, str)

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_exception_context_attributes(self):
        """Test that exceptions support context attributes."""
        error = ValidationError("msg", video_id="vid_123", stage="test_stage")
        assert error.video_id == "vid_123"
        assert error.stage == "test_stage"


class TestRetryableErrors:
    """Test classification of retryable vs non-retryable errors."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_retryable_error_classification(self):
        """Test that FirestoreError, DependencyError, QueueError are retryable."""
        assert FirestoreError in RETRYABLE_ERRORS
        assert DependencyError in RETRYABLE_ERRORS
        assert QueueError in RETRYABLE_ERRORS

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_non_retryable_error_classification(self):
        """Test that ValidationError, ConfigurationError are non-retryable."""
        assert ValidationError in NON_RETRYABLE_ERRORS
        assert ConfigurationError in NON_RETRYABLE_ERRORS

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_is_retryable_function_works(self):
        """Test is_retryable() helper function."""
        # Retryable
        assert is_retryable(FirestoreError("test"))
        assert is_retryable(DependencyError("dep", "reason"))
        assert is_retryable(QueueError("test"))

        # Non-retryable
        assert not is_retryable(ValidationError("test"))
        assert not is_retryable(ConfigurationError("test"))


class TestErrorContext:
    """Test error context extraction."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_get_error_context_for_pipeline_exception(self):
        """Test getting context from pipeline exception."""
        error = ValidationError(
            "Invalid video ID",
            video_id="bad_id",
            stage="validation"
        )
        context = get_error_context(error)

        assert context["error_code"] == "VALIDATION_ERROR"
        assert context["message"] == "Invalid video ID"
        assert context["video_id"] == "bad_id"
        assert context["stage"] == "validation"

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_get_error_context_for_generic_exception(self):
        """Test getting context from non-pipeline exception."""
        error = ValueError("Some error")
        context = get_error_context(error)

        assert context["error_code"] == "UNKNOWN_ERROR"
        assert context["message"] == "Some error"


class TestExceptionInContext:
    """Test exceptions in typical usage patterns."""

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_raise_and_catch_validation_error(self):
        """Test raising and catching validation error."""
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError("Invalid ID", video_id="bad_id")

        error = exc_info.value
        assert error.error_code == "VALIDATION_ERROR"
        assert error.video_id == "bad_id"
        assert not is_retryable(error)

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_raise_and_catch_firestore_error(self):
        """Test raising and catching Firestore error."""
        with pytest.raises(FirestoreError) as exc_info:
            raise FirestoreError(
                "Connection timeout",
                video_id="vid_123"
            )

        error = exc_info.value
        assert error.error_code == "FIRESTORE_ERROR"
        assert is_retryable(error)

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_catch_base_pipeline_exception(self):
        """Test catching generic PipelineException."""
        with pytest.raises(PipelineException):
            raise ValidationError("Any error")

    @pytest.mark.unit
    @pytest.mark.exceptions
    def test_exception_message_preservation(self):
        """Test that exception messages are preserved."""
        original_message = "This is an important error message"
        error = ValidationError(original_message)
        assert error.message == original_message
