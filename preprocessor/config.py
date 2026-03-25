"""
Tunable configuration for the pre-ingestion segmentation pipeline.

Hardware target: NVIDIA GTX 1650 (CUDA 7.5, typically 4 GB VRAM) + Intel i3-9100F.
All constants are intentionally grouped here so operators can tune without
touching algorithmic code.
"""

# ---------------------------------------------------------------------------
# Hardware / GPU
# ---------------------------------------------------------------------------
DEVICE = "cuda"           # "cuda" | "cpu"  — auto-detected at runtime
BATCH_SIZE = 4            # Conservative batch size for GTX 1650 (≤4 GB VRAM typical)
USE_FP16 = True           # FP16 quantization; falls back to FP32 on CPU
CUDA_COMPUTE = 7.5        # GTX 1650 compute capability

# Thread budget (i3-9100F: 4 cores / 4 threads, no hyperthreading)
FFMPEG_THREADS = 2        # cores reserved for FFmpeg decode / encode
UPLOAD_THREADS = 1        # core reserved for GCS upload
SCENE_DETECT_THREADS = 1  # core reserved for PySceneDetect

# ---------------------------------------------------------------------------
# Frame sampling (low-res pass — 5 % of typical workload)
# ---------------------------------------------------------------------------
SAMPLE_RESOLUTION = "480p"   # height to scale to before analysis
SAMPLE_WIDTH = 854            # width at 480p (16:9)
SAMPLE_HEIGHT = 480
SAMPLE_FPS = 4               # frames per second for motion / field analysis

# ---------------------------------------------------------------------------
# Audio analysis
# ---------------------------------------------------------------------------
AUDIO_SR = 22050              # sample rate for librosa load
AUDIO_HOP_LENGTH = 512        # STFT hop length
AUDIO_N_FFT = 2048            # FFT window size

# Whistle detection thresholds
WHISTLE_MIN_FREQ_HZ = 2000    # whistles typically 2 kHz–4 kHz
WHISTLE_MAX_FREQ_HZ = 4000
WHISTLE_RMS_PERCENTILE = 85   # energy spike threshold (percentile of RMS)
WHISTLE_MIN_DURATION_S = 0.3  # minimum whistle burst length in seconds
WHISTLE_CONFIDENCE_WEIGHT = 0.35

# ---------------------------------------------------------------------------
# Motion analysis
# ---------------------------------------------------------------------------
MOTION_DIFF_THRESHOLD = 25    # pixel-level abs-diff threshold (0-255)
MOTION_BURST_MIN_FRAMES = 3   # consecutive high-motion frames → play burst
MOTION_BURST_GAP_FRAMES = 8   # frames of low motion to split bursts
MOTION_SCORE_WEIGHT = 0.25

# ---------------------------------------------------------------------------
# Scene boundary (PySceneDetect)
# ---------------------------------------------------------------------------
SCENE_THRESHOLD = 27.0        # content-detector threshold (lower = sensitive)
SCENE_MIN_SCENE_LEN_S = 1.5   # minimum scene length in seconds
SCENE_WEIGHT = 0.20

# ---------------------------------------------------------------------------
# Field detection (HSV green mask)
# ---------------------------------------------------------------------------
FIELD_HSV_LOWER = (35, 40, 40)    # lower HSV bound for grass green
FIELD_HSV_UPPER = (85, 255, 255)  # upper HSV bound for grass green
FIELD_MIN_COVERAGE = 0.30         # fraction of frame that must be green
FIELD_WEIGHT = 0.20

# ---------------------------------------------------------------------------
# Fusion / segment candidates
# ---------------------------------------------------------------------------
FUSION_MIN_CONFIDENCE = 0.45   # discard candidates below this score
SEGMENT_PAD_S = 1.0            # seconds of padding added around each play
SEGMENT_MIN_DURATION_S = 3.0   # discard segments shorter than this
SEGMENT_MAX_DURATION_S = 60.0  # cap runaway segments

# ---------------------------------------------------------------------------
# FFmpeg cutting
# ---------------------------------------------------------------------------
FFMPEG_BIN = "ffmpeg"          # override if ffmpeg not on PATH
FFMPEG_CUT_CODEC = "copy"      # "-c copy" = lossless, no re-encode
FFMPEG_EXTRA_ARGS = ["-avoid_negative_ts", "make_zero"]

# ---------------------------------------------------------------------------
# Output / storage
# ---------------------------------------------------------------------------
OUTPUT_SEGMENT_PREFIX = "play"       # e.g. play_001.mp4
METADATA_SUFFIX = "_metadata.json"
GCS_SEGMENTS_FOLDER = "segments/"

# ---------------------------------------------------------------------------
# Firestore
# ---------------------------------------------------------------------------
FIRESTORE_PLAYS_COLLECTION = "plays"
FIRESTORE_SIGNALS_SUBCOLLECTION = "signals"
