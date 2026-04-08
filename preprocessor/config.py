"""
Configuration parameters for the TEAM-GRADE preprocessor pipeline.

Tuned for whistle detection based on academic research:
"Automated Referee Whistle Sound Detection for Extraction of Highlights from Sports Video"
"""

# ---------------------------------------------------------------------------
# Audio loading
# ---------------------------------------------------------------------------
AUDIO_SAMPLE_RATE = 22050       # Hz — balance between speed and accuracy
AUDIO_CHUNK_DURATION = 30       # seconds — process in chunks for memory efficiency
AUDIO_HOP_LENGTH = 512          # samples — frame stride (~23 ms at 22050 Hz)
AUDIO_N_FFT = 2048              # samples — FFT window for frequency resolution

# ---------------------------------------------------------------------------
# Bandpass filter (academic research: whistle sits in 2–4 kHz)
# ---------------------------------------------------------------------------
WHISTLE_BANDPASS_LOW = 2000     # Hz — lower edge of whistle band
WHISTLE_BANDPASS_HIGH = 4000    # Hz — upper edge of whistle band
WHISTLE_BANDPASS_ORDER = 5      # FIR/IIR filter order

# ---------------------------------------------------------------------------
# Short-Time Energy (STE) thresholding
# ---------------------------------------------------------------------------
WHISTLE_ENERGY_PERCENTILE = 75  # Upper-quartile threshold (academic finding)
WHISTLE_ENERGY_FLOOR = 1e-6     # Absolute floor — avoids false positives on silence

# ---------------------------------------------------------------------------
# Spectral centroid target range for a referee whistle
# ---------------------------------------------------------------------------
WHISTLE_SPECTRAL_BAND = (2500, 3500)  # Hz — centroid range for a standard whistle

# ---------------------------------------------------------------------------
# Duration constraints
# ---------------------------------------------------------------------------
WHISTLE_MIN_DURATION = 0.05     # 50 ms minimum (academic finding)
WHISTLE_MAX_DURATION = 2.0      # 2 s maximum (outlier rejection)

# ---------------------------------------------------------------------------
# Librosa onset detection
# ---------------------------------------------------------------------------
ONSET_THRESHOLD = 0.1           # Peak threshold for librosa.onset.onset_detect
ONSET_DISTANCE = max(1, int(0.3 * AUDIO_SAMPLE_RATE / AUDIO_HOP_LENGTH))
# Minimum 0.3 s between onset peaks (in frames) — based on the academic finding
# that a referee cannot physically blow two distinct whistles within 300 ms

# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------
EVENT_MERGE_GAP = 0.1           # Merge events closer than 100 ms
CONFIDENCE_THRESHOLD = 0.5      # Minimum confidence to include in output

# ---------------------------------------------------------------------------
# Signal fusion weights (must sum to 1.0 for the overall pipeline)
# ---------------------------------------------------------------------------
SIGNAL_WEIGHTS = {
    "whistle": 0.35,
    "motion": 0.25,
    "scene": 0.20,
    "field": 0.20,
}

# ---------------------------------------------------------------------------
# Confidence fusion weights inside audio_analyzer (onset + STE + spectral)
# ---------------------------------------------------------------------------
AUDIO_FUSION_WEIGHTS = {
    "onset": 0.40,
    "ste": 0.35,
    "spectral": 0.25,
}
