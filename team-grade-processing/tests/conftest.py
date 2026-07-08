"""
Pytest Configuration and Fixtures for TEAM-GRADE Tests

Provides:
- FastAPI TestClient fixture
- Mocked Firestore fixture
- Sample video data fixtures
- Configuration fixtures
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
import sys
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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
    """Provide invalid YouTube URLs."""
    return [
        "https://vimeo.com/123456789",  # Wrong platform
        "not a url",
        "http://youtube.com",  # No video ID
        "",
        "https://youtube.com/watch?v=",  # Missing ID
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
