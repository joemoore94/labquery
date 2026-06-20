"""Tests for the plate reader measurement interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from labquery.measure import MeasureResult, _find_measure_binary, measure_well

MEASURE_DIR = Path("/tmp/labio-all/measure")
HAS_MEASURE = _find_measure_binary(MEASURE_DIR) is not None

pytestmark = pytest.mark.skipif(not HAS_MEASURE, reason="measure binary not found")


def _reader_is_broken() -> bool:
    """Check if the plate reader is in broken/service state."""
    r = measure_well(["CAAAAAAAA"], [1.0], measure_dir=MEASURE_DIR)
    return r.error is not None and ("damage" in r.error.lower() or "service" in r.error.lower())


class TestMeasureWell:
    def test_cel_sample_returns_value(self):
        if _reader_is_broken():
            pytest.skip("Plate reader in broken/service state from prior BAC sample")
        result = measure_well(["CEEHRCE8G"], [100.0], measure_dir=MEASURE_DIR)
        assert result.error is None
        assert result.value is not None
        assert result.value >= 0

    def test_mismatched_lengths(self):
        result = measure_well(["A", "B"], [100.0], measure_dir=MEASURE_DIR)
        assert result.error is not None
        assert "Mismatched" in result.error

    def test_missing_binary(self):
        result = measure_well(["X"], [100.0], measure_dir=Path("/nonexistent"))
        assert result.error is not None
        assert "not found" in result.error


class TestDispatchValidation:
    def test_bac_blocked_by_dispatch(self):
        from labquery.lims_client import Sample
        from labquery.nl_layer import ToolDispatcher
        from labquery.plr_runner import PLRRunner
        from tests.test_lims_client import FakeLIMSClient
        import json

        lims = FakeLIMSClient()
        lims.seed_sample(Sample(
            sample_id="BAC001",
            material_type="BAC",
            volume_ul=500.0,
        ))
        dispatcher = ToolDispatcher(lims, PLRRunner())
        result = json.loads(dispatcher.dispatch(
            "measure_well", {"sample_ids": ["BAC001"], "volumes": [100]}
        ))
        assert "error" in result
        assert "BAC" in result["error"]

    def test_pro_blocked_by_dispatch(self):
        from labquery.lims_client import Sample
        from labquery.nl_layer import ToolDispatcher
        from labquery.plr_runner import PLRRunner
        from tests.test_lims_client import FakeLIMSClient
        import json

        lims = FakeLIMSClient()
        lims.seed_sample(Sample(
            sample_id="PRO001",
            material_type="PRO",
            volume_ul=500.0,
        ))
        dispatcher = ToolDispatcher(lims, PLRRunner())
        result = json.loads(dispatcher.dispatch(
            "measure_well", {"sample_ids": ["PRO001"], "volumes": [100]}
        ))
        assert "error" in result
        assert "PRO" in result["error"]
