"""
GPU Utilities Module
Provides GPU detection, configuration, frame extraction, and safe fallback handling
"""

from .gpu_detector import (
    GPUDetector,
    get_gpu_detector,
    is_gpu_available,
    get_gpu_memory_gb,
)
from .gpu_config import (
    GPUConfig,
    get_gpu_config,
)
from .frame_extractor import (
    BaseFrameExtractor,
    GPUFrameExtractor,
    CPUFrameExtractor,
    FrameExtractorFactory,
)

__all__ = [
    # GPU Detection
    'GPUDetector',
    'get_gpu_detector',
    'is_gpu_available',
    'get_gpu_memory_gb',
    # GPU Configuration
    'GPUConfig',
    'get_gpu_config',
    # Frame Extraction
    'BaseFrameExtractor',
    'GPUFrameExtractor',
    'CPUFrameExtractor',
    'FrameExtractorFactory',
]
