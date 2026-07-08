"""
Resilient Frame Extraction with Consecutive Failure Tolerance
Gracefully handles corrupted/missing frames in video streams.

Pattern from: leducminh11224188/Sport-Comparison
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional, Generator, Dict, Any, List
import json

logger = logging.getLogger(__name__)


class ResilientFrameExtractor:
    """Extract frames with tolerance for consecutive failures.
    
    Key Features:
    - Tracks consecutive failures separately from total failures
    - Resets counter on success
    - Stops only after threshold consecutive failures (not total)
    - Yields frame info for real-time processing
    """
    
    def __init__(
        self,
        video_path: Path,
        fps: int = 15,
        consecutive_failure_threshold: int = 10,
        ffmpeg_path: str = "ffmpeg"
    ):
        """Initialize extractor.
        
        Args:
            video_path: Path to video file
            fps: Frames per second to extract
            consecutive_failure_threshold: Max consecutive failures before stopping
                (not total failures - resets on success)
            ffmpeg_path: Path to ffmpeg executable
            
        Raises:
            ValueError: If video_path doesn't exist or fps invalid
        """
        self.video_path = Path(video_path)
        if not self.video_path.exists():
            raise ValueError(f"Video file not found: {video_path}")
        
        if fps <= 0 or fps > 60:
            raise ValueError(f"fps must be 1-60, got {fps}")
        
        self.fps = fps
        self.consecutive_failure_threshold = consecutive_failure_threshold
        self.ffmpeg_path = ffmpeg_path
        self.consecutive_failures = 0
        self.total_failures = 0
        self.total_success = 0
    
    def extract_frames(
        self,
        output_dir: Path,
        video_id: str
    ) -> Generator[Dict[str, Any], None, None]:
        """Extract frames with resilient error handling.
        
        Strategy:
        - Use ffmpeg to extract all frames
        - Track which frames were successfully written by ffmpeg
        - Emit frame info only for successfully written files
        - Stop after consecutive_failure_threshold consecutive missing frames
        
        Args:
            output_dir: Directory to save frames (created if missing)
            video_id: Video identifier for logging
            
        Yields:
            Dict with keys:
            - frame_index: 0-based frame number
            - timestamp_ms: Timestamp in milliseconds
            - path: Absolute path to frame file
            - status: "success" or "skipped"
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(
            f"[RESILIENT_FE][{video_id}] Extracting frames from {self.video_path.name} "
            f"at {self.fps} fps to {output_dir}"
        )
        
        # Run ffmpeg to extract frames
        output_pattern = output_dir / "frame_%06d.jpg"
        cmd = [
            self.ffmpeg_path,
            "-i", str(self.video_path),
            "-vf", f"fps={self.fps}",
            "-q:v", "2",  # Quality 2 (best)
            "-hide_banner", "-loglevel", "error",
            str(output_pattern)
        ]
        
        try:
            # Run ffmpeg extraction
            logger.debug(f"[RESILIENT_FE][{video_id}] Running: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                error_msg = f"FFmpeg failed: {stderr}"
                logger.error(f"[RESILIENT_FE][{video_id}] {error_msg}")
                raise RuntimeError(error_msg)
            
            # Now enumerate extracted frames
            self.consecutive_failures = 0
            frame_index = 0
            
            for frame_path in sorted(output_dir.glob("frame_*.jpg")):
                try:
                    # ✅ Success: frame exists
                    timestamp_ms = int((frame_index) * (1000 / self.fps))
                    
                    self.consecutive_failures = 0  # RESET on success
                    self.total_success += 1
                    
                    yield {
                        "frame_index": frame_index,
                        "timestamp_ms": timestamp_ms,
                        "path": str(frame_path),
                        "status": "success"
                    }
                    
                    frame_index += 1
                    
                except Exception as e:
                    # ❌ Failure: increment counter
                    self.consecutive_failures += 1
                    self.total_failures += 1
                    
                    logger.warning(
                        f"[RESILIENT_FE][{video_id}] Frame processing failed "
                        f"(consecutive: {self.consecutive_failures}/{self.consecutive_failure_threshold}): {e}"
                    )
                    
                    if self.consecutive_failures >= self.consecutive_failure_threshold:
                        logger.error(
                            f"[RESILIENT_FE][{video_id}] Exceeded max consecutive failures, stopping"
                        )
                        break
            
            logger.info(
                f"[RESILIENT_FE][{video_id}] Extraction complete: "
                f"{self.total_success} success, {self.total_failures} skipped, "
                f"max consecutive: {self.consecutive_failure_threshold}"
            )
            
        except Exception as e:
            logger.error(f"[RESILIENT_FE][{video_id}] Extraction failed: {e}")
            raise
    
    def get_video_info(self, ffprobe_path: str = "ffprobe") -> Optional[Dict[str, Any]]:
        """Get video info (duration, fps, resolution).
        
        Args:
            ffprobe_path: Path to ffprobe executable
            
        Returns:
            Dict with duration, fps, width, height or None if failed
        """
        try:
            cmd = [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration,r_frame_rate,width,height",
                "-of", "json",
                str(self.video_path)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10,
                text=True
            )
            
            if result.returncode != 0:
                logger.warning(f"ffprobe failed: {result.stderr}")
                return None
            
            data = json.loads(result.stdout)
            stream = data.get("streams", [{}])[0]
            
            # Parse frame rate (e.g., "30000/1001" or "30")
            fps_str = stream.get("r_frame_rate", "30")
            if "/" in fps_str:
                num, denom = map(float, fps_str.split("/"))
                fps = num / denom
            else:
                fps = float(fps_str)
            
            duration = float(stream.get("duration", 0))
            
            return {
                "duration_seconds": duration,
                "fps": fps,
                "width": int(stream.get("width", 0)),
                "height": int(stream.get("height", 0)),
                "total_frames": int(duration * fps)
            }
            
        except Exception as e:
            logger.error(f"Failed to get video info: {e}")
            return None
    
    def reset_stats(self):
        """Reset failure counters."""
        self.consecutive_failures = 0
        self.total_failures = 0
        self.total_success = 0
