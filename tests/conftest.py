"""Pytest configuration for openpaws tests.

Subprocess Coverage Collection
------------------------------
This module sets up coverage collection for subprocess-based tests (like our
daemon integration tests). coverage.py can measure code in subprocesses, but
requires:

1. COVERAGE_PROCESS_START env var pointing to .coveragerc
2. coverage.process_startup() called early in subprocess execution
3. 'coverage combine' to merge data files after test run

We accomplish #2 by installing a temporary .pth file in site-packages that
imports coverage and calls process_startup().

Usage:
    # Run tests with subprocess coverage
    coverage run -m pytest tests/
    coverage combine
    coverage report --show-missing
"""

import os
import site
from pathlib import Path


def _install_coverage_pth():
    """Install a .pth file to enable coverage in subprocesses.

    Returns the path to the installed file (for cleanup), or None if not needed.
    """
    # Only install if we're running under coverage
    if "COVERAGE_PROCESS_START" not in os.environ:
        return None

    # Find site-packages directory
    site_packages = site.getsitepackages()[0]

    # Create .pth file content
    # This runs coverage.process_startup() on Python startup
    pth_content = "import coverage; coverage.process_startup()\n"

    pth_path = Path(site_packages) / "coverage_subprocess.pth"

    try:
        pth_path.write_text(pth_content)
        return pth_path
    except PermissionError:
        # Can't write to site-packages, try user site-packages
        user_site = site.getusersitepackages()
        Path(user_site).mkdir(parents=True, exist_ok=True)
        pth_path = Path(user_site) / "coverage_subprocess.pth"
        pth_path.write_text(pth_content)
        return pth_path


def _uninstall_coverage_pth(pth_path):
    """Remove the coverage .pth file."""
    if pth_path and pth_path.exists():
        pth_path.unlink()


# Track the pth file for cleanup
_coverage_pth_path = None


def pytest_configure(config):
    """Called after command line options have been parsed."""
    global _coverage_pth_path

    # Only set up subprocess coverage if explicitly running under coverage
    # (i.e., COVERAGE_PROCESS_START is already set by `coverage run`)
    # Don't auto-configure it for regular pytest runs - it can cause hangs
    if "COVERAGE_PROCESS_START" in os.environ:
        # Install the .pth file for subprocess coverage
        _coverage_pth_path = _install_coverage_pth()


def pytest_unconfigure(config):
    """Called before test process exits."""
    global _coverage_pth_path

    # Clean up the .pth file
    _uninstall_coverage_pth(_coverage_pth_path)
    _coverage_pth_path = None
