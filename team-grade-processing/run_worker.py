#!/usr/bin/env python3
"""
Worker startup script - runs with proper module resolution
"""
import sys
import os
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Set up environment
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(Path(__file__).parent / "credentials" / "service-account.json"))

# Run worker
if __name__ == "__main__":
    from ingest.ingest_pipeline_worker import main
    main()
