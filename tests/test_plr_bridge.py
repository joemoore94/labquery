"""Tests for the PLR bridge -- real PyLabRobot simulator execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from labquery.lims_client import Sample
from labquery.plr_bridge import BACKEND_PRESETS, PLRBridge

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
    config = BACKEND_PRESETS["opentrons"]
    b = PLRBridge(config=config, simulate=True)
    await b.setup()
    yield b
    await b.teardown()


class TestBridgeSetup:
    async def test_setup_marks_ready(self, bridge: PLRBridge):
        assert bridge.ready

    async def test_teardown_marks_not_ready(self):
        config = BACKEND_PRESETS["opentrons"]
        b = PLRBridge(config=config, simulate=True)
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
        config = BACKEND_PRESETS["opentrons"]
        bridge = PLRBridge(config=config, simulate=True)
        with pytest.raises(RuntimeError, match="not set up"):
            await bridge.execute_protocol("cel/dna", [_make_sample()])


class TestBackendPresets:
    def test_all_presets_exist(self):
        assert "opentrons" in BACKEND_PRESETS
        assert "tecan" in BACKEND_PRESETS
        assert "hamilton" in BACKEND_PRESETS

    def test_preset_fields(self):
        for name, config in BACKEND_PRESETS.items():
            assert config.name, f"{name} missing name"
            assert config.setup_deck is not None, f"{name} missing setup_deck"
            assert config.sim_backend_factory is not None, f"{name} missing sim_backend_factory"

    async def test_hardware_not_yet_supported(self):
        config = BACKEND_PRESETS["opentrons"]
        b = PLRBridge(config=config, simulate=False)
        with pytest.raises(NotImplementedError, match="not yet tested"):
            await b.setup()


class TestSetupCombinations:
    """Test all (backend x simulate x enable_visualizer) combinations."""

    @pytest.mark.parametrize("backend", [
        "opentrons",
        pytest.param("tecan", marks=pytest.mark.xfail(reason="Tecan deck missing trash area")),
        "hamilton",
    ])
    async def test_simulate_no_visualizer(self, backend):
        b = PLRBridge(config=BACKEND_PRESETS[backend], simulate=True, enable_visualizer=False)
        await b.setup()
        assert b.ready
        await b.teardown()
        assert not b.ready

    @pytest.mark.parametrize("backend", ["opentrons", "tecan", "hamilton"])
    async def test_hardware_rejected(self, backend):
        b = PLRBridge(config=BACKEND_PRESETS[backend], simulate=False, enable_visualizer=False)
        with pytest.raises(NotImplementedError):
            await b.setup()

    @pytest.mark.parametrize("backend", ["opentrons", "tecan", "hamilton"])
    async def test_hardware_with_visualizer_rejected(self, backend):
        b = PLRBridge(config=BACKEND_PRESETS[backend], simulate=False, enable_visualizer=True)
        with pytest.raises(NotImplementedError):
            await b.setup()

    @pytest.mark.parametrize("backend", [
        pytest.param("tecan", marks=pytest.mark.xfail(reason="Tecan deck missing trash area")),
        "hamilton",
    ])
    async def test_visualizer_unsupported_backend(self, backend):
        b = PLRBridge(config=BACKEND_PRESETS[backend], simulate=True, enable_visualizer=True)
        with pytest.raises(ValueError, match="not supported"):
            await b.setup()
        await b.teardown()

    @patch("pylabrobot.visualizer.Visualizer")
    async def test_visualizer_opentrons_simulator(self, mock_vis_cls):
        mock_vis = AsyncMock()
        mock_vis_cls.return_value = mock_vis

        b = PLRBridge(config=BACKEND_PRESETS["opentrons"], simulate=True, enable_visualizer=True)
        await b.setup()

        assert b.ready
        mock_vis_cls.assert_called_once()
        _, kwargs = mock_vis_cls.call_args
        assert Path(kwargs["favicon"]).exists()
        assert Path(kwargs["favicon"]).read_bytes()[:4] == b"\x89PNG"
        mock_vis.setup.assert_awaited_once()

        await b.teardown()
        mock_vis.stop.assert_awaited_once()
        assert not Path(kwargs["favicon"]).exists()


class TestFavicon:
    def test_default_favicon_is_valid_png(self):
        import base64
        data = base64.b64decode(PLRBridge._DEFAULT_FAVICON)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_favicon_cleaned_up_on_teardown(self):
        b = PLRBridge(config=BACKEND_PRESETS["opentrons"], simulate=True)
        path = b._resolve_visualizer_favicon()
        assert Path(path).exists()
        await b.teardown()
        assert not Path(path).exists()
