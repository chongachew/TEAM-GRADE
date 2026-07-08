"""
Input Validation Module for TEAM-GRADE Pipeline
Centralized validation logic for common inputs and payloads.
Reduces duplication across stage handlers and API routes.
"""

import re
from typing import Optional, Dict, Any, List
from pathlib import Path

from ingest.exceptions import ValidationError


class VideoIdValidator:
    """Validates and sanitizes YouTube video IDs.
    
    YouTube video IDs are 11 characters, alphanumeric plus underscore and hyphen.
    """
    
    PATTERN = re.compile(r'^[A-Za-z0-9_-]{11}$')
    
    @classmethod
    def validate(cls, video_id: str) -> str:
        """Validate and return sanitized video ID.
        
        Args:
            video_id: YouTube video ID to validate
            
        Returns:
            Sanitized video ID
            
        Raises:
            ValidationError: If video_id is invalid
            
        Example:
            >>> VideoIdValidator.validate("dQw4w9WgXcQ")
            'dQw4w9WgXcQ'
            >>> VideoIdValidator.validate("invalid") # Raises ValidationError
        """
        if not video_id or not isinstance(video_id, str):
            raise ValidationError(f"video_id must be non-empty string, got {type(video_id).__name__}")
        
        video_id = video_id.strip()
        
        if not cls.PATTERN.match(video_id):
            raise ValidationError(
                f"Invalid video_id format: '{video_id}'. "
                f"Expected 11 alphanumeric characters (with - or _). "
                f"Got {len(video_id)} characters.",
                video_id=video_id
            )
        
        return video_id


class URLValidator:
    """Validates YouTube URLs and extracts video IDs."""
    
    # Patterns for different YouTube URL formats
    PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([A-Za-z0-9_-]{11})',
        r'^([A-Za-z0-9_-]{11})$',  # Direct video ID
    ]
    
    @classmethod
    def extract_video_id(cls, url: str) -> str:
        """Extract video ID from YouTube URL.
        
        Args:
            url: YouTube URL or video ID
            
        Returns:
            Extracted video ID
            
        Raises:
            ValidationError: If URL is invalid or video ID cannot be extracted
            
        Example:
            >>> URLValidator.extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ")
            'dQw4w9WgXcQ'
        """
        if not url or not isinstance(url, str):
            raise ValidationError(f"URL must be non-empty string, got {type(url).__name__}")
        
        url = url.strip()
        
        for pattern in cls.PATTERNS:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                # Validate extracted ID
                return VideoIdValidator.validate(video_id)
        
        raise ValidationError(
            f"No valid YouTube URL or video ID found in: {url}",
            video_id=url if len(url) <= 20 else f"{url[:20]}..."
        )


class StagePayloadValidator:
    """Validates payloads for specific pipeline stages."""
    
    @staticmethod
    def validate_frame_extraction(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate frame extraction stage payload.
        
        Args:
            payload: Raw payload dict
            
        Returns:
            Normalized payload with validated fields
            
        Raises:
            ValidationError: If payload structure is invalid
        """
        if payload is None:
            return {}
        
        if not isinstance(payload, dict):
            raise ValidationError(f"Frame extraction payload must be dict, got {type(payload)}")
        
        normalized = {}
        
        # Optional: fps override
        if "fps" in payload:
            fps = payload["fps"]
            if not isinstance(fps, (int, float)) or fps <= 0 or fps > 60:
                raise ValidationError(
                    f"Frame extraction fps must be positive number <= 60, got {fps}"
                )
            normalized["fps"] = float(fps)
        
        # Optional: format override
        if "format" in payload:
            fmt = payload["format"]
            if fmt not in ("jpg", "png"):
                raise ValidationError(f"Frame format must be 'jpg' or 'png', got {fmt}")
            normalized["format"] = fmt
        
        return normalized
    
    @staticmethod
    def validate_pose_estimation(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate pose estimation stage payload.
        
        Args:
            payload: Raw payload dict
            
        Returns:
            Normalized payload
            
        Raises:
            ValidationError: If payload structure is invalid
        """
        if payload is None:
            return {}
        
        if not isinstance(payload, dict):
            raise ValidationError(f"Pose estimation payload must be dict, got {type(payload)}")
        
        normalized = {}
        
        # Optional: confidence threshold override
        if "confidence_threshold" in payload:
            threshold = payload["confidence_threshold"]
            if not isinstance(threshold, (int, float)) or not (0 <= threshold <= 1):
                raise ValidationError(
                    f"Confidence threshold must be 0-1, got {threshold}"
                )
            normalized["confidence_threshold"] = float(threshold)
        
        return normalized
    
    @staticmethod
    def validate_jersey_ocr(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate jersey OCR stage payload.
        
        Args:
            payload: Raw payload dict
            
        Returns:
            Normalized payload
            
        Raises:
            ValidationError: If payload is invalid
        """
        if payload is None:
            return {}
        
        if not isinstance(payload, dict):
            raise ValidationError(f"Jersey OCR payload must be dict, got {type(payload)}")
        
        normalized = {}
        
        # Optional: confidence threshold override
        if "min_confidence" in payload:
            conf = payload["min_confidence"]
            if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
                raise ValidationError(f"Jersey OCR min_confidence must be 0-1, got {conf}")
            normalized["min_confidence"] = float(conf)
        
        return normalized


class PathValidator:
    """Validates file paths for security and existence."""
    
    @staticmethod
    def validate_safe_path(
        path: str,
        base_dir: Path,
        must_exist: bool = False
    ) -> Path:
        """Validate that path is safe (no traversal) and resolve it.
        
        Args:
            path: Path string to validate
            base_dir: Base directory (paths must be within this)
            must_exist: If True, path must exist
            
        Returns:
            Resolved Path object
            
        Raises:
            ValidationError: If path is unsafe or (if must_exist=True) doesn't exist
        """
        if not path or not isinstance(path, str):
            raise ValidationError(f"Path must be non-empty string")
        
        try:
            resolved = (base_dir / path).resolve()
        except Exception as e:
            raise ValidationError(f"Cannot resolve path: {e}")
        
        # Ensure path is within base_dir (prevent traversal)
        try:
            resolved.relative_to(base_dir)
        except ValueError:
            raise ValidationError(
                f"Path traversal detected. Path must be within {base_dir}"
            )
        
        if must_exist and not resolved.exists():
            raise ValidationError(f"Path does not exist: {resolved}")
        
        return resolved


class HTTPValidator:
    """Validates HTTP-related inputs."""
    
    @staticmethod
    def validate_url(url: str) -> str:
        """Validate HTTP/HTTPS URL format.
        
        Args:
            url: URL to validate
            
        Returns:
            Validated URL
            
        Raises:
            ValidationError: If URL is invalid
        """
        if not url or not isinstance(url, str):
            raise ValidationError(f"URL must be non-empty string")
        
        url = url.strip()
        
        if not url.startswith(('http://', 'https://')):
            raise ValidationError(f"URL must start with http:// or https://")
        
        if len(url) > 2048:  # Maximum URL length
            raise ValidationError(f"URL too long (max 2048 chars)")
        
        return url


# Convenience function for common validation pattern
def validate_video_id(video_id: str) -> str:
    """Quick validation wrapper for video IDs.
    
    Args:
        video_id: Video ID to validate
        
    Returns:
        Validated video ID
        
    Raises:
        ValidationError: If invalid
    """
    return VideoIdValidator.validate(video_id)
