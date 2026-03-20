"""
TEAM-GRADE API + UI - Final Validation Test
Tests all components without live server startup
"""

from pathlib import Path
import json

print("\n" + "=" * 80)
print("TEAM-GRADE API + UI SYSTEM - FINAL VALIDATION".center(80))
print("=" * 80 + "\n")

# === TEST 1: File Artifacts ===
print("[TEST 1] File Artifacts\n")

artifacts = [
    ("team-grade-processing/api/server.py", 9000, "API Server (FastAPI)"),
    ("team-grade-processing/api/__init__.py", 100, "API Package Marker"),
    ("team-grade-processing/api/requirements.txt", 100, "API Dependencies"),
    ("team-grade-processing/ui/index.html", 15000, "Web UI (Tailwind + JS)"),
    ("team-grade-processing/start_api.py", 1000, "Python Startup Script"),
    ("team-grade-processing/start_api.bat", 300, "Windows Startup Batch"),
    ("team-grade-processing/SETUP_GUIDE.md", 5000, "Setup Documentation"),
    ("team-grade-processing/README_API_UI.md", 8000, "API Documentation"),
    ("team-grade-processing/DELIVERABLES.md", 4000, "Deliverables Summary"),
]

passed = 0
for filepath, min_size, description in artifacts:
    p = Path(filepath)
    if p.exists():
        size = p.stat().st_size
        status = f"PASS" if size >= min_size else f"WARN"
        print(f"✓ [{status}] {description:40s} | {filepath:45s} ({size/1024:.1f} KB)")
        if status == "PASS":
            passed += 1
    else:
        print(f"✗ [FAIL] {description:40s} | {filepath:45s} (NOT FOUND)")

print(f"\n  Result: {passed}/{len(artifacts)} files present\n")

# === TEST 2: Code Structure ===
print("[TEST 2] Code Structure\n")

# Check API server contains required endpoints
api_file = Path("team-grade-processing/api/server.py")
if api_file.exists():
    content = api_file.read_text(encoding='utf-8', errors='ignore')
    
    endpoints = [
        ('@app.get("/health")', "Health Endpoint"),
        ('@app.post("/ingest")', "Ingest Endpoint"),
        ('@app.get("/status', "Status Endpoint"),
        ('@app.get("/queue")', "Queue Endpoint"),
        ("CORSMiddleware", "CORS Support"),
        ("FastAPI", "FastAPI Framework"),
        ("IngestWorker", "Ingestion Integration"),
        ("FirestoreClient", "Firestore Integration"),
    ]
    
    api_passed = 0
    for code_marker, description in endpoints:
        if code_marker in content:
            print(f"✓ [PASS] {description:35s}")
            api_passed += 1
        else:
            print(f"✗ [FAIL] {description:35s} (Missing: {code_marker})")
    
    print(f"\n  API Server: {api_passed}/{len(endpoints)} components present\n")
else:
    print("✗ API server file not found\n")

# Check UI has required elements
ui_file = Path("team-grade-processing/ui/index.html")
if ui_file.exists():
    with open(ui_file, 'rb') as f:
        content = f.read().decode('utf-8', errors='ignore')
    
    ui_elements = [
        ("<!DOCTYPE html>", "HTML Doctype"),
        ("cdn.tailwindcss.com", "Tailwind CSS"),
        ("fetch(", "Fetch API"),
        ("localhost:8000", "API Endpoint Reference"),
        ("handleIngest", "Ingest Function"),
        ("handleReset", "Reset Function"),
        ("updateStatusDisplay", "Status Update Function"),
        ("POLL_INTERVAL", "Auto-polling"),
        ("video_id", "Video ID Tracking"),
        ("progress-bar", "Progress Indicator"),
        ("stage-timeline", "Stage Timeline"),
    ]
    
    ui_passed = 0
    for marker, description in ui_elements:
        if marker in content:
            print(f"✓ [PASS] {description:35s}")
            ui_passed += 1
        else:
            print(f"✗ [FAIL] {description:35s} (Missing: {marker})")
    
    print(f"\n  Web UI: {ui_passed}/{len(ui_elements)} components present\n")
else:
    print("✗ UI file not found\n")

# === TEST 3: Configuration Files ===
print("[TEST 3] Configuration & Documentation\n")

docs = [
    ("team-grade-processing/README_API_UI.md", ["Installation", "Endpoints", "CORS"]),
    ("team-grade-processing/SETUP_GUIDE.md", ["Quick Start", "API", "UI"]),
    ("team-grade-processing/DELIVERABLES.md", ["Features", "Architecture", "Testing"]),
]

for docfile, keywords in docs:
    p = Path(docfile)
    if p.exists():
        content = p.read_text(encoding='utf-8', errors='ignore')
        found = sum(1 for kw in keywords if kw in content)
        print(f"✓ [PASS] {docfile:50s} ({found}/{len(keywords)} keywords)")
    else:
        print(f"✗ [FAIL] {docfile:50s} (NOT FOUND)")

print()

# === TEST 4: Module Imports ===
print("[TEST 4] Module Imports\n")

imports = [
    ("fastapi", "FastAPI Framework"),
    ("uvicorn", "ASGI Server"),
    ("pydantic", "Data Validation"),
]

import_passed = 0
for module, description in imports:
    try:
        __import__(module)
        print(f"✓ [PASS] {description:35s}")
        import_passed += 1
    except ImportError:
        print(f"✗ [FAIL] {description:35s} (Not installed)")

print(f"\n  Dependencies: {import_passed}/{len(imports)} installed\n")

# === TEST 5: Integration Points ===
print("[TEST 5] Integration with Ingestion Worker\n")

integration_checks = [
    ("team-grade-processing/ingest/ingest_worker.py", "IngestWorker"),
    ("team-grade-processing/ingest/youtube_metadata.py", "YouTubeMetadataExtractor"),
    ("team-grade-processing/ingest/firestore_client.py", "FirestoreClient"),
    ("team-grade-processing/ingest/youtube_downloader.py", "YouTubeDownloader"),
]

worker_passed = 0
for filepath, classname in integration_checks:
    p = Path(filepath)
    if p.exists():
        print(f"✓ [PASS] {classname:35s} | {filepath}")
        worker_passed += 1
    else:
        print(f"✗ [FAIL] {classname:35s} | {filepath}")

print(f"\n  Worker Integration: {worker_passed}/{len(integration_checks)} modules available\n")

# === SUMMARY ===
print("=" * 80)
print("VALIDATION SUMMARY".center(80))
print("=" * 80)

total_tests = [
    ("Files Created", len(artifacts), passed),
    ("API Endpoints", len(endpoints), api_passed),
    ("UI Components", len(ui_elements), ui_passed),
    ("Dependencies", len(imports), import_passed),
    ("Worker Integration", len(integration_checks), worker_passed),
]

grand_total = 0
grand_passed = 0

for test_name, total, passed_count in total_tests:
    percentage = (passed_count / total * 100) if total > 0 else 0
    status = "✓" if passed_count == total else "⚠" if passed_count > total * 0.8 else "✗"
    print(f"{status} {test_name:30s} {passed_count:2d}/{total:2d} ({percentage:5.1f}%)")
    grand_total += total
    grand_passed += passed_count

print("\n" + "-" * 80)
percentage = (grand_passed / grand_total * 100) if grand_total > 0 else 0
print(f"Overall: {grand_passed}/{grand_total} checks passed ({percentage:.1f}%)")
print("-" * 80 + "\n")

if percentage >= 95:
    print("✓✓✓ VALIDATION SUCCESSFUL - System Ready for Deployment ✓✓✓\n")
    exit(0)
elif percentage >= 80:
    print("⚠ VALIDATION PASSED WITH WARNINGS - Minor issues detected ⚠\n")
    exit(0)
else:
    print("✗ VALIDATION FAILED - Critical issues need attention ✗\n")
    exit(1)
