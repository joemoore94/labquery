"""Tests for the PLR bridge — real PyLabRobot simulator execution."""

from __future__ import annotations

import pytest

from labquery.lims_client import Sample
from labquery.plr_bridge import PLRBridge

pytestmark = pytest.mark.asyncio


def _make_sample(sample_id: str = "TEST-001", volume_ul: float = 500.0) -> Sample:
    return Sample(
        sample_id=sample_id,
        material_type="CEL",
        volume_ul=volume_ul,
        concentration=5.0,
        labware_vendor="epitube",
        labware_catalog="0030123611",
    )


@pytest.fixture
async def bridge():
    b = PLRBridge(enable_visualizer=False)
    await b.setup()
    yield b
    await b.teardown()


class TestBridgeSetup:
    async def test_setup_marks_ready(self, bridge: PLRBridge):
        assert bridge.ready

    async def test_teardown_marks_not_ready(self):
        b = PLRBridge(enable_visualizer=False)
        await b.setup()
        assert b.ready
        await b.teardown()
        assert not b.ready


class TestProtocolExecution:
    async def test_cel_dna_protocol(self, bridge: PLRBridge):
        samples = [_make_sample("S1", 500), _make_sample("S2", 300)]
        result = await bridge.execute_protocol("cel/dna", samples)

        assert result.status == "completed"
        assert result.run_id.startswith("RUN-")
        assert result.volumes_consumed == {"S1": 25.0, "S2": 25.0}
        assert result.estimated_minutes == 3.0

    async def test_serial_dilution(self, bridge: PLRBridge):
        samples = [_make_sample("S1", 500)]
        result = await bridge.execute_protocol("serial dilution", samples)

        assert result.status == "completed"
        assert result.volumes_consumed == {"S1": 10.0}

    async def test_sample_transfer(self, bridge: PLRBridge):
        samples = [_make_sample("S1", 500)]
        result = await bridge.execute_protocol("transfer", samples)

        assert result.status == "completed"
        assert result.volumes_consumed == {"S1": 50.0}

    async def test_unknown_protocol(self, bridge: PLRBridge):
        result = await bridge.execute_protocol("nonexistent", [_make_sample()])
        assert result.status == "error"

    async def test_insufficient_volume(self, bridge: PLRBridge):
        sample = _make_sample(volume_ul=5.0)
        result = await bridge.execute_protocol("sample_transfer", [sample])
        assert result.status == "error_insufficient_volume"

    async def test_too_many_samples(self, bridge: PLRBridge):
        samples = [_make_sample(f"S{i}", 500) for i in range(25)]
        result = await bridge.execute_protocol("cel/dna", samples)
        assert result.status == "error_too_many_samples"

    async def test_multiple_runs_reuse_rack(self, bridge: PLRBridge):
        samples1 = [_make_sample("S1", 500)]
        result1 = await bridge.execute_protocol("cel/dna", samples1)
        assert result1.status == "completed"

        samples2 = [_make_sample("S2", 400)]
        result2 = await bridge.execute_protocol("cel/dna", samples2)
        assert result2.status == "completed"


class TestBridgeNotReady:
    async def test_execute_without_setup(self):
        bridge = PLRBridge(enable_visualizer=False)
        with pytest.raises(RuntimeError, match="not set up"):
            await bridge.execute_protocol("cel/dna", [_make_sample()])
