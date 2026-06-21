"""Manage a local labio-all LIMS server as a subprocess."""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger("labquery")

LABIO_REPO = "https://github.com/smohler/labio-all.git"
DEFAULT_LABIO_DIR = Path("/tmp/labio-all")
DEFAULT_PORT = 5001


def _ensure_cloned(labio_dir: Path) -> None:
    if (labio_dir / "app.py").exists():
        return
    log.info("Cloning labio-all into %s", labio_dir)
    subprocess.run(
        ["git", "clone", LABIO_REPO, str(labio_dir)],
        check=True,
        capture_output=True,
    )


def _ensure_venv(labio_dir: Path) -> Path:
    venv_dir = labio_dir / "venv"
    python = venv_dir / "bin" / "python"
    if python.exists():
        return python
    log.info("Creating labio-all venv")
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    pip = venv_dir / "bin" / "pip"
    log.info("Installing labio-all dependencies")
    # labio-all's pinned versions are incompatible with Python 3.13+
    subprocess.run(
        [str(pip), "install", "flask>=2.3", "flask-restx>=1.0", "werkzeug>=2.3"],
        check=True,
        capture_output=True,
    )
    return python


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/samples"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2)
            if resp.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.5)
    return False


def start_labio_server(
    labio_dir: Path = DEFAULT_LABIO_DIR,
    port: int = DEFAULT_PORT,
) -> subprocess.Popen:
    _ensure_cloned(labio_dir)
    python = _ensure_venv(labio_dir)

    log.info("Starting labio-all on port %d", port)
    stderr_path = labio_dir / "labio-all.stderr.log"
    stderr_file = open(stderr_path, "w")
    proc = subprocess.Popen(
        [str(python), "app.py"],
        cwd=str(labio_dir),
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
        env={**os.environ, "FLASK_RUN_PORT": str(port)},
    )
    atexit.register(proc.terminate)

    if not _wait_for_server(port):
        proc.terminate()
        stderr_file.close()
        stderr_output = stderr_path.read_text().strip()
        detail = f"\nstderr:\n{stderr_output}" if stderr_output else ""
        raise RuntimeError(
            f"labio-all failed to start on port {port} within 15 seconds{detail}"
        )

    log.info("labio-all is ready on port %d", port)
    return proc
