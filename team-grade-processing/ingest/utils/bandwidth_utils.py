"""
Bandwidth Utilities for Adaptive Quality Selection
Measures network speed and recommends video quality.
"""

import logging
import time
from typing import Tuple

logger = logging.getLogger(__name__)


class BandwidthMeasurer:
    """Measures network bandwidth and recommends download quality."""
    
    @staticmethod
    def measure_bandwidth(test_url: str = None, test_size_mb: float = 5.0) -> float:
        """Measure download bandwidth by downloading test data.
        
        Args:
            test_url: URL to download from (uses small file from YouTube if None)
            test_size_mb: Size of test data to download in MB
            
        Returns:
            Download speed in MB/s (0 if measurement fails)
        """
        try:
            import requests
            
            # Use a small test file or YouTube
            test_url = test_url or "https://www.google.com/images/branding/googlelogo/1x/googlelogo_light_color_272x92dp.png"
            
            start_time = time.time()
            response = requests.get(test_url, timeout=10, stream=True)
            
            bytes_downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    bytes_downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    
                    # Stop after test_size_mb or 5 seconds
                    if bytes_downloaded >= test_size_mb * 1024 * 1024 or elapsed > 5:
                        break
            
            elapsed = time.time() - start_time
            
            if elapsed < 0.1:  # Avoid division by very small numbers
                return 0
            
            # Calculate MB/s
            speed_mbs = bytes_downloaded / (1024 * 1024) / elapsed
            logger.info(f"[BW] Measured bandwidth: {speed_mbs:.2f} MB/s (tested {bytes_downloaded / (1024*1024):.1f} MB in {elapsed:.1f}s)")
            return speed_mbs
            
        except Exception as e:
            logger.warning(f"[BW] Failed to measure bandwidth: {e}")
            return 0
    
    @staticmethod
    def get_optimal_quality(bandwidth_mbs: float) -> Tuple[str, str]:
        """Get optimal video quality based on bandwidth.
        
        Args:
            bandwidth_mbs: Download speed in MB/s
            
        Returns:
            Tuple of (format_string, quality_label)
            - format_string: yt-dlp format specification
            - quality_label: Human-readable quality label
        """
        # Quality profiles based on bandwidth
        if bandwidth_mbs >= 5:
            # Excellent: Download 1080p
            return ('bestvideo[height<=1080]+bestaudio/best[ext=mp4]', '1080p')
        elif bandwidth_mbs >= 3:
            # Good: Download 720p
            return ('bestvideo[height<=720]+bestaudio/best[ext=mp4]', '720p')
        elif bandwidth_mbs >= 1:
            # Fair: Download 480p
            return ('bestvideo[height<=480]+bestaudio/best[ext=mp4]', '480p')
        elif bandwidth_mbs > 0:
            # Poor: Download 360p
            return ('bestvideo[height<=360]+bestaudio/best[ext=mp4]', '360p')
        else:
            # Unknown: Default to best available
            logger.warning("[BW] Bandwidth measurement failed, using default quality")
            return ('best[ext=mp4]', 'auto')
    
    @staticmethod
    def select_format_for_video(bandwidth_mbs: float, video_id: str = None) -> str:
        """Select appropriate format based on bandwidth.
        
        Args:
            bandwidth_mbs: Download speed in MB/s
            video_id: Optional video ID for logging
            
        Returns:
            yt-dlp format specification string
        """
        format_spec, quality_label = BandwidthMeasurer.get_optimal_quality(bandwidth_mbs)
        
        log_context = f"video={video_id}" if video_id else ""
        logger.info(f"[BW] Selected quality: {quality_label} ({bandwidth_mbs:.2f} MB/s) {log_context}")
        
        return format_spec
