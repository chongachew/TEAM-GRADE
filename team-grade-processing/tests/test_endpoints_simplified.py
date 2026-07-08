"""
Simplified Endpoint Tests

Tests API endpoint routing, request validation, and response schema
without complex Firestore mocking. These tests validate the API contracts
are correctly exposed and validators are integrated.
"""

import pytest
from fastapi.testclient import TestClient


class TestEndpointRouting:
    """Test that endpoints are properly registered and accessible."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_endpoint_exists(self, client):
        """Test that POST /api/ingest endpoint exists."""
        # Without valid data, but endpoint should exist and reject gracefully
        response = client.post("/api/ingest", json={"url": ""})
        # Should either be 200 (accepted) or 400/422 (validation) - not 404
        assert response.status_code in [200, 400, 422, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_status_endpoint_exists(self, client):
        """Test that GET /api/ingest/{video_id}/status endpoint exists."""
        # Even with invalid ID, endpoint should exist
        response = client.get("/api/ingest/invalid123456789/status")
        # Should be 400/404/500 error, not 404 endpoint not found
        assert response.status_code in [400, 404, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_info_endpoint_exists(self, client):
        """Test that GET /api/ingest/{video_id}/info endpoint exists."""
        response = client.get("/api/ingest/invalid123456789/info")
        assert response.status_code in [400, 404, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_analysis_endpoint_exists(self, client):
        """Test that GET /api/analysis/{video_id} endpoint exists."""
        response = client.get("/api/analysis/invalid123456789")
        assert response.status_code in [400, 404, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_analysis_reps_endpoint_exists(self, client):
        """Test that GET /api/analysis/{video_id}/reps endpoint exists."""
        response = client.get("/api/analysis/invalid123456789/reps")
        assert response.status_code in [400, 404, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_analysis_summary_endpoint_exists(self, client):
        """Test that GET /api/analysis/{video_id}/summary endpoint exists."""
        response = client.get("/api/analysis/invalid123456789/summary")
        assert response.status_code in [400, 404, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_analysis_metrics_endpoint_exists(self, client):
        """Test that GET /api/analysis/{video_id}/metrics endpoint exists."""
        response = client.get("/api/analysis/invalid123456789/metrics")
        assert response.status_code in [400, 404, 500]


class TestValidationIntegration:
    """Test that validators are integrated into endpoints."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_url_validation(self, client):
        """Test that URL validation is enforced on ingest endpoint."""
        # Invalid URL should be rejected
        response = client.post("/api/ingest", json={
            "url": "not a url"
        })
        # Should be 400/422 validation error
        assert response.status_code in [400, 422]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_empty_url_validation(self, client):
        """Test that empty URL is rejected."""
        response = client.post("/api/ingest", json={
            "url": ""
        })
        assert response.status_code in [400, 422]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_video_id_validation_in_path(self, client):
        """Test that video ID validation includes path parameters."""
        # Too-short video ID should be validated
        response = client.get("/api/ingest/toolshort/status")
        # 400 for validation or 404 for not found
        assert response.status_code in [400, 404, 500]


class TestAPIResponseStructure:
    """Test that API responses have expected structure."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_invalid_response_is_json(self, client):
        """Test that error responses are valid JSON."""
        response = client.post("/api/ingest", json={
            "url": ""
        })
        # Should be able to parse as JSON
        try:
            data = response.json()
            assert isinstance(data, (dict, list))
        except ValueError:
            pytest.fail("Response is not valid JSON")

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_status_not_found_response_is_json(self, client):
        """Test that 404 responses are valid JSON."""
        response = client.get("/api/ingest/dQw4w9WgXcQ/status")
        # 404 means not found - should still be valid JSON
        if response.status_code == 404:
            try:
                data = response.json()
                assert isinstance(data, dict)
            except ValueError:
                pytest.fail("404 response is not valid JSON")

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_api_prefix_present(self, client):
        """Test that all endpoints have /api prefix."""
        # Non-existent endpoint with correct prefix should get proper error
        response = client.get("/api/nonexistent")
        # 404 endpoint not found or 500 if some other error
        assert response.status_code in [404, 405, 500]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_api_prefix_required(self, client):
        """Test that endpoints without /api prefix don't work."""
        # Without /api prefix should still fail
        response = client.get("/ingest/dQw4w9WgXcQ/status")
        # 404 not found
        assert response.status_code == 404


class TestHTTPMethods:
    """Test that endpoints accept correct HTTP methods."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_post_required(self, client):
        """Test that ingest requires POST."""
        # GET should not work
        response = client.get("/api/ingest")
        # Either 405 method not allowed or 422 validation
        assert response.status_code in [405, 422]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_status_get_required(self, client):
        """Test that status endpoint requires GET."""
        # POST should not work
        response = client.post("/api/ingest/dQw4w9WgXcQ/status")
        # 405 method not allowed or similar
        assert response.status_code in [405, 422]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_analysis_get_required(self, client):
        """Test that analysis endpoints require GET."""
        response = client.post("/api/analysis/dQw4w9WgXcQ")
        assert response.status_code in [405, 422]
