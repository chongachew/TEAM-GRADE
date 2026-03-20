#!/usr/bin/env python3
"""
Test suite for YouTube ingestion worker
Reports without modifying any code
"""

import sys
sys.path.insert(0, 'team-grade-processing')

print("\n" + "=" * 70)
print("TEAM-GRADE INGESTION WORKER - TEST SUITE")
print("=" * 70)

# Test 1: Module Imports
print("\n[TEST 1] Module Imports")
print("-" * 70)
tests_passed = 0
tests_total = 0

modules = [
    ('youtube_metadata', 'YouTubeMetadataExtractor'),
    ('youtube_downloader', 'YouTubeDownloader'),
    ('firestore_client', 'FirestoreClient'),
    ('queue_manager', 'QueueManager'),
    ('ingest_worker', 'IngestWorker'),
]

for module_name, class_name in modules:
    tests_total += 1
    try:
        mod = __import__(f'ingest.{module_name}', fromlist=[class_name])
        getattr(mod, class_name)
        print(f"✓ {module_name:25} - OK")
        tests_passed += 1
    except Exception as e:
        print(f"✗ {module_name:25} - FAILED: {e}")

# Test 2: Metadata Extraction
print("\n[TEST 2] YouTube Metadata Extraction")
print("-" * 70)

from ingest.youtube_metadata import YouTubeMetadataExtractor

extractor = YouTubeMetadataExtractor()

test_cases = [
    {
        'name': 'Video ID from youtube.com URL',
        'func': lambda: extractor.get_video_id('https://www.youtube.com/watch?v=dQw4w9WgXcQ'),
        'expected': 'dQw4w9WgXcQ'
    },
    {
        'name': 'Video ID from youtu.be URL',
        'func': lambda: extractor.get_video_id('https://youtu.be/dQw4w9WgXcQ'),
        'expected': 'dQw4w9WgXcQ'
    },
    {
        'name': 'Video ID validation (valid)',
        'func': lambda: extractor.validate_youtube_url('https://www.youtube.com/watch?v=abc'),
        'expected': True
    },
    {
        'name': 'Video ID validation (invalid)',
        'func': lambda: extractor.validate_youtube_url('not-a-url'),
        'expected': False
    },
    {
        'name': 'Team extraction',
        'func': lambda: extractor.extract_teams('Alabama vs LSU - 2024')[0] is not None,
        'expected': True
    },
    {
        'name': 'Year extraction',
        'func': lambda: extractor.extract_year('Game 2024') == 2024,
        'expected': True
    },
    {
        'name': 'Level extraction',
        'func': lambda: extractor.extract_level('College Football') == 'college',
        'expected': True
    },
]

for test in test_cases:
    tests_total += 1
    try:
        result = test['func']()
        if result == test['expected']:
            print(f"✓ {test['name']:40} - OK")
            tests_passed += 1
        else:
            print(f"✗ {test['name']:40} - Got {result}, expected {test['expected']}")
    except Exception as e:
        print(f"✗ {test['name']:40} - EXCEPTION: {e}")

# Test 3: Downloader Initialization
print("\n[TEST 3] YouTube Downloader")
print("-" * 70)

from ingest.youtube_downloader import YouTubeDownloader
from pathlib import Path

tests_total += 1
try:
    downloader = YouTubeDownloader(output_dir='raw_videos')
    print(f"✓ {'Downloader initialization':40} - OK")
    tests_passed += 1
except Exception as e:
    print(f"✗ {'Downloader initialization':40} - FAILED: {e}")

tests_total += 1
try:
    result = downloader.validate_youtube_url('https://www.youtube.com/watch?v=abc')
    if result == True:
        print(f"✓ {'URL validation':40} - OK")
        tests_passed += 1
    else:
        print(f"✗ {'URL validation':40} - Failed")
except Exception as e:
    print(f"✗ {'URL validation':40} - EXCEPTION: {e}")

# Test 4: Firestore Enums
print("\n[TEST 4] Firestore Status Enums")
print("-" * 70)

from ingest.firestore_client import VideoStatus, IngestStage

tests_total += 1
try:
    statuses = [status.value for status in VideoStatus]
    assert 'pending' in statuses
    assert 'completed' in statuses
    assert 'failed' in statuses
    print(f"✓ {'VideoStatus enum':40} - OK ({len(statuses)} statuses)")
    tests_passed += 1
except Exception as e:
    print(f"✗ {'VideoStatus enum':40} - FAILED: {e}")

tests_total += 1
try:
    stages = [stage.value for stage in IngestStage]
    assert 'metadata' in stages
    assert 'download' in stages
    assert 'completed' in stages
    print(f"✓ {'IngestStage enum':40} - OK ({len(stages)} stages)")
    tests_passed += 1
except Exception as e:
    print(f"✗ {'IngestStage enum':40} - FAILED: {e}")

# Test 5: Data Structures
print("\n[TEST 5] Data Structures & Classes")
print("-" * 70)

tests_total += 1
try:
    from ingest.firestore_client import FirestoreClient
    # Don't initialize (needs auth), just verify class exists
    print(f"✓ {'FirestoreClient class':40} - OK")
    tests_passed += 1
except Exception as e:
    print(f"✗ {'FirestoreClient class':40} - FAILED: {e}")

tests_total += 1
try:
    from ingest.queue_manager import QueueManager
    print(f"✓ {'QueueManager class':40} - OK")
    tests_passed += 1
except Exception as e:
    print(f"✗ {'QueueManager class':40} - FAILED: {e}")

tests_total += 1
try:
    from ingest.ingest_worker import IngestWorker
    print(f"✓ {'IngestWorker class':40} - OK")
    tests_passed += 1
except Exception as e:
    print(f"✗ {'IngestWorker class':40} - FAILED: {e}")

# Test 6: Directory Structure
print("\n[TEST 6] Directory Structure")
print("-" * 70)

ingest_files = [
    '__init__.py',
    'ingest_worker.py',
    'youtube_metadata.py',
    'youtube_downloader.py',
    'firestore_client.py',
    'queue_manager.py',
    'README.md',
]

for filename in ingest_files:
    tests_total += 1
    filepath = Path('team-grade-processing/ingest') / filename
    if filepath.exists():
        size_kb = filepath.stat().st_size / 1024
        print(f"✓ {filename:35} - OK ({size_kb:.1f} KB)")
        tests_passed += 1
    else:
        print(f"✗ {filename:35} - NOT FOUND")

# Test 7: Function Signatures
print("\n[TEST 7] Function Signatures & Exports")
print("-" * 70)

functions_to_test = [
    ('ingest', 'IngestWorker'),
    ('ingest', 'YouTubeMetadataExtractor'),
    ('ingest', 'YouTubeDownloader'),
    ('ingest', 'FirestoreClient'),
    ('ingest', 'VideoStatus'),
    ('ingest', 'IngestStage'),
    ('ingest', 'QueueManager'),
]

for module_name, export_name in functions_to_test:
    tests_total += 1
    try:
        mod = __import__(module_name)
        obj = getattr(mod, export_name)
        print(f"✓ {module_name + '.' + export_name:40} - OK")
        tests_passed += 1
    except Exception as e:
        print(f"✗ {module_name + '.' + export_name:40} - FAILED: {e}")

# Test 8: Documentation
print("\n[TEST 8] Documentation")
print("-" * 70)

from ingest.ingest_worker import IngestWorker as IW
from ingest.youtube_metadata import YouTubeMetadataExtractor as YME
from ingest.youtube_downloader import YouTubeDownloader as YD

classes_to_check = [
    ('IngestWorker', IW),
    ('YouTubeMetadataExtractor', YME),
    ('YouTubeDownloader', YD),
]

for name, cls in classes_to_check:
    tests_total += 1
    try:
        if cls.__doc__ and len(cls.__doc__) > 20:
            doc_len = len(cls.__doc__)
            print(f"✓ {name:35} - OK (docstring: {doc_len} chars)")
            tests_passed += 1
        else:
            print(f"✗ {name:35} - No docstring")
    except Exception as e:
        print(f"✗ {name:35} - FAILED: {e}")

# Final Summary
print("\n" + "=" * 70)
print(f"FINAL RESULTS: {tests_passed}/{tests_total} tests passed")
print("=" * 70)

if tests_passed == tests_total:
    print("\n✓✓✓ ALL TESTS PASSED ✓✓✓\n")
    sys.exit(0)
else:
    failed = tests_total - tests_passed
    print(f"\n⚠ {failed} test(s) failed\n")
    sys.exit(1)
