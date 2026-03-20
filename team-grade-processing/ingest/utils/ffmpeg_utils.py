"""
FFmpeg Utilities
Frame extraction using FFmpeg (not OpenCV or MoviePy).
Prioritizes efficiency and reliability.
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional, List
import json

from config import settings

logger = logging.getLogger(__name__)


class FFmpegFrameExtractor:
    """Extract frames from video using FFmpeg."""

    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        """Initialize frame extractor.

        Args:
            ffmpeg_path: Path to ffmpeg executable (uses settings.FFMPEG_PATH if None)
            ffprobe_path: Path to ffprobe executable (uses settings.FFPROBE_PATH if None)
        
        Raises:
            ValueError: If FFmpeg is not found or not accessible, or paths are invalid
        """
        # Get paths from settings or use system defaults (only allow settings or standard names)
        if ffmpeg_path is not None:
            # Reject custom paths to prevent command injection
            if not ffmpeg_path in ['ffmpeg', getattr(settings, 'FFMPEG_PATH', 'ffmpeg')]:
                raise ValueError(f"Custom ffmpeg_path not allowed: {ffmpeg_path}. Use settings.FFMPEG_PATH.")
        self.ffmpeg_path = ffmpeg_path or getattr(settings, 'FFMPEG_PATH', 'ffmpeg')
        
        if ffprobe_path is not None:
            # Reject custom paths to prevent command injection
            if not ffprobe_path in ['ffprobe', getattr(settings, 'FFPROBE_PATH', 'ffprobe')]:
                raise ValueError(f"Custom ffprobe_path not allowed: {ffprobe_path}. Use settings.FFPROBE_PATH.")
        self.ffprobe_path = ffprobe_path or getattr(settings, 'FFPROBE_PATH', 'ffprobe')
        
        if not self._verify_ffmpeg():
            raise ValueError(f"FFmpeg not found. Install from https://ffmpeg.org/download.html")

    def _verify_ffmpeg(self) -> bool:
        """Verify FFmpeg is installed and accessible.

        Returns:
            True if FFmpeg available
        """
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                timeout=5,
                shell=False
            )
            if result.returncode == 0:
                logger.info(f"[OK] FFmpeg found")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        logger.error(
            f"FFmpeg not found. Install from: https://ffmpeg.org/download.html"
        )
        return False

    def get_video_info(self, video_path: Path) -> Optional[dict]:
        """Get video information using FFprobe.

        Args:
            video_path: Path to video file

        Returns:
            Dict with duration, fps, width, height, or None if failed
        """
        try:
            # Sanitize video_path to prevent path traversal
            video_path = Path(video_path)
            video_id = video_path.stem  # Extract filename without extension
            try:
                safe_id = settings.sanitize_id(video_id)
            except ValueError:
                logger.error(f"Invalid video path: {video_path}")
                return None
            
            # Use ffprobe to get video info
            ffprobe_cmd = [
                self.ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration,r_frame_rate,width,height",
                "-of", "json",
                str(video_path)
            ]

            result = subprocess.run(
                ffprobe_cmd,
                capture_output=True,
                timeout=10,
                text=True,
                shell=False
            )

            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON from ffprobe: {e}")
                    return None
                    
                stream = data.get("streams", [{}])[0]

                # Parse frame rate (e.g., "30000/1001" or "30")
                fps_str = stream.get("r_frame_rate", "30")
                if "/" in fps_str:
                    num, denom = map(float, fps_str.split("/"))
                    fps = num / denom
                else:
                    fps = float(fps_str)

                return {
                    "duration_seconds": float(stream.get("duration", 0)),
                    "fps": fps,
                    "width": int(stream.get("width", 0)),
                    "height": int(stream.get("height", 0))
                }
            else:
                logger.warning(f"ffprobe failed with exit code {result.returncode}")
        except Exception as e:
            logger.warning(f"Failed to get video info: {e}")

        return None

    def extract_frames(
        self,
        video_path: Path,
        output_dir: Path,
        fps: Optional[float] = None,
        max_frames: Optional[int] = None,
        frame_format: Optional[str] = None
    ) -> List[Path]:
        """Extract frames from video using FFmpeg.

        Args:
            video_path: Path to input video
            output_dir: Directory to save frames
            fps: Frames per second to extract (uses settings.FRAME_EXTRACTION_FPS if None; must be > 0 and <= 120)
            max_frames: Maximum frames to extract (None = all; must be >= 1 if provided)
            frame_format: Output format ("jpg" or "png"; uses settings.FRAME_FORMAT if None)

        Returns:
            List of extracted frame paths
        
        Raises:
            ValueError: If fps, max_frames, or frame_format invalid
        """
        # Use settings defaults if not provided
        fps = fps if fps is not None else getattr(settings, 'FRAME_EXTRACTION_FPS', 15.0)
        frame_format = frame_format or getattr(settings, 'FRAME_FORMAT', 'jpg')
        
        # Validate fps
        if not isinstance(fps, (int, float)) or fps <= 0 or fps > 120:
            raise ValueError(f"fps must be in range (0, 120], got {fps}")
        
        # Validate frame_format
        if frame_format not in ["jpg", "jpeg", "png"]:
            raise ValueError(f"frame_format must be 'jpg' or 'png', got '{frame_format}'")
        
        # Normalize format name (jpeg -> jpg)
        if frame_format == "jpeg":
            frame_format = "jpg"
        
        # Validate max_frames
        if max_frames is not None and (not isinstance(max_frames, int) or max_frames < 1):
            raise ValueError(f"max_frames must be >= 1, got {max_frames}")
        
        output_dir.mkdir(parents=True, exist_ok=True)

        # Validate output_dir path to prevent traversal attacks
        output_dir = Path(output_dir)
        if output_dir.is_absolute():
            raise ValueError(f"Output directory must be relative, not absolute: {output_dir}")
        try:
            resolved_output = output_dir.resolve()
            resolved_output.relative_to(Path.cwd())
        except ValueError:
            raise ValueError(f"Output directory path would escape project: {output_dir}")

        # Verify input video exists and validate path to prevent traversal
        video_path = Path(video_path)
        if video_path.is_absolute():
            raise ValueError(f"Video path must be relative, not absolute: {video_path}")
        try:
            resolved_video = video_path.resolve()
            resolved_video.relative_to(Path.cwd())
        except ValueError:
            raise ValueError(f"Video path would escape project: {video_path}")
        
        if not video_path.exists():
            logger.error(f"[FRAME_EXT] INPUT VIDEO MISSING: {str(video_path)}")
            return []

        # Get video info for diagnostic logging
        video_size_mb = video_path.stat().st_size / (1024 * 1024)
        logger.info(f"[FRAME_EXT] VIDEO DETAILS: path={str(video_path)}, size={video_size_mb:.1f}MB")

        logger.info(f"[FRAME_EXT] Extracting frames at {fps} FPS")
        logger.info(f"[FRAME_EXT] OUTPUT DIRECTORY: {str(output_dir)}")

        # Build FFmpeg command
        output_pattern = str(output_dir / f"frame_%06d.{frame_format}")

        cmd = [
            self.ffmpeg_path,
            "-i", str(video_path),
            "-vf", f"fps={fps}",
            "-q:v", "2",  # Quality (1=best, 31=worst)
            output_pattern
        ]

        if max_frames:
            cmd[-3:-3] = ["-frames:v", str(max_frames)]

        try:
            # Log full command for debugging
            logger.debug(f"[FRAME_EXT] FULL COMMAND: {' '.join(cmd)}")
            logger.info(f"[FRAME_EXT] Running FFmpeg with fps={fps}, max_frames={max_frames or 'all'}")
            
            # Get timeout from settings or use default
            timeout = getattr(settings, 'EXTRACTION_TIMEOUT', 3600)

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                shell=False
            )

            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                stdout = result.stdout.decode("utf-8", errors="replace")
                
                # Log full FFmpeg output for diagnosis
                logger.error(f"[FRAME_EXT] FFmpeg FAILED with exit code {result.returncode}")
                logger.error(f"[FRAME_EXT] FFmpeg STDERR:\n{stderr}")
                if stdout:
                    logger.debug(f"[FRAME_EXT] FFmpeg STDOUT:\n{stdout}")
                
                # Check if it's a file not found error
                if "No such file or directory" in stderr or "does not exist" in stderr.lower():
                    logger.error(f"[FRAME_EXT] VIDEO FILE NOT FOUND: {str(video_path)}")
                elif "Bitstream filter" in stderr or "Unknown encoder" in stderr:
                    logger.error(f"[FRAME_EXT] FFmpeg codec issue: {stderr[:200]}")
                
                return []

            # Collect extracted frames
            frames = sorted(output_dir.glob(f"frame_*.{frame_format}"))

            logger.info(f"[FRAME_EXT] SUCCESS: Extracted {len(frames)} frames")
            if frames:
                logger.debug(f"[FRAME_EXT] Frame samples: {[f.name for f in frames[:3]]}")

            return frames

        except subprocess.TimeoutExpired:
            timeout = getattr(settings, 'EXTRACTION_TIMEOUT', 3600)
            logger.error(f"[FRAME_EXT] FFmpeg TIMED OUT after {timeout} seconds")
            return []
        except ValueError as e:
            logger.error(f"[FRAME_EXT] VALIDATION ERROR: {e}")
            return []
        except Exception as e:
            logger.error(f"[FRAME_EXT] EXCEPTION: {type(e).__name__}: {str(e)}")
            import traceback
            logger.debug(f"[FRAME_EXT] Traceback: {traceback.format_exc()}")
            return []

    def get_frame_timestamp(
        self,
        frame_index: int,
        fps: float
    ) -> float:
        """Calculate approximate timestamp for a frame.

        Args:
            frame_index: 0-based frame number
            fps: Frames per second

        Returns:
            Timestamp in seconds
        """
        return frame_index / fps if fps > 0 else 0


def extract_frames_from_video(
    video_path: Path,
    output_dir: Path,
    fps: Optional[float] = None,
    max_frames: Optional[int] = None
) -> List[Path]:
    """Helper function to extract frames.

    Args:
        video_path: Path to video
        output_dir: Output directory
        fps: Frames per second (uses settings.FRAME_EXTRACTION_FPS if None)
        max_frames: Maximum frames to extract

    Returns:
        List of frame paths
    
    Raises:
        ValueError: If FFmpeg not available or parameters invalid
    """
    try:
        extractor = FFmpegFrameExtractor()
    except ValueError as e:
        logger.error(f"Cannot initialize FFmpeg extractor: {e}")
        return []
    
    return extractor.extract_frames(
        video_path,
        output_dir,
        fps=fps,
        max_frames=max_frames
    )
