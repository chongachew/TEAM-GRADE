"""
Custom Exception Hierarchy for TEAM-GRADE Pipeline
Provides domain-specific exceptions for better error handling and recovery.
"""

from typing import Optional


class PipelineException(Exception):
    """Base exception for all pipeline-related errors."""
    
    def __init__(
        self, 
        message: str, 
        video_id: Optional[str] = None,
        stage: Optional[str] = None,
        error_code: str = "PIPELINE_ERROR"
    ):
        """Initialize pipeline exception.
        
        Args:
            message: Human-readable error message
            video_id: Optional YouTube video ID for context
            stage: Optional pipeline stage name where error occurred
            error_code: Machine-readable error code for recovery logic
        """
        self.message = message
        self.video_id = video_id
        self.stage = stage
        self.error_code = error_code
        
        context = []
        if video_id:
            context.append(f"video_id={video_id}")
        if stage:
            context.append(f"stage={stage}")
        
        context_str = f" [{', '.join(context)}]" if context else ""
        super().__init__(f"{message}{context_str}")


class ValidationError(PipelineException):
    """Raised when input validation fails."""
    
    def __init__(self, message: str, video_id: Optional[str] = None, **kwargs):
        super().__init__(message, video_id=video_id, error_code="VALIDATION_ERROR", **kwargs)


class FileNotFoundError(PipelineException):
    """Raised when required file doesn't exist."""
    
    def __init__(
        self, 
        file_path: str, 
        video_id: Optional[str] = None,
        stage: Optional[str] = None,
        **kwargs
    ):
        message = f"Required file not found: {file_path}"
        super().__init__(
            message, 
            video_id=video_id, 
            stage=stage,
            error_code="FILE_NOT_FOUND",
            **kwargs
        )


class FirestoreError(PipelineException):
    """Raised when Firestore operation fails."""
    
    def __init__(
        self, 
        message: str, 
        video_id: Optional[str] = None,
        stage: Optional[str] = None,
        operation: Optional[str] = None,
        **kwargs
    ):
        full_msg = f"Firestore operation failed: {message}"
        if operation:
            full_msg += f" (operation={operation})"
        
        super().__init__(
            full_msg,
            video_id=video_id,
            stage=stage,
            error_code="FIRESTORE_ERROR",
            **kwargs
        )


class StageExecutionError(PipelineException):
    """Raised when a pipeline stage fails to execute."""
    
    def __init__(
        self, 
        stage: str, 
        reason: str,
        video_id: Optional[str] = None,
        **kwargs
    ):
        message = f"Stage execution failed: {reason}"
        super().__init__(
            message,
            video_id=video_id,
            stage=stage,
            error_code="STAGE_EXECUTION_ERROR",
            **kwargs
        )


class ConfigurationError(PipelineException):
    """Raised when configuration is invalid or missing."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, error_code="CONFIGURATION_ERROR", **kwargs)


class DependencyError(PipelineException):
    """Raised when external dependency fails."""
    
    def __init__(
        self, 
        dependency: str, 
        reason: str,
        **kwargs
    ):
        message = f"Dependency '{dependency}' error: {reason}"
        super().__init__(message, error_code="DEPENDENCY_ERROR", **kwargs)


class QueueError(PipelineException):
    """Raised when queue operation fails."""
    
    def __init__(
        self, 
        message: str,
        video_id: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message,
            video_id=video_id,
            error_code="QUEUE_ERROR",
            **kwargs
        )


# Exception mapping for recovery strategies
RETRYABLE_ERRORS = {
    FirestoreError,      # Transient network issues
    DependencyError,     # Service temporarily down
    QueueError,          # Queue temporarily unavailable
}

NON_RETRYABLE_ERRORS = {
    ValidationError,     # Invalid input won't fix itself
    ConfigurationError,  # Configuration won't fix itself
}


def is_retryable(exception: Exception) -> bool:
    """Check if exception is retryable.
    
    Args:
        exception: Exception to check
        
    Returns:
        True if exception represents a transient error that might succeed on retry
    """
    return type(exception) in RETRYABLE_ERRORS


def get_error_context(exception: Exception) -> dict:
    """Extract context from pipeline exception.
    
    Args:
        exception: Exception to extract context from
        
    Returns:
        Dict with error context for logging
    """
    if isinstance(exception, PipelineException):
        return {
            "error_code": exception.error_code,
            "video_id": exception.video_id,
            "stage": exception.stage,
            "message": exception.message,
        }
    
    return {
        "error_code": "UNKNOWN_ERROR",
        "message": str(exception),
    }
