"""
TEAM-GRADE Dependency Manager
Ensures all required packages are installed before startup.
"""

import subprocess
import sys
from pathlib import Path


def install_requirements():
    """Install all Python dependencies from requirements.txt files."""
    
    requirements_files = [
        "requirements.txt",
        "team-grade-processing/requirements.txt",
        "team-grade-processing/api/requirements.txt",
    ]
    
    print("\n" + "="*70)
    print("TEAM-GRADE Dependency Installation")
    print("="*70 + "\n")
    
    project_root = Path(__file__).parent
    all_requirements = set()
    
    # Collect all requirements
    for req_file in requirements_files:
        req_path = project_root / req_file
        if req_path.exists():
            print(f"Reading: {req_file}")
            with open(req_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        all_requirements.add(line.split("#")[0].strip())
    
    # Add critical packages not always in requirements
    critical_packages = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "google-cloud-firestore",
        "yt-dlp",
        "opencv-python",
        "numpy",
        "pillow",
        "requests",
        "python-dotenv",
        "pyyaml",
    ]
    
    for pkg in critical_packages:
        all_requirements.add(pkg)
    
    print(f"\nTotal packages to install: {len(all_requirements)}\n")
    
    # Install each requirement
    failed_packages = []
    for i, req in enumerate(sorted(all_requirements), 1):
        print(f"[{i}/{len(all_requirements)}] Installing {req}...", end=" ", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", req, "-q"],
                capture_output=True,
                timeout=60
            )
            if result.returncode == 0:
                print("✓")
            else:
                print("⚠")
                failed_packages.append((req, "Installation failed"))
        except subprocess.TimeoutExpired:
            print("⚠ (timeout)")
            failed_packages.append((req, "Timeout"))
        except Exception as e:
            print("✗")
            failed_packages.append((req, str(e)))
    
    # Summary
    print("\n" + "="*70)
    print("Installation Summary")
    print("="*70)
    print(f"✓ Successfully processed: {len(all_requirements) - len(failed_packages)}/{len(all_requirements)}")
    
    if failed_packages:
        print(f"\n⚠ Warning: {len(failed_packages)} packages had issues:")
        for pkg, reason in failed_packages:
            print(f"  - {pkg}: {reason}")
        print("\nNote: Some packages may not be critical. The system may still work.")
    else:
        print("\n✓ All dependencies installed successfully!")
    
    print("="*70 + "\n")
    
    return len(failed_packages) == 0


if __name__ == "__main__":
    success = install_requirements()
    sys.exit(0 if success else 1)
