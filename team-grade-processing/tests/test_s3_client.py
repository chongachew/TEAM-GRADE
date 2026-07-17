"""
Tests for ingest/s3_client.py (Pass 2b data-layer migration).

Uses the `moto_s3` fixture (tests/conftest.py) - a moto-mocked S3 client
wired into ingest.s3_client's single _get_s3_client() seam, with the media
bucket pre-created. moto intercepts every AWS wire call, so nothing here
ever reaches real AWS regardless of local credentials.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from ingest import s3_client


class TestUploadDownloadFile:
    def test_upload_then_download_round_trips_bytes(self, moto_s3, tmp_path):
        local_path = tmp_path / "video.mp4"
        local_path.write_bytes(b"fake video bytes")

        s3_client.upload_file(local_path, "videos/abc123/raw.mp4")

        dest_path = tmp_path / "downloaded.mp4"
        result = s3_client.download_file("videos/abc123/raw.mp4", dest_path)

        assert result is True
        assert dest_path.read_bytes() == b"fake video bytes"

    def test_download_file_missing_key_returns_false(self, moto_s3, tmp_path):
        dest_path = tmp_path / "downloaded.mp4"

        result = s3_client.download_file("videos/does-not-exist/raw.mp4", dest_path)

        assert result is False
        assert not dest_path.exists()

    def test_download_file_creates_parent_directories(self, moto_s3, tmp_path):
        local_path = tmp_path / "video.mp4"
        local_path.write_bytes(b"bytes")
        s3_client.upload_file(local_path, "videos/abc123/raw.mp4")

        dest_path = tmp_path / "nested" / "dir" / "downloaded.mp4"
        result = s3_client.download_file("videos/abc123/raw.mp4", dest_path)

        assert result is True
        assert dest_path.exists()

    def test_download_file_any_exception_returns_false_not_raises(self, moto_s3, tmp_path, monkeypatch):
        """download_file's contract is "return False on any failure" (missing
        key, credentials issue, network blip, ...) so callers get one uniform
        signal instead of crashing on assorted boto3 exception types."""
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(moto_s3, "download_file", _boom)

        result = s3_client.download_file("videos/abc123/raw.mp4", tmp_path / "out.mp4")

        assert result is False


class TestFileExistsInS3:
    def test_returns_true_for_existing_key(self, moto_s3, tmp_path):
        local_path = tmp_path / "video.mp4"
        local_path.write_bytes(b"bytes")
        s3_client.upload_file(local_path, "videos/abc123/raw.mp4")

        assert s3_client.file_exists_in_s3("videos/abc123/raw.mp4") is True

    def test_returns_false_for_missing_key(self, moto_s3):
        assert s3_client.file_exists_in_s3("videos/nope/raw.mp4") is False


class TestGetPresignedUrl:
    def test_returns_a_url_string_containing_the_key(self, moto_s3, tmp_path):
        local_path = tmp_path / "video.mp4"
        local_path.write_bytes(b"bytes")
        s3_client.upload_file(local_path, "videos/abc123/raw.mp4")

        url = s3_client.get_presigned_url("videos/abc123/raw.mp4", expires_in=60)

        assert "videos/abc123/raw.mp4" in url
        assert url.startswith("http")


class TestUploadDirectory:
    def test_uploads_every_file_non_recursive(self, moto_s3, tmp_path):
        local_dir = tmp_path / "frames"
        local_dir.mkdir()
        for i in range(5):
            (local_dir / f"frame_{i:06d}.jpg").write_bytes(f"frame {i}".encode())
        # A subdirectory should be ignored (non-recursive).
        (local_dir / "subdir").mkdir()
        (local_dir / "subdir" / "nested.jpg").write_bytes(b"should not upload")

        count = s3_client.upload_directory(local_dir, "videos/abc123/frames")

        assert count == 5
        for i in range(5):
            assert s3_client.file_exists_in_s3(f"videos/abc123/frames/frame_{i:06d}.jpg")
        assert not s3_client.file_exists_in_s3("videos/abc123/frames/nested.jpg")

    def test_empty_directory_returns_zero(self, moto_s3, tmp_path):
        local_dir = tmp_path / "empty"
        local_dir.mkdir()

        assert s3_client.upload_directory(local_dir, "videos/abc123/frames") == 0

    def test_nonexistent_directory_returns_zero(self, moto_s3, tmp_path):
        assert s3_client.upload_directory(tmp_path / "does_not_exist", "videos/abc123/frames") == 0

    def test_partial_failure_does_not_abort_whole_batch(self, moto_s3, tmp_path, monkeypatch):
        local_dir = tmp_path / "frames"
        local_dir.mkdir()
        for i in range(3):
            (local_dir / f"frame_{i:06d}.jpg").write_bytes(f"frame {i}".encode())

        real_upload_file = moto_s3.upload_file

        def _flaky_upload(filename, bucket, key, *args, **kwargs):
            if "frame_000001" in key:
                raise RuntimeError("simulated per-file failure")
            return real_upload_file(filename, bucket, key, *args, **kwargs)

        monkeypatch.setattr(moto_s3, "upload_file", _flaky_upload)

        count = s3_client.upload_directory(local_dir, "videos/abc123/frames")

        assert count == 2


class TestDownloadDirectory:
    def test_downloads_every_object_under_prefix(self, moto_s3, tmp_path):
        upload_dir = tmp_path / "frames"
        upload_dir.mkdir()
        for i in range(4):
            (upload_dir / f"frame_{i:06d}.jpg").write_bytes(f"frame {i}".encode())
        s3_client.upload_directory(upload_dir, "videos/abc123/frames")

        download_dir = tmp_path / "downloaded_frames"
        count = s3_client.download_directory("videos/abc123/frames", download_dir)

        assert count == 4
        for i in range(4):
            dest = download_dir / f"frame_{i:06d}.jpg"
            assert dest.read_bytes() == f"frame {i}".encode()

    def test_empty_prefix_returns_zero_and_creates_dir(self, moto_s3, tmp_path):
        download_dir = tmp_path / "downloaded_frames"

        count = s3_client.download_directory("videos/nope/frames", download_dir)

        assert count == 0
        assert download_dir.is_dir()


class TestEnsureVideoLocal:
    def test_returns_existing_local_path_without_touching_s3(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        local_path = tmp_path / f"{video_id}.mp4"
        local_path.write_bytes(b"already here")
        monkeypatch.setattr(s3_client.settings, "get_video_path", lambda vid: local_path)

        # Sabotage the S3 client so a call would raise - proving the
        # already-local fast path never reaches it (check-then-fetch, not
        # always-fetch).
        monkeypatch.setattr(moto_s3, "download_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not be called")))

        result = s3_client.ensure_video_local(video_id)

        assert result == local_path

    def test_downloads_from_s3_when_missing_locally(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        local_path = tmp_path / f"{video_id}.mp4"
        monkeypatch.setattr(s3_client.settings, "get_video_path", lambda vid: local_path)

        s3_client._get_s3_client().put_object(
            Bucket=s3_client.S3_MEDIA_BUCKET, Key=s3_client.video_key(video_id), Body=b"from s3"
        )

        result = s3_client.ensure_video_local(video_id)

        assert result == local_path
        assert local_path.read_bytes() == b"from s3"

    def test_raises_not_found_when_missing_everywhere(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        local_path = tmp_path / f"{video_id}.mp4"
        monkeypatch.setattr(s3_client.settings, "get_video_path", lambda vid: local_path)

        with pytest.raises(s3_client.S3ObjectNotFoundError):
            s3_client.ensure_video_local(video_id)


class TestEnsureFramesLocal:
    def test_returns_existing_nonempty_dir_without_touching_s3(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        frames_dir = tmp_path / "frames" / video_id
        frames_dir.mkdir(parents=True)
        (frames_dir / "frame_000000.jpg").write_bytes(b"already here")
        monkeypatch.setattr(s3_client.settings, "get_frames_dir", lambda vid: frames_dir)

        result = s3_client.ensure_frames_local(video_id)

        assert result == frames_dir

    def test_downloads_from_s3_when_missing_locally(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        frames_dir = tmp_path / "frames" / video_id
        monkeypatch.setattr(s3_client.settings, "get_frames_dir", lambda vid: frames_dir)

        for i in range(3):
            s3_client._get_s3_client().put_object(
                Bucket=s3_client.S3_MEDIA_BUCKET,
                Key=s3_client.frame_key(video_id, f"frame_{i:06d}.jpg"),
                Body=f"frame {i}".encode(),
            )

        result = s3_client.ensure_frames_local(video_id)

        assert result == frames_dir
        assert len(list(frames_dir.iterdir())) == 3

    def test_raises_not_found_when_missing_everywhere(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        frames_dir = tmp_path / "frames" / video_id
        monkeypatch.setattr(s3_client.settings, "get_frames_dir", lambda vid: frames_dir)

        with pytest.raises(s3_client.S3ObjectNotFoundError):
            s3_client.ensure_frames_local(video_id)


class TestEnsureTorsoCropsLocal:
    def test_returns_existing_nonempty_dir_without_touching_s3(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        torso_dir = tmp_path / "torso_crops" / video_id
        torso_dir.mkdir(parents=True)
        (torso_dir / "torso_000000.jpg").write_bytes(b"already here")
        monkeypatch.setattr(s3_client.settings, "get_torso_crops_dir", lambda vid: torso_dir)

        result = s3_client.ensure_torso_crops_local(video_id)

        assert result == torso_dir

    def test_downloads_from_s3_when_missing_locally(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        torso_dir = tmp_path / "torso_crops" / video_id
        monkeypatch.setattr(s3_client.settings, "get_torso_crops_dir", lambda vid: torso_dir)

        s3_client._get_s3_client().put_object(
            Bucket=s3_client.S3_MEDIA_BUCKET,
            Key=s3_client.torso_key(video_id, "torso_000005_007.jpg"),
            Body=b"crop bytes",
        )

        result = s3_client.ensure_torso_crops_local(video_id)

        assert result == torso_dir
        assert (torso_dir / "torso_000005_007.jpg").read_bytes() == b"crop bytes"

    def test_raises_not_found_when_missing_everywhere(self, moto_s3, tmp_path, monkeypatch):
        video_id = "dQw4w9WgXcQ"
        torso_dir = tmp_path / "torso_crops" / video_id
        monkeypatch.setattr(s3_client.settings, "get_torso_crops_dir", lambda vid: torso_dir)

        with pytest.raises(s3_client.S3ObjectNotFoundError):
            s3_client.ensure_torso_crops_local(video_id)


class TestKeyScheme:
    def test_video_key(self):
        assert s3_client.video_key("abc123") == "videos/abc123/raw.mp4"

    def test_frame_key_matches_local_naming_convention(self):
        # ingest/gpu_utils/frame_extractor.py writes frame_{count:06d}.jpg locally.
        assert s3_client.frame_key("abc123", "frame_000042.jpg") == "videos/abc123/frames/frame_000042.jpg"

    def test_torso_key_matches_local_naming_convention(self):
        # ingest/stages/torso_crop_stage.py writes torso_{frame:06d}[_{track:03d}].jpg locally.
        assert s3_client.torso_key("abc123", "torso_000042_007.jpg") == "videos/abc123/torso/torso_000042_007.jpg"


class TestBlockedClientSafetyNet:
    """Confirms the conftest.py autouse fixture is actually in effect for a
    plain (non-moto_s3) test - i.e. that a test which forgets to request
    `moto_s3` still can't reach real AWS."""

    def test_real_s3_call_is_blocked_by_default(self, tmp_path):
        result = s3_client.download_file("videos/abc123/raw.mp4", tmp_path / "out.mp4")

        assert result is False
