"""
Shared Device Resolution for Torch-Based Models
Used by the detection (RF-DETR) and tracking (SAM2) stages so both follow the same
two-layer GPU-gating convention already established by frame_extractor.py: gate on
this repo's PyCUDA-based presence check first, then independently confirm torch's
own CUDA availability right before actually placing a model on "cuda".
"""

import logging

from ingest.gpu_utils.gpu_detector import is_gpu_available

logger = logging.getLogger(__name__)


def resolve_device(use_gpu_flag: bool) -> str:
    """Resolve the torch device string for a new model load.

    Args:
        use_gpu_flag: The stage's own settings flag (e.g. settings.DETECTION_USE_GPU)
            requesting GPU use. If False, always returns "cpu" without probing.

    Returns:
        "cuda" if use_gpu_flag is True AND both GPU-presence checks pass, else "cpu".
    """
    if not use_gpu_flag:
        return "cpu"

    if not is_gpu_available():
        logger.info("[model_device] GPU requested but not detected (PyCUDA check) - using CPU")
        return "cpu"

    try:
        import torch
        if not torch.cuda.is_available():
            logger.info("[model_device] GPU requested but torch.cuda.is_available() is False - using CPU")
            return "cpu"
    except ImportError:
        logger.info("[model_device] GPU requested but torch not installed - using CPU")
        return "cpu"

    return "cuda"
