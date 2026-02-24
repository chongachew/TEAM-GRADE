import os
import time
import subprocess
from firestore_utils import get_next_job, update_status

RAW_DIR = "raw"
NORM_DIR = "normalized"

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(NORM_DIR, exist_ok=True)

def download_video(youtube_url, raw_path):
    cmd = [
        "yt-dlp",
        "-f", "bestvideo+bestaudio",
        "-o", raw_path,
        youtube_url
    ]
    return subprocess.call(cmd) == 0

def normalize_video(raw_path, norm_path):
    cmd = [
        "ffmpeg",
        "-i", raw_path,
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
        "-r", "30",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        norm_path,
        "-y"
    ]
    return subprocess.call(cmd) == 0

def worker_loop():
    print("Local worker started. Polling for jobs...")

    while True:
        game_id, game = get_next_job()

        if not game_id:
            time.sleep(10)
            continue

        youtube_url = game.get("youtubeUrl")
        if not youtube_url:
            update_status(game_id, "error", {"errorMessage": "Missing YouTube URL"})
            continue

        raw_path = f"{RAW_DIR}/{game_id}.mp4"
        norm_path = f"{NORM_DIR}/{game_id}.mp4"

        update_status(game_id, "downloading")

        if not download_video(youtube_url, raw_path):
            update_status(game_id, "download_failed")
            continue

        update_status(game_id, "downloaded", {"rawVideoPath": raw_path})

        update_status(game_id, "normalizing")

        if not normalize_video(raw_path, norm_path):
            update_status(game_id, "normalization_failed")
            continue

        update_status(
            game_id,
            "ready-for-analysis",
            {"normalizedVideoPath": norm_path}
        )

        print(f"Completed job for game {game_id}")
        time.sleep(5)

if __name__ == "__main__":
    worker_loop()