"""
YouTube Video Downloader Module
Downloads football game videos from YouTube with error handling.
"""

import logging
import os
from typing import Optional, Union
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class YouTubeDownloader:
    """Download videos from YouTube using yt-dlp."""

    def __init__(self, output_dir: Optional[Union[str, Path]] = None):
        """
        Initialize downloader.

        Args:
            output_dir: Directory to save videos (default: current directory)
        """
        self.output_dir = Path(output_dir) if output_dir else Path("raw_videos")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized downloader with output dir: {self.output_dir}")

    def _get_output_filename(self, video_id: str) -> str:
        """
        Generate output filename for video.

        Args:
            video_id: YouTube video ID

        Returns:
            Filename for downloaded video
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"youtube_{video_id}_{timestamp}.mp4"

    def download_video(
        self,
        url: str,
        output_path: Optional[Path] = None,
        max_retries: int = 3,
        format_spec: str = None
    ) -> Optional[Path]:
        """
        Download video from YouTube URL.

        Args:
            url: YouTube URL or video ID
            output_path: Custom output path (default: auto-generated in output_dir)
            max_retries: Maximum retry attempts
            format_spec: yt-dlp format specification (default: best[ext=mp4])

        Returns:
            Path to downloaded file or None if failed
        """
        # URL validity is the caller's responsibility (download_stage.py
        # validates via YouTubeMetadataExtractor before ever reaching here) -
        # this used to re-validate with its own YouTube-only regex, which
        # would have rejected the very non-YouTube URLs the caller now
        # accepts upstream.

        # Try to import yt-dlp
        try:
            import yt_dlp
        except ImportError:
            logger.error("yt-dlp not installed. Install with: pip install yt-dlp")
            return None

        # Determine output path
        if output_path:
            output_path = Path(output_path)
        else:
            # Extract video ID
            from .youtube_metadata import YouTubeMetadataExtractor
            video_id = YouTubeMetadataExtractor.get_video_id(url)
            if not video_id:
                logger.error(f"Could not extract video ID from: {url}")
                return None

            filename = self._get_output_filename(video_id)
            output_path = self.output_dir / filename

        # Prepare output path
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Starting download from {url}")
        logger.info(f"Output path: {output_path}")

        # ✅ OPTIMIZATION: Use adaptive format if provided (Phase 2 #5)
        optimal_format = format_spec or "best[ext=mp4]"  # Default to best if not specified
        
        # Configure yt-dlp options
        ydl_opts = {
            "format": optimal_format,  # Adaptive quality or best quality MP4
            "outtmpl": str(output_path),
            "quiet": False,
            "no_warnings": False,
            "progress_hooks": [self._progress_hook],
            "socket_timeout": 30,
            "retries": max_retries,
            # Deno alone isn't enough to solve YouTube's signature/n-challenge:
            # yt-dlp 2026.07.04+ only downloads the actual challenge-solver
            # script (EJS) when explicitly allowed to fetch a remote component -
            # without this, Deno has nothing to execute and every format is
            # reported unavailable ("Signature solving failed... Ensure you
            # have a supported JavaScript runtime AND challenge solver script
            # distribution installed").
            "remote_components": ["ejs:github"],
        }

        # YouTube's bot-check ("Sign in to confirm you're not a bot") blocks
        # this server's IP even after yt-dlp's own client fallbacks - a
        # signed-in account's cookies is the mitigation. See config/settings.py.
        from config import settings
        if settings.YOUTUBE_COOKIES_FILE:
            ydl_opts["cookiefile"] = settings.YOUTUBE_COOKIES_FILE

        # Attempt download with retries
        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(f"Download attempt {attempt + 1}/{max_retries}")

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Verify file was created
                if output_path.exists():
                    file_size_mb = output_path.stat().st_size / (1024 * 1024)
                    logger.info(f"[OK] Download successful: {output_path.name} ({file_size_mb:.1f} MB)")
                    return output_path
                else:
                    logger.error(f"Download completed but file not found: {output_path}")
                    return None

            except Exception as e:
                last_error = e
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")

                # Clean up partial file if exists
                if output_path.exists():
                    try:
                        output_path.unlink()
                        logger.debug(f"Cleaned up partial file: {output_path}")
                    except Exception as cleanup_error:
                        logger.warning(f"Could not cleanup partial file: {cleanup_error}")

                # Wait before retry
                if attempt < max_retries - 1:
                    import time
                    wait_time = 5 * (attempt + 1)  # Exponential backoff
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)

        # All retries failed
        logger.error(f"[FAIL] Download failed after {max_retries} attempts: {last_error}")
        return None

    def _progress_hook(self, progress_info: dict) -> None:
        """
        Progress hook for download tracking.

        Args:
            progress_info: Progress information from yt-dlp
        """
        if progress_info["status"] == "downloading":
            downloaded = progress_info.get("downloaded_bytes", 0)
            total = progress_info.get("total_bytes", 0) or progress_info.get("total_bytes_estimate", 0)

            if total:
                percent = (downloaded / total) * 100
                speed = progress_info.get("speed", 0)

                if speed:
                    speed_mb = speed / (1024 * 1024)
                    logger.info(f"Downloading: {percent:.1f}% ({speed_mb:.1f} MB/s)")
                else:
                    logger.info(f"Downloading: {percent:.1f}%")

        elif progress_info["status"] == "finished":
            logger.info("Download finished, converting to MP4...")

    def get_video_info(self, url: str) -> Optional[dict]:
        """
        Get video information without downloading.

        Args:
            url: YouTube URL

        Returns:
            Dictionary with video info or None if failed
        """
        try:
            import yt_dlp

            logger.info(f"Fetching video info for: {url}")

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                # See the download() ydl_opts above - required for Deno to
                # actually have a challenge-solver script to run.
                "remote_components": ["ejs:github"],
            }
            from config import settings
            if settings.YOUTUBE_COOKIES_FILE:
                ydl_opts["cookiefile"] = settings.YOUTUBE_COOKIES_FILE

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                video_info = {
                    "title": info.get("title", ""),
                    "duration": info.get("duration", 0),
                    "upload_date": info.get("upload_date", ""),
                    "description": info.get("description", "")[:500],
                    "uploader": info.get("uploader", ""),
                    "view_count": info.get("view_count", 0),
                    "file_size_estimate": info.get("filesize", 0),
                }

                logger.info(f"[OK] Video info retrieved: {video_info['title']} ({video_info['duration']}s)")
                return video_info

        except Exception as e:
            logger.error(f"Failed to fetch video info: {e}")
            return None

    def list_downloads(self) -> list:
        """
        List all downloaded videos.

        Returns:
            List of downloaded video paths
        """
        videos = sorted(self.output_dir.glob("youtube_*.mp4"))
        logger.info(f"Found {len(videos)} downloaded videos")
        return videos


# Standalone functions
def download_video(
    url: str,
    output_dir: Optional[Path] = None,
    output_path: Optional[Path] = None
) -> Optional[Path]:
    """Standalone function to download YouTube video."""
    downloader = YouTubeDownloader(output_dir)
    return downloader.download_video(url, output_path)


def get_video_info(url: str) -> Optional[dict]:
    """Standalone function to get video information."""
    downloader = YouTubeDownloader()
    return downloader.get_video_info(url)
