#!/env/python3
"""
TEAM-GRADE Ingestion System - Complete Startup Script
Installs dependencies and starts API server
"""

import os
import sys
import subprocess
import platform

def main():
    print("\n" + "=" * 70)
    print("TEAM-GRADE INGESTION API - STARTUP")
    print("=" * 70 + "\n")
    
    # Check Python version
    print(f"✓ Python {sys.version.split()[0]}")
    
    # Get absolute path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    api_dir = os.path.join(script_dir, "api")
    ui_dir = os.path.join(script_dir, "ui")
    
    # Check dependencies
    print("\nChecking dependencies...")
    req_file = os.path.join(api_dir, "requirements.txt")
    
    try:
        import fastapi
        print("✓ FastAPI installed")
    except ImportError:
        print("⚠ FastAPI not found, installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
        print("✓ Dependencies installed")
    
    # Print startup info
    print("\n" + "=" * 70)
    print("API SERVER STARTING")
    print("=" * 70)
    print(f"Port: 8000")
    print(f"Health: http://localhost:8000/health")
    print(f"Docs: http://localhost:8000/docs")
    print(f"API URL: http://localhost:8000")
    print(f"UI Path: {ui_dir}/index.html")
    print("\n" + "-" * 70)
    
    # Start server
    os.chdir(script_dir)
    server_file = os.path.join(api_dir, "server.py")
    
    try:
        subprocess.run([sys.executable, server_file])
    except KeyboardInterrupt:
        print("\n\n✓ Server stopped by user")
        sys.exit(0)

if __name__ == "__main__":
    main()
