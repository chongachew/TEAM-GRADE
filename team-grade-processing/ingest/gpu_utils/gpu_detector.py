"""
GPU Detection and Initialization Module
Safely detects GPU availability without breaking when GPU unavailable
"""

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

class GPUDetector:
    """Detect GPU availability and capabilities."""
    
    def __init__(self):
        self.detected = False
        self.device_info = {}
        self.compute_capability = None
        self._detect()
    
    def _detect(self) -> None:
        """Safely detect GPU availability."""
        try:
            import pycuda.driver as cuda
            cuda.init()
            
            # Get device count
            device_count = cuda.Device.count()
            if device_count == 0:
                logger.info("[GPU] No CUDA devices detected")
                return
            
            # Get first device info
            device = cuda.Device(0)
            device_name = device.name().decode() if isinstance(device.name(), bytes) else device.name()
            self.compute_capability = device.compute_capability()
            
            logger.info(f"[GPU] Device 0: {device_name}")
            logger.info(f"[GPU] Compute Capability: {self.compute_capability}")
            
            # Check memory
            context = device.make_context()
            free, total = cuda.mem_get_info()
            logger.info(f"[GPU] Memory: {total/(1024**3):.1f}GB total, {free/(1024**3):.1f}GB free")
            context.pop()
            
            self.detected = True
            self.device_info = {
                'name': device_name,
                'device_id': 0,
                'compute_capability': self.compute_capability,
                'memory_total_gb': total / (1024**3),
                'memory_free_gb': free / (1024**3),
            }
            
        except ImportError:
            logger.info("[GPU] PyCUDA not installed - GPU acceleration unavailable")
        except Exception as e:
            logger.warning(f"[GPU] Detection failed: {e}")
    
    def is_available(self) -> bool:
        """Check if GPU is available."""
        return self.detected
    
    def get_info(self) -> Dict:
        """Get GPU device information."""
        return self.device_info
    
    def can_fit(self, size_gb: float) -> bool:
        """Check if data of given size can fit in GPU memory."""
        if not self.detected:
            return False
        return self.device_info.get('memory_free_gb', 0) > size_gb
    
    @staticmethod
    def get_recommended_batch_size() -> int:
        """Get recommended batch size based on GPU."""
        detector = GPUDetector()
        if not detector.detected:
            return 4  # CPU batch size
        
        memory_gb = detector.device_info.get('memory_total_gb', 4)
        
        if memory_gb >= 24:
            return 128
        elif memory_gb >= 12:
            return 64
        elif memory_gb >= 6:
            return 32
        else:
            return 16


# Global instance
_gpu_detector = None

def get_gpu_detector() -> GPUDetector:
    """Get or create global GPU detector instance."""
    global _gpu_detector
    if _gpu_detector is None:
        _gpu_detector = GPUDetector()
    return _gpu_detector

def is_gpu_available() -> bool:
    """Quick check if GPU is available."""
    return get_gpu_detector().is_available()

def get_gpu_memory_gb() -> float:
    """Get available GPU memory in GB."""
    detector = get_gpu_detector()
    return detector.device_info.get('memory_free_gb', 0)
