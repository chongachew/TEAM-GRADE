"""
GPU Configuration and Environment Setup
Manages GPU settings with environment variable overrides
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class GPUConfig:
    """GPU Configuration Settings."""
    
    # GPU Control
    use_gpu: bool = True
    gpu_device_id: int = 0
    
    # Frame Extraction
    frame_extraction_gpu: bool = True
    frame_extraction_batch_size: int = 32
    
    # Pose Estimation
    pose_estimation_gpu: bool = False  # Not available until Phase 3
    pose_estimation_batch_size: int = 16
    
    # Video Download
    download_gpu_encode: bool = False  # Not available until Phase 3
    
    # Memory Management
    gpu_memory_limit_gb: Optional[float] = None
    enable_memory_pooling: bool = True
    
    # Fallback Behavior
    fallback_to_cpu: bool = True
    warn_on_fallback: bool = True
    
    @classmethod
    def load_from_env(cls) -> 'GPUConfig':
        """Load configuration from environment variables."""
        config = cls()
        
        config.use_gpu = os.getenv('TEAM_GRADE_GPU_ENABLED', 'true').lower() == 'true'
        config.gpu_device_id = int(os.getenv('TEAM_GRADE_GPU_DEVICE', '0'))
        config.frame_extraction_gpu = os.getenv('TEAM_GRADE_FRAME_GPU', 'true').lower() == 'true'
        config.frame_extraction_batch_size = int(os.getenv('TEAM_GRADE_FRAME_BATCH', '32'))
        config.fallback_to_cpu = os.getenv('TEAM_GRADE_FALLBACK_CPU', 'true').lower() == 'true'
        
        logger.info(f"[GPU Config] GPU Enabled: {config.use_gpu}")
        logger.info(f"[GPU Config] Device ID: {config.gpu_device_id}")
        logger.info(f"[GPU Config] CPU Fallback: {config.fallback_to_cpu}")
        
        return config
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'use_gpu': self.use_gpu,
            'gpu_device_id': self.gpu_device_id,
            'frame_extraction_gpu': self.frame_extraction_gpu,
            'frame_extraction_batch_size': self.frame_extraction_batch_size,
            'fallback_to_cpu': self.fallback_to_cpu,
        }

# Global instance
_gpu_config = None

def get_gpu_config() -> GPUConfig:
    """Get or create global GPU configuration."""
    global _gpu_config
    if _gpu_config is None:
        _gpu_config = GPUConfig.load_from_env()
    return _gpu_config
