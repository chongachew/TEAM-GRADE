# Two build targets sharing one heavy ML dependency base - `api` (FastAPI) and
# `worker` (the queue-polling ingestion pipeline). Build with
# `docker build --target api` / `--target worker`.

FROM python:3.11-slim AS base

# ffmpeg: required by yt-dlp downloads and every ffmpeg-invoking stage
# (frame extraction fallback, clip cutting, box overlay). libgl1/libglib2.0-0/
# libsm6/libxext6: opencv-python's runtime shared-library deps on Debian slim,
# missing these produces the classic "libGL.so.1: cannot open shared object
# file" crash on first `import cv2`. libgles2/libegl1: mediapipe's pose
# landmarker graph runner links against GLESv2/EGL even on the CPU delegate -
# without these, model load fails with "libGLESv2.so.2: cannot open shared
# object file", pose stage never completes on any video.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libgles2 \
    libegl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# CPU-only torch/torchvision wheels - the default PyPI index resolves torch to
# its CUDA build, which is several GB larger and pointless on Fargate (no
# GPU). Matches the CPU wheel combination already verified working locally
# (see requirements.txt's own comment: torch==2.13.0+cpu, torchvision==0.28.0+cpu).
# Cache mount instead of --no-cache-dir: this dependency stack (torch,
# paddlepaddle, easyocr) takes 30-40 minutes to download cold - a cache mount
# means an edit to requirements.txt only re-resolves/re-downloads what
# actually changed on the next build, not the whole multi-GB set again.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# SAM2's tiny checkpoint - the sam2 pip package ships no weights of its own
# (Meta's separate release, per models/README.md). RF-DETR's own weights
# self-download on first use (cached under ~/.roboflow/models/), nothing to
# pre-fetch for those here.
RUN mkdir -p models && curl -L -o models/sam2.1_hiera_tiny.pt \
    https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt

# Deno - yt-dlp needs a JS runtime to solve YouTube's signature/n-challenge
# obfuscation on current extractions; without one, downloads fail with
# "Signature solving failed... Requested format is not available" even with
# valid cookies (cookies fix the separate bot-check block, not this). Its own
# layer, not merged into the apt-get block above, so the pip install layer
# stays cached on rebuild.
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV PATH="/usr/local/bin:${PATH}"

COPY . .

RUN chmod +x docker-entrypoint.sh
ENTRYPOINT ["./docker-entrypoint.sh"]

# ── API ─────────────────────────────────────────────────────────────────────
FROM base AS api
EXPOSE 8000
CMD ["python", "api/server.py"]

# ── Worker ──────────────────────────────────────────────────────────────────
FROM base AS worker
CMD ["python", "run_worker.py"]
