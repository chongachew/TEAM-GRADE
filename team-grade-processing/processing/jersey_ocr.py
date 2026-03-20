"""
Jersey OCR Module
Detects and reads jersey numbers from torso crops using OCR.
"""

import logging
from pathlib import Path
from typing import Dict, Tuple, Optional, List
import numpy as np

logger = logging.getLogger(__name__)


class JerseyOCR:
    """
    Extracts jersey numbers from player torso crops.
    Supports multiple OCR backends (EasyOCR, PaddleOCR, etc.)
    """

    def __init__(self, ocr_engine: str = "easyocr", language: str = "en"):
        """
        Initialize jersey OCR detector.

        Args:
            ocr_engine: OCR backend ('easyocr', 'paddle', 'tesseract')
            language: Language for OCR recognition

        Raises:
            ValueError: If OCR engine not supported
        """
        self.ocr_engine = ocr_engine
        self.language = language
        self.reader = None
        self._load_ocr_model()

    def _load_ocr_model(self) -> None:
        """
        Load OCR model based on configuration.
        
        Raises:
            ValueError: If model loading fails
        """
        try:
            if self.ocr_engine == "easyocr":
                self._load_easyocr()
            elif self.ocr_engine == "paddle":
                self._load_paddleocr()
            elif self.ocr_engine == "tesseract":
                self._load_tesseract()
            else:
                raise ValueError(f"Unsupported OCR engine: {self.ocr_engine}")
            
            logger.info(f"Loaded {self.ocr_engine} OCR model")
        except Exception as e:
            logger.error(f"Failed to load OCR model: {e}")
            raise

    def _load_easyocr(self) -> None:
        """Load EasyOCR model."""
        try:
            import easyocr
            self.reader = easyocr.Reader([self.language], gpu=True)
        except ImportError:
            logger.error("EasyOCR not installed: pip install easyocr")
            raise
        except RuntimeError:
            logger.warning("GPU not available, falling back to CPU")
            import easyocr
            self.reader = easyocr.Reader([self.language], gpu=False)

    def _load_paddleocr(self) -> None:
        """Load PaddleOCR model."""
        try:
            from paddleocr import PaddleOCR
            self.reader = PaddleOCR(use_angle_cls=True, lang=self.language)
        except ImportError:
            logger.error("PaddleOCR not installed: pip install paddleocr paddlepaddle")
            raise

    def _load_tesseract(self) -> None:
        """Load Tesseract model (placeholder)."""
        try:
            import pytesseract
            self.reader = pytesseract
        except ImportError:
            logger.error("Tesseract not installed: pip install pytesseract")
            raise

    def read_jersey_number(
        self,
        crop: np.ndarray,
        confidence_threshold: float = 0.3
    ) -> Tuple[Optional[str], float]:
        """
        Read jersey number(s) from torso crop.

        Args:
            crop: Torso crop image (BGR)
            confidence_threshold: Minimum confidence for text detection

        Returns:
            Tuple of (jersey_number, confidence)
            - jersey_number: Detected number as string (e.g., "23") or None
            - confidence: Detection confidence (0.0-1.0)
        """
        if crop is None or crop.size == 0:
            logger.warning("Invalid crop provided")
            return None, 0.0

        try:
            if self.ocr_engine == "easyocr":
                return self._read_easyocr(crop, confidence_threshold)
            elif self.ocr_engine == "paddle":
                return self._read_paddle(crop, confidence_threshold)
            else:
                logger.error(f"Read not implemented for {self.ocr_engine}")
                return None, 0.0
        except Exception as e:
            logger.error(f"Jersey OCR failed: {e}")
            return None, 0.0

    def _read_easyocr(
        self,
        crop: np.ndarray,
        confidence_threshold: float
    ) -> Tuple[Optional[str], float]:
        """
        Read jersey number using EasyOCR.

        Args:
            crop: Input crop
            confidence_threshold: Minimum confidence

        Returns:
            (jersey_number, confidence)
        """
        results = self.reader.readtext(crop)
        
        if not results:
            logger.debug("No text detected in crop")
            return None, 0.0

        # Filter results by confidence and extract numbers
        detections = []
        for (bbox, text, conf) in results:
            if conf >= confidence_threshold:
                # Extract only digits
                digits = ''.join(c for c in text.upper() if c.isdigit())
                if digits:  # Found at least one digit
                    detections.append((digits, conf))
                    logger.debug(f"Detected: '{digits}' (conf: {conf:.3f})")

        if detections:
            # Return highest confidence detection
            best = max(detections, key=lambda x: x[1])
            logger.info(f"Jersey number: {best[0]} (confidence: {best[1]:.3f})")
            return best[0], float(best[1])

        logger.debug("No valid digits detected")
        return None, 0.0

    def _read_paddle(
        self,
        crop: np.ndarray,
        confidence_threshold: float
    ) -> Tuple[Optional[str], float]:
        """
        Read jersey number using PaddleOCR.

        Args:
            crop: Input crop
            confidence_threshold: Minimum confidence

        Returns:
            (jersey_number, confidence)
        """
        results = self.reader.ocr(crop, cls=True)
        
        if not results or not results[0]:
            logger.debug("No text detected in crop")
            return None, 0.0

        detections = []
        for line in results:
            for word_info in line:
                text, conf = word_info[1], word_info[2]
                if conf >= confidence_threshold:
                    digits = ''.join(c for c in text.upper() if c.isdigit())
                    if digits:
                        detections.append((digits, conf))
                        logger.debug(f"Detected: '{digits}' (conf: {conf:.3f})")

        if detections:
            best = max(detections, key=lambda x: x[1])
            logger.info(f"Jersey number: {best[0]} (confidence: {best[1]:.3f})")
            return best[0], float(best[1])

        return None, 0.0

    def read_all_text(self, crop: np.ndarray) -> List[Dict[str, any]]:
        """
        Extract all text from crop (debugging purpose).

        Args:
            crop: Input crop

        Returns:
            List of detection dicts with text, confidence, bbox
        """
        if crop is None or crop.size == 0:
            return []

        try:
            if self.ocr_engine == "easyocr":
                results = self.reader.readtext(crop)
                return [
                    {
                        "text": text,
                        "confidence": float(conf),
                        "bbox": bbox
                    }
                    for bbox, text, conf in results
                ]
            elif self.ocr_engine == "paddle":
                results = self.reader.ocr(crop, cls=True)
                output = []
                for line in results:
                    for word_info in line:
                        bbox, (text, conf) = word_info[0], word_info[1:3]
                        output.append({
                            "text": text,
                            "confidence": float(conf),
                            "bbox": bbox
                        })
                return output
            else:
                return []
        except Exception as e:
            logger.error(f"Failed to extract all text: {e}")
            return []

    def close(self) -> None:
        """Clean up OCR resources."""
        self.reader = None
        logger.info("Jersey OCR closed")


def preprocess_jersey_crop(crop: np.ndarray) -> np.ndarray:
    """
    Preprocess torso crop for better OCR performance.
    Applies contrast enhancement and noise reduction.

    Args:
        crop: Input crop

    Returns:
        Preprocessed crop
    """
    import cv2
    
    # Convert to grayscale
    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop.copy()

    # Enhance contrast (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Denoise
    denoised = cv2.fastNlMeansDenoising(enhanced, h=10)

    # Convert back to BGR for consistency
    return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)


def filter_jersey_candidates(
    detections: List[Tuple[str, float]],
    min_length: int = 1,
    max_length: int = 3
) -> List[Tuple[str, float]]:
    """
    Filter jersey number candidates by reasonable length.

    Args:
        detections: List of (text, confidence) tuples
        min_length: Minimum jersey number digits
        max_length: Maximum jersey number digits

    Returns:
        Filtered detections
    """
    return [
        (text, conf)
        for text, conf in detections
        if min_length <= len(text) <= max_length
    ]
