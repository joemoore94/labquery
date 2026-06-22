"""Tests for the PyLabRobot protocol execution layer."""

from __future__ import annotations

import pytest

from labquery.lims_client import Sample
from labquery.plr_runner import PLRRunner, _resolve_protocol


def _make_sample(sample_id: str = "TEST-001", volume_ul: float = 500.0) -> Sample:
    return Sample(
        sample_id=sample_id,
        material_type="CEL",
        volume_ul=volume_ul,
        concentration=5.0,
        labware_vendor="epitube",
        labware_catalog="0030123611",
    )


class TestResolveProtocol:
    def test_exact_key(self):
        result = _resolve_protocol("cel_dna_combination")
        assert result is not None
        assert result[0] == "cel_dna_combination"

    def test_alias(self):
        result = _resolve_protocol("CEL/DNA combination")
        assert result is not None
        assert result[0] == "cel_dna_combination"

    def test_case_insensitive(self):
        result = _resolve_protocol("Serial Dilution")
        assert result is not None
        assert result[0] == "serial_dilution"

    def test_unknown_protocol(self):
        assert _resolve_protocol("nonexistent_protocol") is None


class TestPLRRunner:
    def test_successful_run(self):
        runner = PLRRunner()
        samples = [_make_sample("S1"), _make_sample("S2")]
        result = runner.run_protocol("cel/dna", samples)

        assert result.status == "completed"
        assert result.run_id.startswith("RUN-")
        assert len(result.volumes_consumed) == 2
        assert result.volumes_consumed["S1"] == 25.0
        assert result.volumes_consumed["S2"] == 25.0
        assert result.estimated_minutes == 3.0

    def test_unknown_protocol(self):
        runner = PLRRunner()
        result = runner.run_protocol("fake_protocol", [_make_sample()])
        assert result.status == "error"

    def test_insufficient_volume(self):
        runner = PLRRunner()
        low_vol_sample = _make_sample(volume_ul=5.0)
        result = runner.run_protocol("sample_transfer", [low_vol_sample])
        assert result.status == "error_insufficient_volume"

    def test_list_protocols(self):
        runner = PLRRunner()
        protocols = runner.list_protocols()
        assert len(protocols) == 3
        names = {p["name"] for p in protocols}
        assert "cel_dna_combination" in names
        assert "serial_dilution" in names
        assert "sample_transfer" in names


class TestBackendResolution:
    def test_default_backend(self):
        runner = PLRRunner()
        assert runner.backend == "opentrons"

    def test_custom_backend(self):
        runner = PLRRunner(backend="tecan")
        assert runner.backend == "tecan"

    def test_invalid_backend(self):
        runner = PLRRunner(backend="nonexistent")
        import pytest
        with pytest.raises(ValueError, match="Unknown backend"):
            import asyncio
            asyncio.run(runner.setup())
