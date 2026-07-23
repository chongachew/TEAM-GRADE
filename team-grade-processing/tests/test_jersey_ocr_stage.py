"""
Regression test for a real production bug: get_ocr_instance() in
jersey_ocr_stage.py constructed JerseyOCR(ocr_engine=..., language=...,
gpu=True), but JerseyOCR.__init__() (processing/jersey_ocr.py) only ever
accepted ocr_engine and language - no gpu kwarg exists there (GPU vs. CPU is
decided internally by _load_easyocr(), which hardcodes gpu=True on
easyocr.Reader with an automatic fallback to CPU on RuntimeError). This threw
TypeError on every single call, mapped to error_code "MODEL_LOAD_FAILED" by
run_jersey_ocr_stage's except block - deterministic, not transient, so every
retry failed identically. First surfaced for real once a production video
(kJM5Uk9DtoQ) reached this stage with real torso-crop data after the
multi-player pose fix.
"""

from unittest.mock import MagicMock, patch

from config import settings
from ingest.stages import jersey_ocr_stage


def test_get_ocr_instance_constructs_with_only_supported_kwargs(monkeypatch):
    jersey_ocr_stage._OCR_CACHE = None
    monkeypatch.setattr(settings, "OCR_ENGINE", "easyocr")
    monkeypatch.setattr(settings, "OCR_LANGUAGE", "en")

    with patch("processing.jersey_ocr.JerseyOCR") as MockJerseyOCR:
        MockJerseyOCR.return_value = MagicMock()
        instance = jersey_ocr_stage.get_ocr_instance()

    MockJerseyOCR.assert_called_once_with(ocr_engine="easyocr", language="en")
    assert instance is MockJerseyOCR.return_value


def test_get_ocr_instance_rejects_unsupported_gpu_kwarg_regression(monkeypatch):
    """Reproduces the real bug directly against the real JerseyOCR class
    (not a mock) - the actual TypeError this bug threw in production."""
    from processing.jersey_ocr import JerseyOCR

    jersey_ocr_stage._OCR_CACHE = None
    monkeypatch.setattr(settings, "OCR_ENGINE", "easyocr")
    monkeypatch.setattr(settings, "OCR_LANGUAGE", "en")

    try:
        JerseyOCR(ocr_engine="easyocr", language="en", gpu=True)
        raised = False
    except TypeError:
        raised = True

    assert raised, "JerseyOCR still doesn't accept 'gpu' - if this now passes, get_ocr_instance() no longer needs to omit it"
