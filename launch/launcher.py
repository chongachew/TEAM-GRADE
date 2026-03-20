#!/usr/bin/env python3
"""
TEAM-GRADE Ingestion System - One-Click Launcher
Launches worker, API, and UI with a single click
"""

import os
import sys
import time
import subprocess
import webbrowser
import logging
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# Configure logging with ASCII-only characters (Windows compatible)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ASCII display constants
SEPARATOR = "=" * 70
ARROW = "-->"
CHECK = "[OK]"
FAIL = "[FAIL]"
WARN = "[WARN]"
WORK = "[WORK]"


class TEAMGRADELauncher:
    """One-click launcher for TEAM-GRADE ingestion system."""

    def __init__(self):
        """Initialize launcher with project paths."""
        # Handle both Python script and compiled .exe execution
        if getattr(sys, 'frozen', False):
            # Running as compiled .exe
            # The .exe is in launch/dist/, so go up 2 levels to get project root
            exe_dir = Path(sys.executable).parent
            # Navigate from dist -> launch -> project_root
            self.launch_dir = exe_dir.parent  # launch/
            self.project_root = self.launch_dir.parent  # project root
        else:
            # Running as Python script
            self.launch_dir = Path(__file__).parent
            self.project_root = self.launch_dir.parent
        
        self.venv_path = self.project_root / ".venv"

        # Process tracking
        self.processes = []
        self.worker_pid = None
        self.api_pid = None

        logger.info(f"\n{SEPARATOR}")
        logger.info("TEAM-GRADE Ingestion System Launcher")
        logger.info(f"{SEPARATOR}\n")

    def validate_environment(self) -> bool:
        """Validate environment before launching."""
        logger.info(f"{WORK} Validating environment...")

        # Check project root exists
        if not self.project_root.exists():
            logger.error(f"{FAIL} Project root not found: {self.project_root}")
            return False
        logger.info(f"{CHECK} Project root found: {self.project_root}")

        # Check venv exists
        if not self.venv_path.exists():
            logger.error(f"{FAIL} Virtual environment not found: {self.venv_path}")
            return False
        logger.info(f"{CHECK} Virtual environment found")

        # Check venv Scripts directory exists
        venv_scripts = self.venv_path / "Scripts"
        if not venv_scripts.exists():
            logger.error(f"{FAIL} venv Scripts directory not found: {venv_scripts}")
            return False
        logger.info(f"{CHECK} venv Scripts directory found")

        # Check required Python modules
        try:
            import uvicorn
            import fastapi
            logger.info(f"{CHECK} FastAPI and uvicorn available")
        except ImportError as e:
            logger.error(f"{FAIL} Missing required module: {e}")
            return False

        logger.info(f"{CHECK} Environment validation successful\n")
        return True

    def spawn_worker(self) -> bool:
        """Spawn worker in new PowerShell window."""
        try:
            logger.info(f"{WORK} Launching ingestion worker...")

            # Inline PowerShell command to activate venv and start worker
            activate_script = self.venv_path / "Scripts" / "Activate.ps1"
            ps_command = [
                "powershell.exe",
                "-NoProfile",
                "-NoExit",
                "-Command",
                f"& '{activate_script}'; cd '{self.project_root}/team-grade-processing'; python -m ingest.ingest_worker"
            ]

            # Spawn in new window (Windows-specific flag)
            process = subprocess.Popen(
                ps_command,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=str(self.project_root),
            )

            self.processes.append(("Worker", process))
            self.worker_pid = process.pid
            logger.info(f"{CHECK} Worker launched (PID: {self.worker_pid})")
            return True

        except Exception as e:
            logger.error(f"{FAIL} Failed to launch worker: {e}")
            return False

    def spawn_api(self) -> bool:
        """Spawn API server in new PowerShell window."""
        try:
            logger.info(f"{WORK} Launching API server...")

            # Inline PowerShell command to activate venv and start API
            activate_script = self.venv_path / "Scripts" / "Activate.ps1"
            ps_command = [
                "powershell.exe",
                "-NoProfile",
                "-NoExit",
                "-Command",
                f"& '{activate_script}'; cd '{self.project_root}/team-grade-processing'; python api/server.py"
            ]

            # Spawn in new window (Windows-specific flag)
            process = subprocess.Popen(
                ps_command,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=str(self.project_root),
            )

            self.processes.append(("API", process))
            self.api_pid = process.pid
            logger.info(f"{CHECK} API server launched (PID: {self.api_pid})")
            return True

        except Exception as e:
            logger.error(f"{FAIL} Failed to launch API: {e}")
            return False

    def wait_for_api(self, timeout: int = 30) -> bool:
        """Wait for API server to become available."""
        logger.info(f"{WORK} Waiting for API server to be ready...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = urlopen("http://localhost:8000/docs", timeout=2)
                if response.status == 200:
                    logger.info(f"{CHECK} API server is ready")
                    return True
            except (URLError, Exception):
                pass

            time.sleep(1)

        logger.warning(f"{WARN} API server not responding after {timeout} seconds")
        logger.info("Continuing anyway (server may still be starting)...")
        return True

    def open_ui(self) -> bool:
        """Open UI in default web browser."""
        try:
            logger.info(f"{WORK} Opening UI in browser...")

            # Open UI
            ui_url = "http://localhost:8000"
            webbrowser.open(ui_url)

            logger.info(f"{CHECK} UI opened at {ui_url}\n")
            return True

        except Exception as e:
            logger.error(f"{FAIL} Failed to open UI: {e}")
            return False

    def display_status(self):
        """Display system status."""
        logger.info(f"{SEPARATOR}")
        logger.info("TEAM-GRADE System is Running")
        logger.info(f"{SEPARATOR}")
        logger.info(f"{CHECK} Worker running (PID: {self.worker_pid})")
        logger.info(f"{CHECK} API server running (PID: {self.api_pid})")
        logger.info(f"{CHECK} UI available at http://localhost:8000")
        logger.info(f"\n{SEPARATOR}")
        logger.info("Commands:")
        logger.info("  - Keep all windows open for the system to function")
        logger.info("  - Close any window to stop that component")
        logger.info("  - Restart the launcher to restart all components")
        logger.info(f"{SEPARATOR}\n")

    def launch(self) -> bool:
        """Execute full launch sequence."""
        try:
            # Validate environment
            if not self.validate_environment():
                logger.error(f"\n{SEPARATOR}")
                logger.error(f"{FAIL} Environment validation failed")
                logger.error(f"{SEPARATOR}\n")
                return False

            # Spawn worker
            if not self.spawn_worker():
                logger.error(f"{FAIL} Failed to spawn worker")
                return False

            # Give worker time to start
            time.sleep(2)

            # Spawn API
            if not self.spawn_api():
                logger.error(f"{FAIL} Failed to spawn API")
                return False

            # Give API time to start
            time.sleep(3)

            # Wait for API to be ready
            if not self.wait_for_api():
                logger.warning(f"{WARN} API may not be fully ready")

            # Open UI
            if not self.open_ui():
                logger.warning(f"{WARN} Failed to open UI automatically")
                logger.info("You can manually open: http://localhost:8000")

            # Display status
            self.display_status()

            return True

        except KeyboardInterrupt:
            logger.info("\n" + SEPARATOR)
            logger.info("Launcher interrupted by user")
            logger.info(SEPARATOR)
            return False

        except Exception as e:
            logger.error(f"\n{FAIL} Unexpected error: {e}")
            return False


def main():
    """Main entry point."""
    launcher = TEAMGRADELauncher()

    if launcher.launch():
        logger.info("Launch sequence completed successfully!")
        sys.exit(0)
    else:
        logger.error("Launch sequence failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
