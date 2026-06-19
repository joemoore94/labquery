"""REST client for LIMS backends.

Designed around an abstract interface so labio-all, Benchling, eLabJournal,
or any future LIMS can be swapped in without changing the NL or PLR layers.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx


@dataclass
class Sample:
    sample_id: str
    sample_type: str
    location_rack: str
    location_position: str
    volume_ul: float
    status: str
    created_at: datetime | None = None
    last_modified: datetime | None = None
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
    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        """List samples, optionally filtered by type and minimum volume."""

    @abstractmethod
    def update_sample(self, sample_id: str, **fields) -> Sample:
        """Update fields on a sample (volume, location, status, etc.)."""

    @abstractmethod
    def get_run_history(
        self, sample_id: str, days_back: int = 7
    ) -> list[RunRecord]:
        """Get protocol run history for a sample."""

    @abstractmethod
    def record_run(self, run: RunRecord) -> RunRecord:
        """Record a completed protocol run."""


class LabioAllClient(LIMSClient):
    """Client for the labio-all open-source LIMS REST API."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url or os.environ.get("LABIO_URL", "http://localhost:8000")
        ).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=30)

    def get_sample(self, sample_id: str) -> Sample | None:
        resp = self._http.get(f"/samples/{sample_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._parse_sample(resp.json())

    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        params: dict = {}
        if sample_type:
            params["type"] = sample_type
        if min_volume_ul is not None:
            params["min_volume"] = min_volume_ul

        resp = self._http.get("/samples", params=params)
        resp.raise_for_status()

        data = resp.json()
        samples_list = data if isinstance(data, list) else data.get("samples", [])
        return [self._parse_sample(s) for s in samples_list]

    def update_sample(self, sample_id: str, **fields) -> Sample:
        resp = self._http.patch(f"/samples/{sample_id}", json=fields)
        resp.raise_for_status()
        return self._parse_sample(resp.json())

    def get_run_history(
        self, sample_id: str, days_back: int = 7
    ) -> list[RunRecord]:
        resp = self._http.get(
            f"/samples/{sample_id}/runs", params={"days_back": days_back}
        )
        resp.raise_for_status()
        data = resp.json()
        runs_list = data if isinstance(data, list) else data.get("runs", [])
        return [self._parse_run(r) for r in runs_list]

    def record_run(self, run: RunRecord) -> RunRecord:
        payload = {
            "run_id": run.run_id,
            "protocol_name": run.protocol_name,
            "sample_ids": run.sample_ids,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "status": run.status,
            "notes": run.notes,
        }
        resp = self._http.post("/runs", json=payload)
        resp.raise_for_status()
        return self._parse_run(resp.json())

    def _parse_sample(self, data: dict) -> Sample:
        return Sample(
            sample_id=data.get("sample_id", data.get("id", "")),
            sample_type=data.get("sample_type", data.get("type", "")),
            location_rack=data.get("location_rack", data.get("rack", "")),
            location_position=data.get("location_position", data.get("position", "")),
            volume_ul=float(data.get("volume_ul", data.get("volume", 0))),
            status=data.get("status", "unknown"),
            created_at=self._parse_dt(data.get("created_at")),
            last_modified=self._parse_dt(data.get("last_modified", data.get("updated_at"))),
            metadata={
                k: v
                for k, v in data.items()
                if k not in {
                    "sample_id", "id", "sample_type", "type",
                    "location_rack", "rack", "location_position", "position",
                    "volume_ul", "volume", "status", "created_at",
                    "last_modified", "updated_at",
                }
            },
        )

    def _parse_run(self, data: dict) -> RunRecord:
        return RunRecord(
            run_id=data.get("run_id", data.get("id", "")),
            protocol_name=data.get("protocol_name", ""),
            sample_ids=data.get("sample_ids", []),
            started_at=self._parse_dt(data.get("started_at")) or datetime.now(),
            completed_at=self._parse_dt(data.get("completed_at")),
            status=data.get("status", "completed"),
            notes=data.get("notes", ""),
        )

    @staticmethod
    def _parse_dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
