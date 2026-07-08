# TEAM-GRADE

A video-analysis pipeline that turns raw football game film into per-player biomechanics
and trait scores — submit a YouTube URL, and a queue-based worker downloads the video,
runs pose estimation and jersey OCR, segments it into individual reps, and scores each one.

## How it works

```
                POST /api/ingest
                       │
                       ▼
   FastAPI (api/server.py)  ──writes──▶  Firestore (videos/, ingestion_queue/)
                                                │
                                                ▼
                              Worker (ingest_pipeline_worker.py)
                              polls the queue and runs one stage per video per pass:

   metadata → download → frame_extraction → pose → torso_crop →
   jersey_ocr → rep_extraction → biomechanics → complete

                                                │
                                                ▼
                GET /api/ingest/{id}/status  ◀──reads── Firestore
                GET /api/analysis/{id}
                       │
                       ▼
              React frontend (team-grade-frontend/)
              upload form → live progress bar → results
```

Each stage is retried independently (exponential backoff, 3 attempts) before failing the
video, so a transient error in one stage doesn't restart the whole pipeline.

## Notable engineering

- **Measured performance work, not guesswork.** The frame-extraction, pose, and
  biomechanics stages were each profiled and optimized independently — GPU-accelerated
  frame extraction with automatic CPU fallback when no CUDA device is present, a
  lightweight pose model swap, and vectorized (NumPy) biomechanics scoring instead of
  a per-frame Python loop. Combined, these took per-video processing from ~103s to ~72s
  (see `team-grade-processing/BENCHMARK_RESULTS.txt` for the underlying numbers).
- **Self-healing ingestion.** A video can end up "pending" with no corresponding queue
  entry (e.g. the API call succeeded but the queue write raced or failed). The `/api/ingest`
  endpoint detects this on repeat submission and re-enqueues automatically instead of
  leaving the video stuck forever.
- **Diagnosed and fixed live, not just in theory**: while building the frontend for this
  repo, I ran the pipeline end-to-end against the real Firestore project and caught two
  concrete bugs that only show up under real conditions — a stage-name mismatch between
  the primary ingestion path and the rest of the pipeline (silently orphaning that stage's
  status forever), and a queue entry that retried forever with no dead-letter path once
  its underlying video document was gone.

## Repo layout

```
team-grade-processing/   # FastAPI backend + ingestion worker (the real backend)
  api/server.py           # all live endpoints (health, ingest, status, analysis, queue)
  ingest/                 # queue manager, Firestore client, worker orchestration
  ingest/stages/          # the 9 pipeline stages + GPU/lightweight variants
  processing/             # pose estimation, torso cropping, OCR, trait scoring
  tests/                  # pytest suite (mocked Firestore, no live-service dependency)
team-grade-frontend/     # React app: upload a video, watch it process, see results
node-api/                # Firestore connectivity smoke-test script (not part of the live app)
Analysis/trench_engine/  # standalone trait-scoring engine + its own demo (main.py)
Docs/, Firestore/        # architecture notes and Firestore schema reference
```

## Running it locally

Requires a Firestore service-account key at `credentials/service-account.json`
(gitignored — not included in this repo) and `ffmpeg` on your PATH for video download/merge.

```bash
# Backend (from repo root — these scripts cd into team-grade-processing/ themselves)
pip install -r requirements.txt
python start_api.py      # API on :8000
python start_worker.py   # separate process — actually processes the queue

# Frontend
cd team-grade-frontend
npm install
npm start                 # :3000, calls the API via REACT_APP_API_BASE (defaults to :8000)
```

## Testing

```bash
cd team-grade-processing
pytest tests/ -v
```

The suite mocks Firestore/YouTube/worker singletons rather than hitting the live project,
so it's safe to run without credentials and won't touch production data.

## Known limitations

- The richer analysis surface (per-rep breakdown, summary statistics, aggregated trait
  metrics across an endpoint hierarchy) was designed but never wired into the live API —
  today `GET /api/analysis/{id}` returns the raw stored analysis dict for a video. Building
  out `/reps`, `/summary`, `/metrics` as real endpoints is the natural next step.
- GPU acceleration is optional (falls back to CPU automatically) but `ffmpeg` is a hard
  requirement for the download stage.
