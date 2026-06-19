"""Tests for the LIMS client layer.

Uses an in-memory fake LIMS client to test the interface contract
without requiring a running labio-all instance.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from labquery.lims_client import LIMSClient, Sample


class FakeLIMSClient(LIMSClient):
    """In-memory LIMS implementation for testing."""

    def __init__(self):
        self._samples: dict[str, Sample] = {}

    def seed_sample(self, sample: Sample) -> None:
        self._samples[sample.sample_id] = sample

    def get_sample(self, sample_id: str) -> Sample | None:
        return self._samples.get(sample_id)

    def list_sample_ids(self) -> list[str]:
        return list(self._samples.keys())

    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        results = list(self._samples.values())
        if sample_type:
            results = [s for s in results if s.material_type == sample_type]
        if min_volume_ul is not None:
            results = [s for s in results if s.volume_ul >= min_volume_ul]
        return results

    def update_sample_volume(self, sample_id: str, new_volume_ul: float) -> bool:
        sample = self._samples.get(sample_id)
        if sample is None:
            return False
        sample.volume_ul = new_volume_ul
        return True


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
    client.seed_sample(
        Sample(
            sample_id="E8RR2SS5U",
            material_type="CEL",
            volume_ul=30.0,
            concentration=1.5,
            labware_vendor="epitube",
            labware_catalog="0030123611",
        )
    )
    return client


class TestGetSample:
    def test_existing_sample(self, lims: FakeLIMSClient):
        sample = lims.get_sample("C6OT0FN3S")
        assert sample is not None
        assert sample.material_type == "CEL"
        assert sample.volume_ul == 450.0
        assert sample.labware_vendor == "epitube"

    def test_missing_sample(self, lims: FakeLIMSClient):
        assert lims.get_sample("NONEXISTENT") is None


class TestListSampleIds:
    def test_returns_all_ids(self, lims: FakeLIMSClient):
        ids = lims.list_sample_ids()
        assert len(ids) == 3
        assert "C6OT0FN3S" in ids


class TestListSamples:
    def test_filter_by_type(self, lims: FakeLIMSClient):
        cel_samples = lims.list_samples(sample_type="CEL")
        assert len(cel_samples) == 2
        assert all(s.material_type == "CEL" for s in cel_samples)

    def test_filter_by_volume(self, lims: FakeLIMSClient):
        samples = lims.list_samples(min_volume_ul=100)
        assert len(samples) == 2
        assert all(s.volume_ul >= 100 for s in samples)

    def test_combined_filters(self, lims: FakeLIMSClient):
        samples = lims.list_samples(sample_type="CEL", min_volume_ul=100)
        assert len(samples) == 1
        assert samples[0].sample_id == "C6OT0FN3S"

    def test_no_matches(self, lims: FakeLIMSClient):
        assert lims.list_samples(sample_type="BAC") == []


class TestUpdateSampleVolume:
    def test_update_volume(self, lims: FakeLIMSClient):
        assert lims.update_sample_volume("C6OT0FN3S", 400.0)
        assert lims.get_sample("C6OT0FN3S").volume_ul == 400.0

    def test_update_missing_sample(self, lims: FakeLIMSClient):
        assert not lims.update_sample_volume("NONEXISTENT", 100.0)
