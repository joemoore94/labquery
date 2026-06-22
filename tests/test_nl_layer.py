"""Tests for the NL layer dispatch logic.

Tests the tool dispatch and result formatting without making real API calls.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from labquery.lims_client import Sample
from labquery.nl_layer import NLLayer
from labquery.plr_runner import PLRRunner
from tests.test_lims_client import FakeLIMSClient


@pytest.fixture
def lims() -> FakeLIMSClient:
    client = FakeLIMSClient()
    client.seed_sample(
        Sample(
            sample_id="C6OT0FN3S",
            material_type="CEL",
            volume_ul=450.0,
            concentration=5.2,
            labware_vendor="epitube",
            labware_catalog="0030123611",
            created=datetime(2026, 6, 18, 14, 22),
        )
    )
    client.seed_sample(
        Sample(
            sample_id="D7PP1QR4T",
            material_type="DNA",
            volume_ul=200.0,
            concentration=3.1,
            labware_vendor="azenta",
            labware_catalog="68-1003-10",
        )
    )
    return client


@pytest.fixture
def nl(lims: FakeLIMSClient) -> NLLayer:
    plr = PLRRunner()
    layer = NLLayer(lims=lims, plr=plr)
    return layer


class TestDispatchQuerySample:
    def test_existing_sample(self, nl: NLLayer):
        result = json.loads(nl.dispatcher.dispatch("query_sample_status", {"sample_id": "C6OT0FN3S"}))
        assert result["sample_id"] == "C6OT0FN3S"
        assert result["material_type"] == "CEL"
        assert result["volume_ul"] == 450.0
        assert result["labware_vendor"] == "epitube"

    def test_missing_sample(self, nl: NLLayer):
        result = json.loads(nl.dispatcher.dispatch("query_sample_status", {"sample_id": "NOPE"}))
        assert "error" in result


class TestDispatchCheckInventory:
    def test_count_by_type(self, nl: NLLayer):
        result = json.loads(nl.dispatcher.dispatch("check_inventory", {"sample_type": "CEL"}))
        assert result["available_count"] == 1  # only 1 CEL has >= 50ul
        assert result["sample_type"] == "CEL"

    def test_with_required_count(self, nl: NLLayer):
        result = json.loads(
            nl.dispatcher.dispatch("check_inventory", {"sample_type": "CEL", "required_count": 5})
        )
        assert result["sufficient"] is False
        assert result["shortfall"] == 4


class TestDispatchRunProtocol:
    def test_successful_run(self, nl: NLLayer):
        result = json.loads(
            nl.dispatcher.dispatch(
                "run_protocol",
                {"protocol_name": "cel/dna", "sample_ids": ["C6OT0FN3S"]},
            )
        )
        assert result["status"] == "completed"
        assert result["samples_processed"] == 1

    def test_missing_sample_in_run(self, nl: NLLayer):
        result = json.loads(
            nl.dispatcher.dispatch(
                "run_protocol",
                {"protocol_name": "cel/dna", "sample_ids": ["NONEXISTENT"]},
            )
        )
        assert "error" in result

    def test_volume_updated_after_run(self, nl: NLLayer, lims: FakeLIMSClient):
        nl.dispatcher.dispatch(
            "run_protocol",
            {"protocol_name": "cel/dna", "sample_ids": ["C6OT0FN3S"]},
        )
        sample = lims.get_sample("C6OT0FN3S")
        assert sample.volume_ul == 425.0  # 450 - 25


class TestDispatchListSampleIds:
    def test_returns_ids(self, nl: NLLayer):
        result = json.loads(nl.dispatcher.dispatch("list_sample_ids", {}))
        assert result["total_count"] == 2
        assert "C6OT0FN3S" in result["sample_ids"]

    def test_limit(self, nl: NLLayer):
        result = json.loads(nl.dispatcher.dispatch("list_sample_ids", {"limit": 1}))
        assert len(result["sample_ids"]) == 1


class TestDispatchUnknownTool:
    def test_unknown_tool(self, nl: NLLayer):
        result = json.loads(nl.dispatcher.dispatch("nonexistent_tool", {}))
        assert "error" in result
