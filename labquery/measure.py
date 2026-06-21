"""Interface to the labio-all plate reader (measure binary).

The measure binary simulates a midi-chlorian plate reader that takes
sample IDs and volumes, returning a measurement value. Only CEL and DNA
samples produce valid readings — BAC breaks the machine and PRO requires
a service wait.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from platform import system

log = logging.getLogger("labquery")

DEFAULT_MEASURE_DIR = Path("/tmp/labio-all/measure")


@dataclass
class MeasureResult:
    value: float | None
    error: str | None = None
    raw_output: str = ""


def _find_measure_binary(measure_dir: Path | None = None) -> Path | None:
    """Locate the platform-appropriate measure binary."""
    base = measure_dir or DEFAULT_MEASURE_DIR
    platform = system().lower()

    if platform == "darwin":
        path = base / "mac" / "measure"
    elif platform == "windows":
        path = base / "windows" / "measure.exe"
    elif platform == "linux":
        path = base / "linux" / "measure"
    else:
        return None

    if path.exists():
        return path
    return None


def measure_well(
    sample_ids: list[str],
    volumes: list[float],
    measure_dir: Path | None = None,
) -> MeasureResult:
    """Run the plate reader measurement on a single well.

    Args:
        sample_ids: List of sample IDs in the well.
        volumes: Corresponding volumes in uL for each sample.
        measure_dir: Override path to the measure binary directory.

    Returns:
        MeasureResult with the measurement value or error.
    """
    binary = _find_measure_binary(measure_dir)
    if binary is None:
        return MeasureResult(
            value=None,
            error="measure binary not found — clone labio-all to /tmp/labio-all",
        )

    if len(sample_ids) != len(volumes):
        return MeasureResult(
            value=None,
            error=f"Mismatched lengths: {len(sample_ids)} IDs, {len(volumes)} volumes",
        )

    cmd = [
        str(binary),
        "--ids", *sample_ids,
        "--volumes", *[str(v) for v in volumes],
    ]

    log.info("Measure: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return MeasureResult(value=None, error="Measurement timed out (120s)")

    output = result.stdout + result.stderr
    log.info("Measure output: %s", output.strip()[:200])

    if "broke" in output.lower() or "broken" in output.lower():
        return MeasureResult(
            value=None,
            error="Plate reader damaged! BAC samples are not compatible with this reader.",
            raw_output=output,
        )

    if "sorry" in output.lower() or "service tech" in output.lower():
        return MeasureResult(
            value=None,
            error="Plate reader is in service mode. Wait before measuring again.",
            raw_output=output,
        )

    for line in output.splitlines():
        if line.strip().startswith("Measurement:"):
            val_str = line.split(":", 1)[1].strip()
            try:
                return MeasureResult(value=float(val_str), raw_output=output)
            except ValueError:
                return MeasureResult(
                    value=None,
                    error=f"Could not parse measurement value: {val_str}",
                    raw_output=output,
                )

    return MeasureResult(
        value=None,
        error="No measurement value found in output",
        raw_output=output,
    )
