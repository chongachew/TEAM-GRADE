#!/usr/bin/env python3
"""
Test Suite Runner for TEAM-GRADE Pipeline

Provides comprehensive test execution and reporting.
Usage:
    python tests/run_tests.py              # Run all tests
    python tests/run_tests.py --unit       # Run only unit tests
    python tests/run_tests.py --endpoints  # Run only endpoint tests
    python tests/run_tests.py --coverage   # Run with coverage report
"""

import subprocess
import sys
import argparse
from pathlib import Path


def run_command(cmd):
    """Run command and return result."""
    print(f"\n{'='*70}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*70}\n")
    result = subprocess.run(cmd)
    return result.returncode


def main():
    """Main test runner."""
    parser = argparse.ArgumentParser(description="TEAM-GRADE Test Suite Runner")
    parser.add_argument("--unit", action="store_true", help="Run only unit tests")
    parser.add_argument("--integration", action="store_true", help="Run only integration tests")
    parser.add_argument("--endpoints", action="store_true", help="Run only endpoint tests")
    parser.add_argument("--validators", action="store_true", help="Run only validator tests")
    parser.add_argument("--exceptions", action="store_true", help="Run only exception tests")
    parser.add_argument("--coverage", action="store_true", help="Run with coverage report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--failed-first", "-ff", action="store_true", help="Run failed tests first")
    parser.add_argument("--failed-only", "-lf", action="store_true", help="Run only failed tests")
    
    args = parser.parse_args()
    
    # Build pytest command
    cmd = ["pytest"]
    
    # Add marker filters
    markers = []
    if args.unit:
        markers.append("unit")
    if args.integration:
        markers.append("integration")
    if args.endpoints:
        markers.append("endpoints")
    if args.validators:
        markers.append("validators")
    if args.exceptions:
        markers.append("exceptions")
    
    if markers:
        # Create 'or' expression for markers
        marker_expr = " or ".join(markers)
        cmd.extend(["-m", marker_expr])
    
    # Add verbosity
    if args.verbose:
        cmd.append("-vv")
    else:
        cmd.append("-v")
    
    # Add coverage
    if args.coverage:
        cmd.extend(["--cov=.", "--cov-report=html", "--cov-report=term-missing"])
    
    # Add test failure options
    if args.failed_first:
        cmd.append("--ff")
    if args.failed_only:
        cmd.append("--lf")
    
    # Add test directory
    cmd.append("tests/")
    
    # Run tests
    exit_code = run_command(cmd)
    
    # Print summary
    print(f"\n{'='*70}")
    if exit_code == 0:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print(f"{'='*70}\n")
    
    # Show coverage report location if coverage was run
    if args.coverage and exit_code == 0:
        print("📊 Coverage report: htmlcov/index.html\n")
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
