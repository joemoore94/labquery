"""Tests for ad-hoc transfer and aspirate/dispense dispatch."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from labquery.lims_client import Sample
from labquery.nl_layer import ToolDispatcher
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
    return client


@pytest.fixture
def dispatcher(lims: FakeLIMSClient) -> ToolDispatcher:
    plr = PLRRunner()
    return ToolDispatcher(lims=lims, plr=plr)


class TestTransferDispatch:
    def test_simple_transfer(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1", "A2", "A3"],
            "destination_wells": ["B1", "B2", "B3"],
            "volume_ul": 50.0,
        }))
        assert result["status"] == "completed"
        assert result["wells_processed"] == 3
        assert result["tips_used"] == 3
        assert "A1->B1" in result["volumes_moved"]
        assert result["volumes_moved"]["A1->B1"] == 50.0

    def test_transfer_reuse_tips(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1", "A2"],
            "destination_wells": ["B1", "B2"],
            "volume_ul": 25.0,
            "reuse_tips": True,
        }))
        assert result["status"] == "completed"
        assert result["tips_used"] == 1

    def test_transfer_well_range_expansion(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1-A3"],
            "destination_wells": ["B1-B3"],
            "volume_ul": 10.0,
        }))
        assert result["status"] == "completed"
        assert result["wells_processed"] == 3

    def test_mismatched_well_counts(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1", "A2"],
            "destination_wells": ["B1"],
            "volume_ul": 50.0,
        }))
        assert "error" in result

    def test_zero_volume(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1"],
            "destination_wells": ["B1"],
            "volume_ul": 0,
        }))
        assert "error" in result

    def test_negative_volume(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1"],
            "destination_wells": ["B1"],
            "volume_ul": -10.0,
        }))
        assert "error" in result

    def test_invalid_well_position(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["Z99"],
            "destination_wells": ["B1"],
            "volume_ul": 50.0,
        }))
        assert "error" in result

    def test_run_id_generated(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("transfer", {
            "source_wells": ["A1"],
            "destination_wells": ["B1"],
            "volume_ul": 50.0,
        }))
        assert result["run_id"].startswith("RUN-")


class TestAspirateDispenseDispatch:
    def test_basic_sequence(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("aspirate_dispense", {
            "steps": [
                {"action": "aspirate", "well": "A1", "volume_ul": 100.0},
                {"action": "dispense", "well": "B1", "volume_ul": 50.0},
                {"action": "dispense", "well": "B2", "volume_ul": 50.0},
            ],
        }))
        assert result["status"] == "completed"
        assert result["wells_processed"] == 3
        assert result["tips_used"] == 1

    def test_new_tip_between_steps(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("aspirate_dispense", {
            "steps": [
                {"action": "aspirate", "well": "A1", "volume_ul": 50.0},
                {"action": "dispense", "well": "B1", "volume_ul": 50.0},
            ],
            "new_tip_between_steps": True,
        }))
        assert result["status"] == "completed"
        assert result["tips_used"] == 2

    def test_empty_steps(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("aspirate_dispense", {
            "steps": [],
        }))
        assert "error" in result

    def test_invalid_action(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("aspirate_dispense", {
            "steps": [
                {"action": "mix", "well": "A1", "volume_ul": 50.0},
            ],
        }))
        assert "error" in result

    def test_invalid_well_in_step(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("aspirate_dispense", {
            "steps": [
                {"action": "aspirate", "well": "Z99", "volume_ul": 50.0},
            ],
        }))
        assert "error" in result

    def test_zero_volume_in_step(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("aspirate_dispense", {
            "steps": [
                {"action": "aspirate", "well": "A1", "volume_ul": 0},
            ],
        }))
        assert "error" in result


class TestGetWellContents:
    def test_returns_error_without_bridge(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("get_well_contents", {}))
        assert "error" in result

    def test_with_specific_wells(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch("get_well_contents", {
            "wells": ["A1", "A2"],
        }))
        assert "error" in result


class TestNamedProtocolsStillWork:
    def test_run_protocol_still_works(self, dispatcher: ToolDispatcher):
        result = json.loads(dispatcher.dispatch(
            "run_protocol",
            {"protocol_name": "cel/dna", "sample_ids": ["C6OT0FN3S"]},
        ))
        assert result["status"] == "completed"
