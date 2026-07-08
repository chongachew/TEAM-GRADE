#!/usr/bin/env python
"""
API Server Starter
Starts FastAPI server on port 8000
"""

import subprocess
import sys
import os
from pathlib import Path

# Ensure we're in the right directory
tgp_dir = Path(__file__).parent / "team-grade-processing"
os.chdir(tgp_dir)

print("\n" + "="*70)
print("TEAM-GRADE API SERVER - STARTING")
print("="*70)

print(f"\nWorking Directory: {os.getcwd()}")
print(f"\nAPI Server Details:")
print(f"  Address: http://0.0.0.0:8000")
print(f"  UI: http://localhost:8080/upload.html (if HTML server running)")
print(f"  Health Check: http://localhost:8000/api/health")

print("\n" + "-"*70)
print("STARTING API SERVER...")
print("-"*70 + "\n")

try:
    # Start the API server
    subprocess.run([
        sys.executable, "-m", "uvicorn", 
        "api.server:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--reload"
    ], check=False)
except KeyboardInterrupt:
    print("\n\nAPI Server stopped by user.")
    sys.exit(0)
except Exception as e:
    print(f"\nError starting API server: {e}")
    sys.exit(1)
