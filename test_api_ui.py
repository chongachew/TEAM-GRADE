"""
TEAM-GRADE API + UI - Comprehensive Test Suite
Tests all API endpoints and UI functionality
"""

import requests
import json
import sys
import time
import subprocess
from pathlib import Path

# Configuration
API_URL = "http://localhost:8000"
API_TIMEOUT = 3

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}{text:^70}{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")

def print_test(name, result, details=""):
    status = f"{Colors.GREEN}✓ PASS{Colors.END}" if result else f"{Colors.RED}✗ FAIL{Colors.END}"
    print(f"{status} | {name}")
    if details:
        print(f"       {details}")

def print_section(name):
    print(f"\n{Colors.YELLOW}[{name}]{Colors.END}")

# ============================================================================
# TEST 1: File Structure
# ============================================================================

def test_file_structure():
    print_section("TEST 1: File Structure")
    
    base_path = Path("team-grade-processing")
    
    files_to_check = [
        ("api/server.py", "API server"),
        ("api/__init__.py", "API package marker"),
        ("api/requirements.txt", "API dependencies"),
        ("ui/index.html", "Web UI"),
        ("start_api.py", "Startup script"),
        ("start_api.bat", "Batch startup"),
    ]
    
    all_pass = True
    for file_path, description in files_to_check:
        full_path = base_path / file_path
        exists = full_path.exists()
        size_kb = full_path.stat().st_size / 1024 if exists else 0
        print_test(f"{description}", exists, f"{file_path} ({size_kb:.1f} KB)")
        all_pass = all_pass and exists
    
    return all_pass

# ============================================================================
# TEST 2: Module Imports
# ============================================================================

def test_module_imports():
    print_section("TEST 2: Module Imports")
    
    all_pass = True
    
    # Test FastAPI imports
    try:
        from fastapi import FastAPI
        print_test("FastAPI import", True)
    except ImportError as e:
        print_test("FastAPI import", False, str(e))
        all_pass = False
    
    # Test ingestion worker imports
    sys.path.insert(0, "team-grade-processing")
    try:
        from ingest import IngestWorker
        print_test("IngestWorker import", True)
    except ImportError as e:
        print_test("IngestWorker import", False, str(e))
        all_pass = False
    
    try:
        from ingest import YouTubeMetadataExtractor
        print_test("YouTubeMetadataExtractor import", True)
    except ImportError as e:
        print_test("YouTubeMetadataExtractor import", False, str(e))
        all_pass = False
    
    try:
        from ingest import FirestoreClient
        print_test("FirestoreClient import", True)
    except ImportError as e:
        print_test("FirestoreClient import", False, str(e))
        all_pass = False
    
    return all_pass

# ============================================================================
# TEST 3: API Server Startup (Background)
# ============================================================================

def start_api_server():
    print_section("TEST 3: Starting API Server")
    
    try:
        # Start in background
        proc = subprocess.Popen(
            [sys.executable, "team-grade-processing/api/server.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print_test("API server process started", True, f"PID: {proc.pid}")
        
        # Wait for server to start
        print("Waiting for server to initialize (5 seconds)...")
        time.sleep(5)
        
        return proc
    except Exception as e:
        print_test("API server startup", False, str(e))
        return None

# ============================================================================
# TEST 4: Endpoint Tests
# ============================================================================

def test_endpoints():
    print_section("TEST 4: API Endpoints")
    
    all_pass = True
    
    # Test 4.1: Health Check
    try:
        response = requests.get(f"{API_URL}/health", timeout=API_TIMEOUT)
        success = response.status_code == 200
        print_test("GET /health", success, f"Status: {response.status_code}")
        
        if success:
            data = response.json()
            print(f"       Components: {', '.join(data.get('components', {}).keys())}")
        
        all_pass = all_pass and success
    except Exception as e:
        print_test("GET /health", False, str(e))
        all_pass = False
    
    # Test 4.2: Queue Status
    try:
        response = requests.get(f"{API_URL}/queue", timeout=API_TIMEOUT)
        success = response.status_code == 200
        print_test("GET /queue", success, f"Status: {response.status_code}")
        
        if success:
            data = response.json()
            print(f"       Queued: {data.get('queued', 0)}, Processing: {data.get('processing', 0)}")
        
        all_pass = all_pass and success
    except Exception as e:
        print_test("GET /queue", False, str(e))
        all_pass = False
    
    # Test 4.3: Invalid Video ID
    try:
        response = requests.get(f"{API_URL}/status/invalid_id", timeout=API_TIMEOUT)
        success = response.status_code == 404
        print_test("GET /status/{invalid_id}", success, f"Status: {response.status_code} (expected 404)")
        all_pass = all_pass and success
    except Exception as e:
        print_test("GET /status/{invalid_id}", False, str(e))
        all_pass = False
    
    # Test 4.4: Ingest with Invalid URL
    try:
        response = requests.post(
            f"{API_URL}/ingest",
            json={"url": "not-a-youtube-url"},
            timeout=API_TIMEOUT
        )
        success = response.status_code == 400
        print_test("POST /ingest (invalid URL)", success, f"Status: {response.status_code} (expected 400)")
        all_pass = all_pass and success
    except Exception as e:
        print_test("POST /ingest (invalid URL)", False, str(e))
        all_pass = False
    
    # Test 4.5: Ingest with Valid YouTube URL (test URL)
    try:
        response = requests.post(
            f"{API_URL}/ingest",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            timeout=API_TIMEOUT
        )
        success = response.status_code == 200
        print_test("POST /ingest (valid URL)", success, f"Status: {response.status_code}")
        
        if success:
            data = response.json()
            video_id = data.get('video_id', 'N/A')
            status = data.get('status', 'N/A')
            print(f"       Video ID: {video_id}, Status: {status}")
        
        all_pass = all_pass and success
    except Exception as e:
        print_test("POST /ingest (valid URL)", False, str(e))
        all_pass = False
    
    return all_pass

# ============================================================================
# TEST 5: UI File
# ============================================================================

def test_ui_file():
    print_section("TEST 5: UI File Validation")
    
    ui_path = Path("team-grade-processing/ui/index.html")
    all_pass = True
    
    # Check file exists
    exists = ui_path.exists()
    print_test("UI file exists", exists, str(ui_path))
    all_pass = all_pass and exists
    
    if exists:
        content = ui_path.read_text()
        
        # Check for required elements
        checks = [
            ("<!DOCTYPE html>", "HTML doctype"),
            ("Tailwind", "Tailwind CSS"),
            ("fetch(", "Fetch API"),
            ("localhost:8000", "API endpoint"),
            ("YouTube", "YouTube references"),
        ]
        
        for pattern, description in checks:
            found = pattern in content
            print_test(f"Contains {description}", found)
            all_pass = all_pass and found
        
        # Check file size
        size_kb = ui_path.stat().st_size / 1024
        print(f"       File size: {size_kb:.1f} KB")
    
    return all_pass

# ============================================================================
# MAIN TEST RUNNER
# ============================================================================

def main():
    print_header("TEAM-GRADE API + UI - COMPREHENSIVE TEST SUITE")
    
    results = {}
    
    # Test 1: File Structure
    results["File Structure"] = test_file_structure()
    
    # Test 2: Module Imports
    results["Module Imports"] = test_module_imports()
    
    # Test 3 & 4: Start API and test endpoints
    api_proc = start_api_server()
    if api_proc:
        results["API Endpoints"] = test_endpoints()
        
        # Kill the API process
        api_proc.terminate()
        try:
            api_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            api_proc.kill()
    else:
        results["API Endpoints"] = False
    
    # Test 5: UI File
    results["UI Validation"] = test_ui_file()
    
    # Summary
    print_header("TEST SUMMARY")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = f"{Colors.GREEN}✓ PASS{Colors.END}" if result else f"{Colors.RED}✗ FAIL{Colors.END}"
        print(f"{status} | {test_name}")
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"Results: {Colors.GREEN}{passed}/{total}{Colors.END} test groups passed ({100*passed//total}%)")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")
    
    # Overall status
    if passed == total:
        print(f"{Colors.GREEN}✓✓✓ ALL TESTS PASSED ✓✓✓{Colors.END}\n")
        return 0
    else:
        print(f"{Colors.RED}✗ Some tests failed{Colors.END}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
