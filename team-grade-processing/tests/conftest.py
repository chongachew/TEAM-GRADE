"""
Pytest Configuration and Fixtures for TEAM-GRADE Tests

Provides:
- FastAPI TestClient fixture
- Mocked Firestore fixture
- Sample video data fixtures
- Configuration fixtures
"""

import os
import pytest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
import sys
from datetime import datetime

import boto3

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# Real Postgres fixtures (Pass 2a data-layer migration)
# ============================================================================
# Most of the existing suite mocks firestore_client entirely (MagicMock
# substitution, see the `client` fixture below) and never touches a real
# database. The handful of tests that exercise ingest/postgres_client.py,
# ingest/utils/firestore_utils.py, or ingest/queue_manager.py directly
# (notably the SKIP LOCKED concurrency test) need a real, disposable Postgres
# instead - SQLAlchemy Core statements built with ON CONFLICT/FOR UPDATE
# SKIP LOCKED are not meaningfully testable against a MagicMock chain.
#
# Point $DATABASE_URL at a local, disposable Postgres before running these
# (see the docker run command in this project's migration notes / README):
#   docker run -d --name tg-pg-test -e POSTGRES_PASSWORD=test \
#     -e POSTGRES_DB=teamgrade_test -p 15432:5432 postgres:16
#   export DATABASE_URL=postgresql://postgres:test@localhost:15432/teamgrade_test
#   alembic upgrade head


@pytest.fixture(scope="session")
def pg_database_url():
    """The real (local, disposable) Postgres DATABASE_URL for this test run.

    Skips (rather than fails) every test that depends on this fixture when
    DATABASE_URL isn't set, so the rest of the suite (which mocks Postgres
    entirely) still runs fine in an environment with no database at all.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set - skipping real-Postgres test")
    return url


@pytest.fixture(scope="session")
def pg_engine(pg_database_url):
    """Session-scoped engine against the real test database. Assumes the
    schema has already been created via `alembic upgrade head` - these tests
    don't manage migrations themselves.
    """
    from ingest.postgres_client import make_engine

    engine = make_engine(pg_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_client(pg_engine, pg_database_url):
    """A real PostgresClient wired to the test database, with every table
    truncated before the test runs so each test starts from a clean slate
    (simpler and faster than drop/recreate-per-test for this table count).
    """
    from ingest.postgres_client import PostgresClient
    from ingest.db_schema import metadata

    with pg_engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())

    return PostgresClient(database_url=pg_database_url)


# ============================================================================
# S3 safety net (Pass 2b data-layer migration)
# ============================================================================
# This dev machine (like most engineers') has real ~/.aws/credentials
# configured for other work. Every test in this suite must be incapable of
# making a real S3 API call against the real bucket, regardless of that -
# not just "unlikely to" but structurally unable to. ingest/s3_client.py's
# every operation funnels through the single _get_s3_client() seam, so
# patching that one function here blocks all real network I/O by default.
#
# Tests that want to exercise real S3 *wire-protocol* behavior (moto-backed,
# never real AWS) request the `moto_s3` fixture below, which layers its own
# (moto-intercepted) client on top and takes precedence for the duration of
# that test. Tests that want to verify a call site invokes ingest.s3_client's
# functions correctly (without caring about S3 itself) patch those functions
# directly, which never reaches this fixture at all.


class _BlockedS3Client:
    """Stand-in for a real boto3 S3 client that refuses to do anything.
    Raising (rather than e.g. returning empty results) makes sure this is
    loud in test output if a test's mocking is ever incomplete, while still
    being caught cleanly by every call site's own non-fatal error handling
    (download_file/file_exists_in_s3 catch broadly and return False/None;
    ensure_*_local's callers already treat "unavailable" as a normal,
    handled outcome)."""

    def __getattr__(self, name):
        def _blocked(*args, **kwargs):
            raise RuntimeError(
                f"Real S3 client method '{name}()' was invoked during a test. "
                "Mock ingest.s3_client at the function boundary (or use the "
                "moto_s3 fixture) instead of letting a test reach real AWS."
            )
        return _blocked


@pytest.fixture(autouse=True)
def _block_real_s3_calls(monkeypatch):
    monkeypatch.setattr("ingest.s3_client._get_s3_client", lambda: _BlockedS3Client())


@pytest.fixture
def moto_s3(_block_real_s3_calls, monkeypatch):
    """A real (moto-mocked, never-real-AWS) S3 client with the media bucket
    pre-created, wired in as ingest.s3_client's client for the duration of
    the test. Use this for tests that exercise ingest/s3_client.py's actual
    upload/download logic against S3's wire protocol.

    Depends on `_block_real_s3_calls` explicitly so fixture setup order is
    guaranteed: this fixture's monkeypatch of `_get_s3_client` is applied
    after (and therefore overrides) the blanket-block one.
    """
    from moto import mock_aws
    import ingest.s3_client as s3_client_module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=s3_client_module.S3_MEDIA_BUCKET)
        monkeypatch.setattr(s3_client_module, "_get_s3_client", lambda: client)
        yield client


# ============================================================================
# FastAPI Test Client Fixture
# ============================================================================

@pytest.fixture
def app():
    """Provide FastAPI app instance."""
    from api.server import app
    return app


@pytest.fixture
def client(app):
    """Provide TestClient for FastAPI app.

    Safety net: api.server's module-level firestore_client/queue_manager/
    ingest_worker/metadata_extractor singletons are replaced with MagicMocks
    for the duration of the test, so no test can accidentally read or write
    the real Firestore project even if it forgets to mock a specific call.
    Tests that need specific return values layer their own `patch(...)` on
    top of these within the test body.
    """
    from starlette.testclient import TestClient as _TestClient
    default_firestore = MagicMock()
    # Safe, JSON-serializable defaults so tests that don't override a call
    # get a realistic "not found" response instead of an unserializable Mock.
    default_firestore.get_video_status.return_value = None
    default_firestore.get_queue_status.return_value = {"queued": 0, "processing": 0}
    default_firestore.check_queue_integrity.return_value = {
        "video_exists": False, "queue_exists": False,
        "status": None, "stage": None, "error": None,
    }
    with patch("api.server.firestore_client", default_firestore), \
         patch("api.server.queue_manager", MagicMock()), \
         patch("api.server.ingest_worker", MagicMock()), \
         patch("api.server.metadata_extractor", MagicMock()):
        yield _TestClient(app)


# ============================================================================
# Mocked Firestore Fixtures
# ============================================================================

@pytest.fixture
def mock_firestore_client():
    """Provide mocked Firestore client."""
    mock_client = MagicMock()
    mock_client.db = MagicMock()
    return mock_client


@pytest.fixture
def mock_firestore_doc():
    """Provide mocked Firestore document."""
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = "test_video_id"
    return mock_doc


@pytest.fixture
def mock_video_document():
    """Provide mock video document data."""
    return {
        "video_id": "dQw4w9WgXcQ",
        "title": "Test Video",
        "status": "in-progress",
        "jersey_number": 42,
        "frame_count": 300,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "stages": {
            "metadata": {"status": "completed"},
            "download": {"status": "completed"},
            "frame_extraction": {"status": "in-progress"},
            "pose": {"status": "pending"},
            "torso_crop": {"status": "pending"},
            "jersey_ocr": {"status": "pending"},
            "rep_extraction": {"status": "pending"},
            "biomechanics": {"status": "pending"},
            "complete": {"status": "pending"}
        },
        "analysis": {
            "summary": {"avg_score": 85.5},
            "reps": []
        }
    }


@pytest.fixture
def mock_rep_data():
    """Provide mock rep analysis data."""
    return {
        "rep_id": "rep_001",
        "video_id": "dQw4w9WgXcQ",
        "start_frame": 0,
        "end_frame": 60,
        "duration_seconds": 2.0,
        "analysis": {
            "position": "squat",
            "traits": {
                "depth": {"score": 85.0, "confidence": 0.92},
                "speed": {"score": 78.0, "confidence": 0.88}
            }
        }
    }


# ============================================================================
# Video ID Test Fixtures
# ============================================================================

@pytest.fixture
def valid_video_ids():
    """Provide valid YouTube video IDs."""
    return [
        "dQw4w9WgXcQ",      # Real Rick Roll video ID
        "9bZkp7q19f0",      # Example ID
        "jNQXAC9IVRw",      # YouTube test video
        "A1B2C3D4E5F",      # Alphanumeric (11 chars)
        "aAbBcCdDeEf",      # Mixed case (11 chars)
    ]


@pytest.fixture
def invalid_video_ids():
    """Provide invalid YouTube video IDs."""
    return [
        "dQw4w9WgXcQ/extra",  # Contains slash
        "dQw4w9WgXcQ..back",  # Directory traversal
        "dQw4w9WgXc",         # Too short (only 10 chars)
        "dQw4w9WgXcQX",       # Too long (12 chars)
        "",                   # Empty
        "dQw4w9Wg XcQ",       # Contains space
        "../../../etc/passwd", # Path traversal
    ]


@pytest.fixture
def valid_urls():
    """Provide valid YouTube URLs."""
    return [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtube.com/watch?v=9bZkp7q19f0",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s",  # With timestamp
        "dQw4w9WgXcQ",  # Direct ID
    ]


@pytest.fixture
def invalid_urls():
    """Provide invalid video URLs - rejected by every supported source."""
    return [
        "https://instagram.com/reel/abc123",  # Not a supported source
        "not a url",
        "http://youtube.com",  # No video ID
        "",
        "https://youtube.com/watch?v=",  # Missing ID
    ]


@pytest.fixture
def valid_non_youtube_urls():
    """Provide valid URLs from the other sources TEAM-GRADE accepts (see
    ingest/youtube_metadata.py's NON_YOUTUBE_HOST_PATTERNS/DIRECT_FILE_EXTENSIONS -
    yt-dlp already supports all of these natively)."""
    return [
        "https://vimeo.com/123456789",
        "https://www.tiktok.com/@user/video/1234567890123456789",
        "https://drive.google.com/file/d/1a2b3c4d5e6f/view",
        "https://example.com/videos/game_film.mp4",
        "https://example.com/videos/game_film.m3u8?token=abc",
    ]


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture
def test_settings():
    """Provide test configuration."""
    from config import settings
    return {
        "FIRESTORE_PROJECT_ID": settings.FIRESTORE_PROJECT_ID,
        "COLLECTION_VIDEOS": settings.COLLECTION_VIDEOS,
        "FRAME_EXTRACTION_FPS": settings.FRAME_EXTRACTION_FPS,
        "POSE_CONFIDENCE_THRESHOLD": settings.POSE_CONFIDENCE_THRESHOLD,
        "API_HOST": settings.API_HOST,
        "API_PORT": settings.API_PORT,
    }


# ============================================================================
# Markers for Test Organization
# ============================================================================

def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests with mocked services")
    config.addinivalue_line("markers", "endpoints: API endpoint tests")
    config.addinivalue_line("markers", "validators: Validator tests")
    config.addinivalue_line("markers", "exceptions: Exception handling tests")
    config.addinivalue_line("markers", "slow: Tests that take longer to run")


# ============================================================================
# Helper Fixtures
# ============================================================================

@pytest.fixture
def patch_firestore_credentials(tmp_path):
    """Patch environment to use temporary Firestore credentials."""
    # Create a mock credentials file
    creds_file = tmp_path / "service-account.json"
    creds_file.write_text('{"type": "service_account", "project_id": "test",'
                         '"private_key_id": "key_id", "private_key": "-----BEGIN RSA PRIVATE KEY-----\\n'
                         'MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygTaCL7dYkjdWuQx+bI3ew9JNj5kBXYe\\n'
                         '-----END RSA PRIVATE KEY-----\\n"}')
    
    with patch("config.settings.FIRESTORE_CREDENTIALS", creds_file):
        yield creds_file


@pytest.fixture
def sample_request_data():
    """Provide sample request payloads."""
    return {
        "ingest": {
            "valid": {
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "team_id": "team_123",
                "player_number": "42",
                "position": "rb"
            },
            "invalid": {
                "url": "https://vimeo.com/123456",
                "team_id": "",
                "player_number": "not_a_number",
            }
        }
    }
