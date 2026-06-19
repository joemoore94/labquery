"""Tests for the LIMS client layer.

Uses an in-memory fake LIMS client to test the interface contract
without requiring a running labio-all instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from labquery.lims_client import LIMSClient, RunRecord, Sample


class FakeLIMSClient(LIMSClient):
    """In-memory LIMS implementation for testing."""

    def __init__(self):
        self._samples: dict[str, Sample] = {}
        self._runs: list[RunRecord] = []

    def seed_sample(self, sample: Sample) -> None:
        self._samples[sample.sample_id] = sample

    def seed_run(self, run: RunRecord) -> None:
        self._runs.append(run)

    def get_sample(self, sample_id: str) -> Sample | None:
        return self._samples.get(sample_id)

    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        results = list(self._samples.values())
        if sample_type:
            results = [s for s in results if s.sample_type == sample_type]
        if min_volume_ul is not None:
            results = [s for s in results if s.volume_ul >= min_volume_ul]
        return results

    def update_sample(self, sample_id: str, **fields) -> Sample:
        sample = self._samples[sample_id]
        for k, v in fields.items():
            if hasattr(sample, k):
                setattr(sample, k, v)
        return sample

    def get_run_history(
        self, sample_id: str, days_back: int = 7
    ) -> list[RunRecord]:
        cutoff = datetime.now() - timedelta(days=days_back)
        return [
            r
            for r in self._runs
            if sample_id in r.sample_ids and r.started_at >= cutoff
        ]

    def record_run(self, run: RunRecord) -> RunRecord:
        self._runs.append(run)
        return run


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
    client.seed_sample(
        Sample(
            sample_id="E8RR2SS5U",
            sample_type="CEL",
            location_rack="Rack 3",
            location_position="C1",
            volume_ul=30.0,
            status="low_volume",
        )
    )
    return client


class TestGetSample:
    def test_existing_sample(self, lims: FakeLIMSClient):
        sample = lims.get_sample("C6OT0FN3S")
        assert sample is not None
        assert sample.sample_type == "CEL"
        assert sample.volume_ul == 450.0
        assert sample.location_rack == "Rack 3"

    def test_missing_sample(self, lims: FakeLIMSClient):
        assert lims.get_sample("NONEXISTENT") is None


class TestListSamples:
    def test_filter_by_type(self, lims: FakeLIMSClient):
        cel_samples = lims.list_samples(sample_type="CEL")
        assert len(cel_samples) == 2
        assert all(s.sample_type == "CEL" for s in cel_samples)

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


class TestUpdateSample:
    def test_update_volume(self, lims: FakeLIMSClient):
        updated = lims.update_sample("C6OT0FN3S", volume_ul=400.0)
        assert updated.volume_ul == 400.0
        assert lims.get_sample("C6OT0FN3S").volume_ul == 400.0

    def test_update_status(self, lims: FakeLIMSClient):
        updated = lims.update_sample("C6OT0FN3S", status="in_use")
        assert updated.status == "in_use"


class TestRunHistory:
    def test_returns_recent_runs(self, lims: FakeLIMSClient):
        lims.seed_run(
            RunRecord(
                run_id="RUN-001",
                protocol_name="cel_dna_combination",
                sample_ids=["C6OT0FN3S"],
                started_at=datetime.now() - timedelta(days=2),
                status="completed",
            )
        )
        runs = lims.get_run_history("C6OT0FN3S", days_back=7)
        assert len(runs) == 1
        assert runs[0].run_id == "RUN-001"

    def test_filters_old_runs(self, lims: FakeLIMSClient):
        lims.seed_run(
            RunRecord(
                run_id="RUN-OLD",
                protocol_name="serial_dilution",
                sample_ids=["C6OT0FN3S"],
                started_at=datetime.now() - timedelta(days=30),
                status="completed",
            )
        )
        runs = lims.get_run_history("C6OT0FN3S", days_back=7)
        assert len(runs) == 0

    def test_no_history(self, lims: FakeLIMSClient):
        runs = lims.get_run_history("D7PP1QR4T", days_back=7)
        assert runs == []


class TestRecordRun:
    def test_record_and_retrieve(self, lims: FakeLIMSClient):
        run = RunRecord(
            run_id="RUN-NEW",
            protocol_name="sample_transfer",
            sample_ids=["C6OT0FN3S", "D7PP1QR4T"],
            started_at=datetime.now(),
            status="completed",
        )
        recorded = lims.record_run(run)
        assert recorded.run_id == "RUN-NEW"

        history = lims.get_run_history("C6OT0FN3S")
        assert any(r.run_id == "RUN-NEW" for r in history)
