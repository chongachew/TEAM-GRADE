"""
Structured Logging Utilities
Provides ASCII-only, structured logging for the ingestion pipeline.
"""

import logging
import sys
from typing import Optional
from pathlib import Path

from config import settings


class StructuredLogger:
    """Provides structured ASCII logging with consistent formatting."""

    STAGE_PREFIX = {
        "metadata": "[METADATA]",
        "download": "[DOWNLOAD]",
        "frame_extraction": "[FRAME_EXT]",
        "pose": "[POSE]",
        "torso_crop": "[TORSO]",
        "jersey_ocr": "[OCR]",
        "rep_extraction": "[REPS]",
        "biomechanics": "[BIOMECH]",
        "complete": "[COMPLETE]",
        "error": "[ERROR]",
        "done": "[DONE]",
    }

    def __init__(self, name: str):
        """Initialize structured logger.

        Args:
            name: Logger name
        """
        self.logger = logging.getLogger(name)

    def _format_message(
        self,
        stage: str,
        video_id: str,
        message: str,
        context: Optional[dict] = None
    ) -> str:
        """Format structured log message.

        Args:
            stage: Pipeline stage name (must be in STAGE_PREFIX keys)
            video_id: YouTube video ID (will be sanitized)
            message: Main log message
            context: Optional context dict with extra fields (keys must be non-empty strings)

        Returns:
            Formatted log message (ASCII-safe)
        
        Raises:
            ValueError: If stage not in STAGE_PREFIX or video_id invalid
        """
        # Validate and get stage prefix
        if stage not in self.STAGE_PREFIX:
            raise ValueError(f"Unknown stage: {stage}. Must be one of {list(self.STAGE_PREFIX.keys())}")
        prefix = self.STAGE_PREFIX[stage]
        
        # Sanitize and validate video_id
        try:
            safe_video_id = settings.sanitize_id(video_id)
        except ValueError as e:
            raise ValueError(f"Invalid video_id: {e}")
        
        context_str = f"[video_id={safe_video_id}]"

        # Sanitize message to prevent newline injection
        sanitized_message = message.replace('\n', ' ').replace('\r', ' ') if message else ""

        if context:
            for key, value in context.items():
                # Validate context key is non-empty string with safe characters only
                if not isinstance(key, str) or not key:
                    continue
                
                # Restrict keys to alphanumeric and underscore (prevent injection)
                if not key.replace('_', '').isalnum():
                    continue
                
                # Safely encode any value to ASCII-safe string
                try:
                    val_str = str(value)[:100]  # Limit to 100 chars
                    # Remove non-ASCII characters
                    val_str = val_str.encode("ascii", "replace").decode("ascii")
                    context_str += f"[{key}={val_str}]"
                except Exception:
                    pass

        return f"{prefix}{context_str} {sanitized_message}"

    def info(
        self,
        stage: str,
        video_id: str,
        message: str,
        context: Optional[dict] = None
    ) -> None:
        """Log info-level message.

        Args:
            stage: Pipeline stage name (must be in STAGE_PREFIX keys)
            video_id: YouTube video ID (will be sanitized)
            message: Log message
            context: Optional context dict
        """
        try:
            formatted = self._format_message(stage, video_id, message, context)
            self.logger.info(formatted)
        except ValueError as e:
            self.logger.error(f"Logging error: {e}")

    def warning(
        self,
        stage: str,
        video_id: str,
        message: str,
        context: Optional[dict] = None
    ) -> None:
        """Log warning-level message."""
        try:
            formatted = self._format_message(stage, video_id, message, context)
            self.logger.warning(formatted)
        except ValueError as e:
            self.logger.error(f"Logging error: {e}")

    def error(
        self,
        stage: str,
        video_id: str,
        message: str,
        context: Optional[dict] = None
    ) -> None:
        """Log error-level message."""
        try:
            formatted = self._format_message(stage, video_id, message, context)
            self.logger.error(formatted)
        except ValueError as e:
            self.logger.error(f"Logging error: {e}")

    def debug(
        self,
        stage: str,
        video_id: str,
        message: str,
        context: Optional[dict] = None
    ) -> None:
        """Log debug-level message."""
        try:
            formatted = self._format_message(stage, video_id, message, context)
            self.logger.debug(formatted)
        except ValueError as e:
            self.logger.error(f"Logging error: {e}")


def setup_logging(name: str, log_file: Optional[str] = None, console_level: int = logging.INFO, file_level: int = logging.DEBUG) -> logging.Logger:
    """Setup logging for the application.

    Args:
        name: Logger name
        log_file: Optional log file path (validates parent directory exists and is writable)
        console_level: Console handler log level (default: logging.INFO)
        file_level: File handler log level (default: logging.DEBUG)

    Returns:
        Configured logger instance
    
    Raises:
        ValueError: If log_file parent directory invalid
    """
    import os
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Remove existing handlers using proper method
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        try:
            # Validate log file path is relative and within safe bounds
            log_path = Path(log_file)
            
            # Reject absolute paths to prevent writing outside project
            if log_path.is_absolute():
                raise ValueError(f"Log file path must be relative, not absolute: {log_file}")
            
            # Resolve to prevent traversal attacks (e.g., "../../etc/passwd")
            try:
                resolved_path = log_path.resolve()
                # Verify resolved path is below current working directory
                resolved_path.relative_to(Path.cwd())
            except ValueError:
                raise ValueError(f"Log file path would escape project directory: {log_file}")
            
            log_dir = log_path.parent
            # Ensure parent directory exists
            if not log_dir.exists():
                raise ValueError(f"Log directory does not exist: {log_dir}")
            
            # Check parent directory is writable
            if not os.access(str(log_dir), os.W_OK):
                raise ValueError(f"Log directory not writable: {log_dir}")
            
            # Get encoding from settings or use UTF-8 default
            file_encoding = getattr(settings, 'LOG_FILE_ENCODING', 'utf-8')
            
            file_handler = logging.FileHandler(str(log_path), encoding=file_encoding)
            file_handler.setLevel(file_level)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except ValueError as e:
            logger.error(f"Failed to setup file logging: {e}")
            raise
        except Exception as e:
            logger.warning(f"Failed to setup file logging: {e}")

    return logger
