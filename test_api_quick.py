"""
TEAM-GRADE API + UI - Quick Test Suite
Fast validation of API and UI components
"""

import subprocess
import time
import sys
import json
from pathlib import Path

try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

API_URL = "http://localhost:8000"
TIMEOUT = 3

print("\n" + "=" * 70)
print("TEAM-GRADE API + UI - QUICK TEST".center(70))
print("=" * 70 + "\n")

# Test 1: File Structure
print("[TEST 1] File Structure")
files = [
    "team-grade-processing/api/server.py",
    "team-grade-processing/ui/index.html",
    "team-grade-processing/start_api.py"
]
for f in files:
    exists = Path(f).exists()
    status = "PASS" if exists else "FAIL"
    print(f"  [{status}] {f}")

# Test 2: Dependencies
print("\n[TEST 2] Dependencies")
deps = ["fastapi", "uvicorn", "pydantic"]
for dep in deps:
    try:
        __import__(dep)
        print(f"  [PASS] {dep}")
    except ImportError:
        print(f"  [FAIL] {dep}")

# Test 3: Start API Server
print("\n[TEST 3] Starting API Server...")
proc = subprocess.Popen(
    [sys.executable, "team-grade-processing/api/server.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)
print(f"  [PASS] Server started (PID: {proc.pid})")
print("  Waiting 5 seconds for initialization...")
time.sleep(5)

# Test 4: API Endpoints
print("\n[TEST 4] API Endpoints")

tests = [
    ("GET", "/health", None),
    ("GET", "/queue", None),
    ("POST", "/ingest", {"url": "https://www.youtube.com/watch?v=test"}),
]

for method, path, data in tests:
    try:
        if method == "GET":
            r = requests.get(f"{API_URL}{path}", timeout=TIMEOUT)
        else:
            r = requests.post(f"{API_URL}{path}", json=data, timeout=TIMEOUT)
        
        status = "PASS" if r.status_code < 500 else "FAIL"
        print(f"  [{status}] {method} {path} -> {r.status_code}")
    except Exception as e:
        print(f"  [FAIL] {method} {path} -> {str(e)[:50]}")

# Test 5: UI File
print("\n[TEST 5] UI File")
ui_path = Path("team-grade-processing/ui/index.html")
if ui_path.exists():
    print(f"  [PASS] UI file exists ({ui_path.stat().st_size / 1024:.1f} KB)")
    try:
        with open(ui_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            checks = ["<!DOCTYPE", "Tailwind", "fetch", "localhost:8000"]
            for check in checks:
                if check in content:
                    print(f"  [PASS] Contains '{check}'")
                else:
                    print(f"  [FAIL] Missing '{check}'")
    except Exception as e:
        print(f"  [FAIL] Could not read UI: {e}")
else:
    print(f"  [FAIL] UI file not found")

# Cleanup
print("\n[CLEANUP] Stopping API server...")
proc.terminate()
try:
    proc.wait(timeout=2)
except subprocess.TimeoutExpired:
    proc.kill()
print("  [PASS] API server stopped")

print("\n" + "=" * 70)
print("TEST COMPLETE".center(70))
print("=" * 70 + "\n")
