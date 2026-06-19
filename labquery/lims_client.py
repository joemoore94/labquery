"""REST client for LIMS backends.

Designed around an abstract interface so labio-all, Benchling, eLabJournal,
or any future LIMS can be swapped in without changing the NL or PLR layers.
"""

from __future__ import annotations

import concurrent.futures
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx


@dataclass
class Sample:
    sample_id: str
    material_type: str
    volume_ul: float
    volume_unit: str = "uL"
    concentration: float = 0.0
    concentration_unit: str = "mg/ml"
    labware_vendor: str = ""
    labware_catalog: str = ""
    sequence_url: str = ""
    created: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: str
    protocol_name: str
    sample_ids: list[str]
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "completed"
    notes: str = ""


class LIMSClient(ABC):
    """Abstract interface for LIMS backends."""

    @abstractmethod
    def get_sample(self, sample_id: str) -> Sample | None:
        """Retrieve a single sample by ID."""

    @abstractmethod
    def list_sample_ids(self) -> list[str]:
        """List all sample IDs."""

    @abstractmethod
    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        """List samples, optionally filtered by type and minimum volume."""

    @abstractmethod
    def update_sample_volume(self, sample_id: str, new_volume_ul: float) -> bool:
        """Update the volume on a sample. Returns True on success."""


class LabioAllClient(LIMSClient):
    """Client for the labio-all open-source LIMS REST API.

    API shape (Flask app on port 5001):
      GET  /samples          -> list of sample ID strings
      GET  /samples/random   -> random sample object
      GET  /sample/<id>      -> sample object
      POST /sample/<id>      -> update volume (body: {"volume": {"value": N}})
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url or os.environ.get("LABIO_URL", "http://127.0.0.1:5001")
        ).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=30)
        self._sample_cache: dict[str, Sample] = {}
        self._all_fetched: bool = False

    def get_sample(self, sample_id: str) -> Sample | None:
        if sample_id in self._sample_cache:
            return self._sample_cache[sample_id]
        resp = self._http.get(f"/sample/{sample_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        sample = self._parse_sample(sample_id, resp.json())
        self._sample_cache[sample_id] = sample
        return sample

    def list_sample_ids(self) -> list[str]:
        resp = self._http.get("/samples")
        resp.raise_for_status()
        return resp.json()

    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        if not self._all_fetched:
            self._fetch_all_samples()

        results = list(self._sample_cache.values())
        if sample_type:
            results = [s for s in results if s.material_type == sample_type]
        if min_volume_ul is not None:
            results = [s for s in results if s.volume_ul >= min_volume_ul]
        return results

    def _fetch_all_samples(self) -> None:
        ids = self.list_sample_ids()
        uncached = [sid for sid in ids if sid not in self._sample_cache]

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(self._fetch_one, sid): sid for sid in uncached}
            concurrent.futures.wait(futures)

        self._all_fetched = True

    def _fetch_one(self, sample_id: str) -> Sample | None:
        """Fetch a single sample (thread-safe, used by concurrent fetcher)."""
        resp = self._http.get(f"/sample/{sample_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        sample = self._parse_sample(sample_id, resp.json())
        self._sample_cache[sample_id] = sample
        return sample

    def update_sample_volume(self, sample_id: str, new_volume_ul: float) -> bool:
        resp = self._http.post(
            f"/sample/{sample_id}",
            json={"volume": {"value": new_volume_ul}},
        )
        return resp.status_code == 200

    def _parse_sample(self, sample_id: str, data: dict) -> Sample:
        material = data.get("material", {})
        volume = data.get("volume", {})
        conc = data.get("conc", {})
        labware = data.get("labware", {})

        return Sample(
            sample_id=sample_id,
            material_type=material.get("type", ""),
            volume_ul=float(volume.get("value", 0)),
            volume_unit=volume.get("unit", "uL"),
            concentration=float(conc.get("value", 0)),
            concentration_unit=conc.get("unit", "mg/ml"),
            labware_vendor=labware.get("vendor", ""),
            labware_catalog=labware.get("catalog", ""),
            sequence_url=material.get("seq", ""),
            created=self._parse_dt(data.get("created")),
        )

    @staticmethod
    def _parse_dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val.rstrip("Z"))
        except (ValueError, TypeError):
            return None
