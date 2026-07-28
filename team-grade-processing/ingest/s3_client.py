"""
S3 Media Storage Client (Pass 2b of the data-layer migration)

`team-grade-api` and `team-grade-worker` run as two separate ECS services with
separate local disks. Historically, the download/frame-extraction/torso-crop
stages ran only in the worker container and wrote only to its local disk -
but several API-side routes (clip-cut, /media playback, thumbnail) read from
the very same local-path helpers (`config.settings.get_video_path()` etc.)
expecting those files to already be there. On the API container they never
are - it never ran the worker's stages. This module makes S3
(`the-bridge-app-team-grade-media`) the real shared storage between the two
services: a durable write target after each stage produces local output, and
a transparent fallback source when a local read site's file happens to be
missing (the first time an API-side route or a resumed/relocated worker needs
it).

This module deliberately does NOT change what `settings.get_video_path()` /
`get_frames_dir()` / `get_torso_crops_dir()` return - cv2/ffmpeg need real
local paths, so every function here is additive sync *around* those local
paths, never a replacement for local processing.

Key scheme (mirrors the local directory layout under those settings helpers):
    videos/{video_id}/raw.mp4
    videos/{video_id}/frames/{filename}   (filename e.g. "frame_000123.jpg",
                                            same as ingest/gpu_utils/frame_extractor.py's
                                            local naming - see frame_key())
    videos/{video_id}/torso/{filename}    (filename e.g. "torso_000123.jpg" or
                                            "torso_000123_007.jpg", same as
                                            ingest/stages/torso_crop_stage.py's
                                            local naming - see torso_key())
"""

import concurrent.futures
import logging
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings

logger = logging.getLogger(__name__)

# Same env-var convention as the rest of this Pass 2 migration (DATABASE_URL
# etc.): read once, with a default matching the bucket that already exists
# and is already wired into both ECS task defs from Phase 1.
S3_MEDIA_BUCKET = os.environ.get("S3_MEDIA_BUCKET", "the-bridge-app-team-grade-media")

# Bounded parallelism for upload_directory/download_directory - a video can
# have hundreds to thousands of frames, but we don't want to open unbounded
# concurrent connections to S3.
_MAX_WORKERS = 8

_s3_client = None


def _get_s3_client():
    """Lazy singleton boto3 S3 client (avoids constructing one at import
    time, so simply importing this module never touches network/credentials).
    A dedicated function (rather than inlining `boto3.client("s3")` at every
    call site) also gives tests one single seam to monkeypatch.
    """
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


class S3ObjectNotFoundError(Exception):
    """Raised by the ensure_*_local() helpers when the requested object/
    directory doesn't exist locally AND couldn't be found in S3 either -
    a clear, specific failure instead of letting callers crash on cv2/ffmpeg
    hitting a missing-file OSError.
    """


# ============================================================================
# Key scheme
# ============================================================================

def video_key(video_id: str) -> str:
    return f"videos/{video_id}/raw.mp4"


def frames_prefix(video_id: str) -> str:
    return f"videos/{video_id}/frames"


def frame_key(video_id: str, filename: str) -> str:
    return f"{frames_prefix(video_id)}/{filename}"


def torso_prefix(video_id: str) -> str:
    return f"videos/{video_id}/torso"


def torso_key(video_id: str, filename: str) -> str:
    return f"{torso_prefix(video_id)}/{filename}"


def play_clip_key(video_id: str, play_index: int) -> str:
    return f"videos/{video_id}/plays/{play_index:03d}.mp4"


# ============================================================================
# Core S3 operations
# ============================================================================

def upload_file(local_path: Path, s3_key: str) -> None:
    """Upload one local file to `s3_key` in the shared media bucket.

    Raises on failure (network error, missing local file, permissions, ...) -
    callers that must not let an upload failure fail their own operation
    (every stage/route in this codebase that calls this) are responsible for
    wrapping it in their own non-fatal try/except, matching this codebase's
    existing "soft-flag, never fail the stage" idiom (see e.g. the
    authenticity-check hook in frame_extraction_stage_gpu.py).
    """
    local_path = Path(local_path)
    _get_s3_client().upload_file(str(local_path), S3_MEDIA_BUCKET, s3_key)
    logger.debug(f"[s3_client] Uploaded {local_path} -> s3://{S3_MEDIA_BUCKET}/{s3_key}")


def download_file(s3_key: str, local_path: Path) -> bool:
    """Download one S3 object to `local_path`. Returns False (rather than
    raising) on ANY failure - a genuinely missing key, no/invalid AWS
    credentials, a transient network error, etc. - so callers get one
    uniform "couldn't fetch it" signal and can fall back to raising their own
    clear "not found" error instead of crashing on a missing-file exception
    deep inside cv2/ffmpeg.
    """
    local_path = Path(local_path)
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _get_s3_client().download_file(S3_MEDIA_BUCKET, s3_key, str(local_path))
        return True
    except Exception as e:
        logger.warning(
            f"[s3_client] download_file failed for s3://{S3_MEDIA_BUCKET}/{s3_key}: {e}"
        )
        # Don't leave a zero-byte/partial file behind on a failed download.
        try:
            if local_path.exists():
                local_path.unlink()
        except OSError:
            pass
        return False


def file_exists_in_s3(s3_key: str) -> bool:
    try:
        _get_s3_client().head_object(Bucket=S3_MEDIA_BUCKET, Key=s3_key)
        return True
    except Exception:
        return False


def delete_video_prefix(video_id: str) -> int:
    """Delete every S3 object under videos/{video_id}/ (raw video, frames,
    torso crops) - used by the retention sweep for never-claimed videos.
    Returns the count of objects deleted; safe to call on a video with no
    objects at all (returns 0).
    """
    prefix = f"videos/{video_id}/"
    client = _get_s3_client()
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=S3_MEDIA_BUCKET, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if not keys:
                continue
            client.delete_objects(Bucket=S3_MEDIA_BUCKET, Delete={"Objects": keys})
            deleted += len(keys)
    except (BotoCoreError, ClientError) as e:
        logger.warning(f"delete_video_prefix({video_id}) failed partway: {e}")
    return deleted


def get_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    return _get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_MEDIA_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )


def upload_directory(local_dir: Path, s3_prefix: str) -> int:
    """Upload every file directly inside `local_dir` (non-recursive - frames/
    torso-crop directories are flat) to `{s3_prefix}/{filename}`, in parallel.
    Returns the count of files successfully uploaded; a single file's failure
    is logged and skipped rather than aborting the whole batch.
    """
    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        logger.warning(f"[s3_client] upload_directory: {local_dir} is not a directory, skipping")
        return 0

    files = [p for p in local_dir.iterdir() if p.is_file()]
    if not files:
        return 0

    prefix = s3_prefix.rstrip("/")

    def _upload_one(path: Path) -> bool:
        _get_s3_client().upload_file(str(path), S3_MEDIA_BUCKET, f"{prefix}/{path.name}")
        return True

    uploaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_upload_one, p): p for p in files}
        for future in concurrent.futures.as_completed(futures):
            path = futures[future]
            try:
                if future.result():
                    uploaded += 1
            except Exception as e:
                logger.warning(f"[s3_client] upload_directory: failed to upload {path}: {e}")

    logger.info(f"[s3_client] Uploaded {uploaded}/{len(files)} files from {local_dir} to s3://{S3_MEDIA_BUCKET}/{prefix}/")
    return uploaded


def download_directory(s3_prefix: str, local_dir: Path) -> int:
    """Mirror of upload_directory: download every object under `s3_prefix` in
    the shared bucket into `local_dir` (created if needed), in parallel.
    Returns the count of files successfully downloaded.
    """
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    prefix = s3_prefix.rstrip("/") + "/"

    keys = []
    try:
        client = _get_s3_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_MEDIA_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith("/"):  # skip the prefix "directory marker", if any
                    keys.append(key)
    except Exception as e:
        logger.warning(f"[s3_client] download_directory: failed to list s3://{S3_MEDIA_BUCKET}/{prefix}: {e}")
        return 0

    if not keys:
        return 0

    def _download_one(key: str) -> bool:
        filename = key[len(prefix):]
        if not filename:
            return False
        dest = local_dir / filename
        client.download_file(S3_MEDIA_BUCKET, key, str(dest))
        return True

    downloaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_download_one, k): k for k in keys}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                if future.result():
                    downloaded += 1
            except Exception as e:
                logger.warning(f"[s3_client] download_directory: failed to download {key}: {e}")

    logger.info(f"[s3_client] Downloaded {downloaded}/{len(keys)} files from s3://{S3_MEDIA_BUCKET}/{prefix} to {local_dir}")
    return downloaded


# ============================================================================
# "Ensure local" helpers - the check-then-fetch guards read sites call
# ============================================================================
# Each of these is a no-op (single existence check, no S3 round-trip at all)
# when the local file/directory is already there - only a freshly-relocated
# worker retry or an API-container request that never ran the local stage
# actually pays for a real S3 fetch.

def ensure_video_local(video_id: str) -> Path:
    """Guarantee `settings.get_video_path(video_id)` exists locally,
    downloading it from S3 first if it doesn't. Returns the local path.

    Raises S3ObjectNotFoundError if the video isn't available locally OR in S3.
    """
    local_path = settings.get_video_path(video_id)
    if local_path.exists():
        return local_path

    key = video_key(video_id)
    logger.info(f"[s3_client] Video not local for {video_id}, fetching from s3://{S3_MEDIA_BUCKET}/{key}")
    if download_file(key, local_path):
        return local_path

    raise S3ObjectNotFoundError(
        f"Video {video_id} not found locally ({local_path}) or in S3 (s3://{S3_MEDIA_BUCKET}/{key})"
    )


def ensure_frames_local(video_id: str) -> Path:
    """Guarantee `settings.get_frames_dir(video_id)` exists locally and is
    non-empty, downloading the whole frames/ prefix from S3 first if not.
    Returns the local frames directory.

    Raises S3ObjectNotFoundError if no frames are available locally OR in S3.
    """
    local_dir = settings.get_frames_dir(video_id)
    if local_dir.is_dir() and any(local_dir.iterdir()):
        return local_dir

    prefix = frames_prefix(video_id)
    logger.info(f"[s3_client] Frames not local for {video_id}, fetching from s3://{S3_MEDIA_BUCKET}/{prefix}/")
    count = download_directory(prefix, local_dir)
    if count == 0:
        raise S3ObjectNotFoundError(
            f"No frames found locally ({local_dir}) or in S3 (s3://{S3_MEDIA_BUCKET}/{prefix}/) for {video_id}"
        )
    return local_dir


def ensure_torso_crops_local(video_id: str) -> Path:
    """Guarantee `settings.get_torso_crops_dir(video_id)` exists locally and
    is non-empty, downloading the whole torso/ prefix from S3 first if not.
    Returns the local torso-crops directory.

    Raises S3ObjectNotFoundError if no torso crops are available locally OR in S3.
    """
    local_dir = settings.get_torso_crops_dir(video_id)
    if local_dir.is_dir() and any(local_dir.iterdir()):
        return local_dir

    prefix = torso_prefix(video_id)
    logger.info(f"[s3_client] Torso crops not local for {video_id}, fetching from s3://{S3_MEDIA_BUCKET}/{prefix}/")
    count = download_directory(prefix, local_dir)
    if count == 0:
        raise S3ObjectNotFoundError(
            f"No torso crops found locally ({local_dir}) or in S3 (s3://{S3_MEDIA_BUCKET}/{prefix}/) for {video_id}"
        )
    return local_dir
