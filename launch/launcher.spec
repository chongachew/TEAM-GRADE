# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller specification file for TEAM-GRADE Launcher
Builds a standalone .exe that includes helper scripts and all dependencies
"""

import os
import sys
from pathlib import Path

# Get the directory where this spec file is located
# When running through PyInstaller, we use the workpath
spec_dir = Path(os.getcwd()).parent / "launch"
if not spec_dir.exists():
    # Fallback: assume spec is in current working directory
    spec_dir = Path(os.getcwd())

project_root = spec_dir.parent

# Define the main launcher script
main_script = str(spec_dir / "launcher.py")

# Helper scripts are no longer needed as commands are embedded in launcher.py
helper_scripts = []

# Block list - modules to exclude
blocklist = [
    "matplotlib",
    "numpy",
    "scipy",
    "PIL",
    "PyQt5",
    "tkinter",
]

a = Analysis(
    [main_script],
    pathex=[str(project_root), str(spec_dir)],
    binaries=[],
    datas=helper_scripts,
    hiddenimports=[
        "fastapi",
        "uvicorn",
        "pydantic",
        "google.cloud.firestore",
        "google.auth",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=blocklist,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="TEAM-GRADE-Launcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Show console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
