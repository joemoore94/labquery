"""End-to-end integration tests.

Tests the full pipeline: LIMS query -> protocol execution -> LIMS writeback -> measurement.
Uses FakeLIMSClient (no live labio-all needed) and real PLR simulator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labquery.lims_client import Sample
from labquery.measure import _find_measure_binary
from labquery.nl_layer import ToolDispatcher
from labquery.plr_runner import PLRRunner
from tests.test_lims_client import FakeLIMSClient

MEASURE_DIR = Path("/tmp/labio-all/measure")
HAS_MEASURE = _find_measure_binary(MEASURE_DIR) is not None


@pytest.fixture
def lims() -> FakeLIMSClient:
    client = FakeLIMSClient()
    client.seed_sample(Sample(
        sample_id="CEL000001",
        material_type="CEL",
        volume_ul=1000.0,
        concentration=5.0,
    ))
    client.seed_sample(Sample(
        sample_id="DNA000001",
        material_type="DNA",
        volume_ul=800.0,
        concentration=3.0,
    ))
    client.seed_sample(Sample(
        sample_id="BAC000001",
        material_type="BAC",
        volume_ul=500.0,
    ))
    return client


@pytest.fixture
def dispatcher(lims: FakeLIMSClient) -> ToolDispatcher:
    plr = PLRRunner(use_simulator=True)
    return ToolDispatcher(lims, plr)


class TestQueryToProtocolToWriteback:
    def test_query_then_run_then_check_volume(self, dispatcher, lims):
        # 1. Query sample
        query_result = json.loads(dispatcher.dispatch(
            "query_sample_status", {"sample_id": "CEL000001"}
        ))
        assert query_result["volume_ul"] == 1000.0

        # 2. Run protocol
        run_result = json.loads(dispatcher.dispatch(
            "run_protocol",
            {"protocol_name": "cel/dna", "sample_ids": ["CEL000001"]},
        ))
        assert run_result["status"] == "completed"
        assert run_result["volumes_consumed"]["CEL000001"] == 25.0

        # 3. Verify LIMS writeback
        updated = lims.get_sample("CEL000001")
        assert updated.volume_ul == 975.0

    def test_multiple_runs_deplete_volume(self, dispatcher, lims):
        for _ in range(3):
            result = json.loads(dispatcher.dispatch(
                "run_protocol",
                {"protocol_name": "cel/dna", "sample_ids": ["CEL000001"]},
            ))
            assert result["status"] == "completed"

        sample = lims.get_sample("CEL000001")
        assert sample.volume_ul == 925.0  # 1000 - (25 * 3)

    def test_inventory_reflects_volume_changes(self, dispatcher, lims):
        # Run protocol to consume volume
        dispatcher.dispatch(
            "run_protocol",
            {"protocol_name": "sample_transfer", "sample_ids": ["DNA000001"]},
        )

        # Check inventory
        inv_result = json.loads(dispatcher.dispatch(
            "check_inventory", {"sample_type": "DNA", "min_volume_ul": 800}
        ))
        assert inv_result["available_count"] == 0  # 800 - 50 = 750, below threshold


def _reader_is_broken() -> bool:
    from labquery.measure import measure_well as mw
    r = mw(["CAAAAAAAA"], [1.0], measure_dir=MEASURE_DIR)
    return r.error is not None and ("damage" in r.error.lower() or "service" in r.error.lower())


@pytest.mark.skipif(not HAS_MEASURE, reason="measure binary not found")
class TestProtocolToMeasure:
    def test_run_then_measure_cel(self, dispatcher, lims):
        if _reader_is_broken():
            pytest.skip("Plate reader in broken/service state")

        run_result = json.loads(dispatcher.dispatch(
            "run_protocol",
            {"protocol_name": "cel/dna", "sample_ids": ["CEL000001"]},
        ))
        assert run_result["status"] == "completed"

        measure_result = json.loads(dispatcher.dispatch(
            "measure_well",
            {"sample_ids": ["CEL000001"], "volumes": [25.0]},
        ))
        assert "measurement" in measure_result
        assert measure_result["measurement"] is not None

    def test_bac_blocked_before_measure(self, dispatcher):
        result = json.loads(dispatcher.dispatch(
            "measure_well",
            {"sample_ids": ["BAC000001"], "volumes": [100.0]},
        ))
        assert "error" in result
        assert "BAC" in result["error"]

    def test_cel_dna_combination_measurement(self, dispatcher):
        if _reader_is_broken():
            pytest.skip("Plate reader in broken/service state")

        result = json.loads(dispatcher.dispatch(
            "measure_well",
            {"sample_ids": ["CEL000001", "DNA000001"], "volumes": [100.0, 100.0]},
        ))
        assert "measurement" in result
        assert result["measurement"] > 0


class TestListProtocols:
    def test_protocols_available(self, dispatcher):
        result = json.loads(dispatcher.dispatch("list_protocols", {}))
        assert len(result) == 3
        names = {p["name"] for p in result}
        assert "cel_dna_combination" in names


class TestDeckStatus:
    def test_deck_not_active(self, dispatcher):
        result = json.loads(dispatcher.dispatch("get_deck_status", {}))
        assert "error" in result
