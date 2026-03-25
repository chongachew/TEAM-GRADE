# TEAM-GRADE
AI Game footage grader for college football scouting

# Bridge Athletics — Film Ingestion & Analysis Pipeline

This repository contains the full ingestion and analysis system for Bridge Athletics:
- YouTube → Firestore → Local Worker → Normalized Film
- Player detection, tracking, pose estimation
- Scouting trait extraction based on coaching manuals
- Competition-tier classification using offer data + film features

## Folder Structure

```
bridge-athletics/
│
├── ingestion/              # Download + normalization worker
│   ├── local_worker.py
│   ├── firestore_utils.py
│   ├── raw/
│   └── normalized/
│
├── analysis/               # ML + feature extraction
│   ├── trait_dictionary.json
│   ├── feature_extraction/
│   ├── competition_model/
│   └── utils/
│
├── firestore/              # Firestore schema + examples
│   ├── schema.md
│   └── examples/
│
└── docs/                   # Architecture + philosophy
    ├── architecture.md
    ├── pipeline_overview.md
    └── scouting_philosophy.md
```

## Overview

This system ingests high school football games from YouTube, normalizes them, extracts player traits, and predicts competition level using a combination of:
- Computer vision
- Tracking
- Pose estimation
- Offer data
- Scouting trait dictionaries

## Status

Early development — ingestion pipeline first, analysis pipeline next.

---

## Pre-Ingestion Segmentation Pipeline

A **non-breaking, modular** upstream preprocessor that segments full-game
recordings into individual play clips before the existing analysis pipeline
ever runs.

### Architecture

```
preprocessor/
├── config.py            # Tunable thresholds + GTX 1650 settings
├── audio_analyzer.py    # Librosa RMS + spectral centroid whistle detection
├── motion_analyzer.py   # Frame differencing (480p, 4 fps)
├── scene_analyzer.py    # PySceneDetect ContentDetector (fast mode)
├── field_detector.py    # HSV green mask — validates field presence
├── segmenter_fusion.py  # Confidence-scored segment candidates
├── ffmpeg_cutter.py     # Lossless -c copy segment extraction
├── segment_metadata.py  # JSON sidecar + Firestore enqueue
├── async_pipeline.py    # CPU-GPU pipelining (GTX 1650 optimised)
├── cli.py               # Local testing entry point
├── worker_service.py    # Always-running daemon
└── requirements.txt

functions/
├── main.py              # Cloud Function HTTP wrapper
└── requirements.txt

tests/
├── test_audio_analyzer.py
├── test_motion_analyzer.py
├── test_field_detector.py
├── test_segmenter_fusion.py
└── fixtures/
```

### Hardware Constraints & Solutions

| Constraint | Value | Solution |
|---|---|---|
| GPU | GTX 1650 / 4 GB VRAM (typical) / CUDA 7.5 | `BATCH_SIZE=4`, FP16, no model > 1 GB |
| CPU | i3-9100F / 4 cores / no HT | 2 threads FFmpeg, 1 upload, 1 scene detect |
| Storage | 118 GB total | 5-10 GB temp per game; delete raw after segmenting |
| Frame load | Typical pipeline @ 30 fps full-res | 480p @ 4 fps = **5 %** of workload |

### Quick Start

```bash
# 1. Install dependencies
pip install -r preprocessor/requirements.txt

# 2. Run against a local file (dry-run = detect only, no FFmpeg cuts)
python preprocessor/cli.py \
    --video /data/games/game.mp4 \
    --output_dir /data/segments/ \
    --dry_run

# 3. Run against a GCS file and enqueue to Firestore
python preprocessor/cli.py \
    --video gs://bucket/game.mp4 \
    --output_dir gs://bucket/segments/ \
    --game_id abc123 \
    --use_firestore
```

### CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--video` | required | Input video path (local or `gs://`) |
| `--output_dir` | required | Output directory (local or `gs://`) |
| `--game_id` | video filename stem | Game ID for Firestore + file naming |
| `--use_firestore` | off | Enqueue segments to Firestore |
| `--dry_run` | off | Detect only, skip FFmpeg cuts |
| `--skip_audio` | off | Skip whistle detection |
| `--skip_scene` | off | Skip PySceneDetect |
| `--log_level` | INFO | DEBUG / INFO / WARNING / ERROR |

### Worker Daemon

The worker polls Firestore for games with `status == "ready-for-segmentation"`:

```bash
# Set environment variables (optional overrides)
export POLL_INTERVAL_S=15
export SEGMENTS_BASE_DIR=/data/segments
export USE_FIRESTORE=true

python -m preprocessor.worker_service
```

Firestore status lifecycle (segmenter adds two new states):

```
pending → approved → downloading → downloaded → normalizing →
ready-for-analysis → ready-for-segmentation →
segmenting → segmentation-complete → analysis-complete
```

### Output Format

**Segment file**: `play_001.mp4` (lossless `-c copy`)

**Sidecar JSON** (`play_001_metadata.json`):
```json
{
  "game_id": "abc123",
  "segment_id": "play_001",
  "segment_path": "gs://bucket/segments/play_001.mp4",
  "start_time": 12.5,
  "end_time": 35.0,
  "duration": 22.5,
  "whistle_confidence": 0.82,
  "motion_score": 0.71,
  "scene_score": 1.0,
  "field_presence": 0.65,
  "fusion_confidence": 0.74,
  "status": "pending",
  "created_at": "2026-03-25T22:00:00Z"
}
```

**Firestore path**: `plays/{game_id}/segments/{segment_id}`

### Configuration Tuning (`preprocessor/config.py`)

| Parameter | Default | Description |
|---|---|---|
| `BATCH_SIZE` | 4 | GPU batch size (GTX 1650 safe) |
| `USE_FP16` | True | FP16 quantization |
| `SAMPLE_FPS` | 4 | Frames/s for motion/field analysis |
| `SAMPLE_HEIGHT` | 480 | Low-res frame height |
| `WHISTLE_MIN_FREQ_HZ` | 2000 | Whistle band lower bound (Hz) |
| `WHISTLE_MAX_FREQ_HZ` | 4000 | Whistle band upper bound (Hz) |
| `WHISTLE_RMS_PERCENTILE` | 85 | Energy spike threshold percentile |
| `MOTION_DIFF_THRESHOLD` | 25 | Abs-diff threshold for motion burst |
| `SCENE_THRESHOLD` | 27.0 | PySceneDetect content threshold |
| `FIELD_MIN_COVERAGE` | 0.30 | Min green-pixel fraction for field |
| `FUSION_MIN_CONFIDENCE` | 0.45 | Min fusion score to keep segment |
| `SEGMENT_PAD_S` | 1.0 | Padding around each play (seconds) |

**Increase recall** (more segments, lower precision):
- Lower `FUSION_MIN_CONFIDENCE` → e.g. `0.30`
- Lower `WHISTLE_RMS_PERCENTILE` → e.g. `75`
- Lower `MOTION_DIFF_THRESHOLD` → e.g. `15`

**Increase precision** (fewer false positives):
- Raise `FUSION_MIN_CONFIDENCE` → e.g. `0.60`
- Raise `FIELD_MIN_COVERAGE` → e.g. `0.40`

### Running Unit Tests

```bash
# No GPU, no real video required
pip install pytest librosa soundfile opencv-python numpy
pytest tests/ -v
```

### Integration (Non-Breaking)

The segmenter is a **standalone upstream step** that does not modify any
existing code:

- ✅ No changes to `Ingestion/local_worker.py`
- ✅ No changes to `Ingestion/firestore_utils.py`
- ✅ No changes to any analysis modules
- ✅ Outputs to new `segments/` folder + Firestore `plays/` collection
- ✅ Existing pipeline continues unchanged