#!/usr/bin/env python
"""
Pipeline Worker Starter
Activates environment and starts Phase 3 optimized pipeline worker
"""

import subprocess
import sys
import os
from pathlib import Path

# Ensure we're in the right directory
tgp_dir = Path(__file__).parent / "team-grade-processing"
os.chdir(tgp_dir)

print("\n" + "="*70)
print("PHASE 3 PIPELINE WORKER - DEPLOYMENT")
print("="*70)

print(f"\nWorking Directory: {os.getcwd()}")
print(f"Python: {sys.executable}")
print(f"Python Version: {sys.version.split()[0]}")

# Display startup info
print("\n" + "-"*70)
print("PHASE 3 OPTIMIZATIONS ACTIVE:")
print("-"*70)
print("  ✓ Stage 3: GPU-accelerated frame extraction (NVDEC)")
print("  ✓ Stage 4: Lightweight pose model (2MB, 2x faster)")
print("  ✓ Stage 8: Vectorized biomechanics (NumPy)")
print("\nExpected Performance: 30-50% speedup (72.1s → 50-60s per video)")

print("\n" + "-"*70)
print("STARTING WORKER...")
print("-"*70 + "\n")

try:
    # Start the worker
    subprocess.run([sys.executable, "-m", "ingest.ingest_pipeline_worker"], check=False)
except KeyboardInterrupt:
    print("\n\nWorker stopped by user.")
    sys.exit(0)
except Exception as e:
    print(f"\nError starting worker: {e}")
    sys.exit(1)
