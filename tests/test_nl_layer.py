"""Tests for the NL layer dispatch logic.

Tests the tool dispatch and result formatting without making real API calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from labquery.lims_client import RunRecord, Sample
from labquery.nl_layer import NLLayer
from labquery.plr_runner import PLRRunner
from tests.test_lims_client import FakeLIMSClient


@pytest.fixture
def lims() -> FakeLIMSClient:
    client = FakeLIMSClient()
    client.seed_sample(
        Sample(
            sample_id="C6OT0FN3S",
            sample_type="CEL",
            location_rack="Rack 3",
            location_position="A4",
            volume_ul=450.0,
            status="available",
            last_modified=datetime(2026, 6, 18, 14, 22),
        )
    )
    client.seed_sample(
        Sample(
            sample_id="D7PP1QR4T",
            sample_type="DNA",
            location_rack="Rack 1",
            location_position="B2",
            volume_ul=200.0,
            status="available",
        )
    )
    client.seed_run(
        RunRecord(
            run_id="RUN-0042",
            protocol_name="cel_dna_combination",
            sample_ids=["C6OT0FN3S"],
            started_at=datetime.now() - timedelta(days=2),
            status="completed",
        )
    )
    return client


@pytest.fixture
def nl(lims: FakeLIMSClient) -> NLLayer:
    plr = PLRRunner(use_simulator=True)
    layer = NLLayer(lims=lims, plr=plr)
    return layer


class TestDispatchQuerySample:
    def test_existing_sample(self, nl: NLLayer):
        result = json.loads(nl._dispatch("query_sample_status", {"sample_id": "C6OT0FN3S"}))
        assert result["sample_id"] == "C6OT0FN3S"
        assert result["sample_type"] == "CEL"
        assert result["volume_ul"] == 450.0
        assert result["location_rack"] == "Rack 3"

    def test_missing_sample(self, nl: NLLayer):
        result = json.loads(nl._dispatch("query_sample_status", {"sample_id": "NOPE"}))
        assert "error" in result


class TestDispatchCheckInventory:
    def test_count_by_type(self, nl: NLLayer):
        result = json.loads(nl._dispatch("check_inventory", {"sample_type": "CEL"}))
        assert result["available_count"] == 1  # only 1 has >= 50ul
        assert result["sample_type"] == "CEL"

    def test_with_required_count(self, nl: NLLayer):
        result = json.loads(
            nl._dispatch("check_inventory", {"sample_type": "CEL", "required_count": 5})
        )
        assert result["sufficient"] is False
        assert result["shortfall"] == 4


class TestDispatchRunProtocol:
    def test_successful_run(self, nl: NLLayer):
        result = json.loads(
            nl._dispatch(
                "run_protocol",
                {"protocol_name": "cel/dna", "sample_ids": ["C6OT0FN3S"]},
            )
        )
        assert result["status"] == "completed"
        assert result["samples_processed"] == 1

    def test_missing_sample_in_run(self, nl: NLLayer):
        result = json.loads(
            nl._dispatch(
                "run_protocol",
                {"protocol_name": "cel/dna", "sample_ids": ["NONEXISTENT"]},
            )
        )
        assert "error" in result

    def test_volume_updated_after_run(self, nl: NLLayer, lims: FakeLIMSClient):
        nl._dispatch(
            "run_protocol",
            {"protocol_name": "cel/dna", "sample_ids": ["C6OT0FN3S"]},
        )
        sample = lims.get_sample("C6OT0FN3S")
        assert sample.volume_ul == 425.0  # 450 - 25


class TestDispatchRunHistory:
    def test_returns_history(self, nl: NLLayer):
        result = json.loads(
            nl._dispatch("get_run_history", {"sample_id": "C6OT0FN3S"})
        )
        assert len(result) == 1
        assert result[0]["run_id"] == "RUN-0042"

    def test_no_history(self, nl: NLLayer):
        result = json.loads(
            nl._dispatch("get_run_history", {"sample_id": "D7PP1QR4T"})
        )
        assert result == []


class TestDispatchUnknownTool:
    def test_unknown_tool(self, nl: NLLayer):
        result = json.loads(nl._dispatch("nonexistent_tool", {}))
        assert "error" in result
