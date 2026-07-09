# team-grade-processing

The FastAPI backend and ingestion worker — the real, running part of TEAM-GRADE.
See the [root README](../README.md) for the overall architecture; this covers what's
actually in this directory.

## Layout

```
api/server.py           # every live endpoint: /api/health, /api/ingest,
                         # /api/ingest/{id}/status, /api/analysis/{id}, /api/queue
ingest/
  firestore_client.py    # Firestore reads/writes, queue integrity checks, self-healing
  queue_manager.py        # enqueue/dequeue, retry/backoff bookkeeping
  ingest_worker.py         # POST /api/ingest's primary ingestion path
  ingest_pipeline_worker.py # the long-running worker: polls the queue, dispatches stages
  stages/                  # the pipeline stages (see below)
  tracking/                # SAM2 density-aware memory tracker (used by stages/tracking_stage.py)
  gpu_utils/                # CUDA detection with automatic CPU fallback
models/                   # gitignored model checkpoints (SAM2 weights - see models/README.md)
config/
  settings.py              # paths, Firestore project/collection names, tunables
  constants.py              # canonical stage-name constants (STAGE_POSE, etc. — use these,
                             # not string literals, when touching stage-related code)
processing/                # standalone processing modules (pose, torso crop, jersey OCR,
                            # rep extraction, biomechanics) — importable independently of
                            # the Firestore-backed pipeline, see examples.py
tests/                      # pytest suite, Firestore/worker singletons mocked
```

## Pipeline stages

`metadata → download → frame_extraction → [motion_compensation → detection → tracking] → pose → torso_crop → jersey_ocr → rep_extraction → biomechanics → complete`

`frame_extraction`, `pose`, and `biomechanics` each have a performance-optimized variant
(`frame_extraction_stage_gpu.py`, `pose_stage_lite.py`, `biomechanics_stage_vectorized.py`)
that's what actually runs today — see `BENCHMARK_RESULTS.txt` for the measured per-stage
improvements.

The bracketed stages (`motion_compensation_stage.py`, `detection_stage.py`,
`tracking_stage.py`) only run when `settings.MULTI_PLAYER_TRACKING_ENABLED` is true
(default off — `frame_extraction` enqueues straight to `pose` otherwise, same as before
this pivot). When on, they cancel camera motion, detect every player per frame (RF-DETR),
and assign each a persistent `track_id` (SAM2 memory tracker, `ingest/tracking/`) so
`rep_extraction`/`biomechanics` can score players independently instead of assuming one
athlete. See the root README's "How it works" section for the fuller explanation.

## Running it

```bash
pip install -r ../requirements.txt
python ../start_api.py       # API on :8000
python ../start_worker.py    # separate process — dequeues and actually processes videos
```

Needs a Firestore service-account key at `../credentials/service-account.json` (gitignored)
and `ffmpeg` on PATH for the download stage.

## Testing

```bash
pytest tests/ -v
```

Every test mocks the `firestore_client`/`queue_manager`/`ingest_worker`/`metadata_extractor`
singletons in `api.server` (see `tests/conftest.py`'s `client` fixture) — none of them touch
the real Firestore project.
