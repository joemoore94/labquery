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

try:
    from benchling_sdk.auth.api_key_auth import ApiKeyAuth
    from benchling_sdk.benchling import Benchling

    _HAS_BENCHLING = True
except ImportError:
    _HAS_BENCHLING = False


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

        def fetch_one(sample_id: str) -> None:
            with httpx.Client(base_url=self.base_url, timeout=30) as client:
                resp = client.get(f"/sample/{sample_id}")
            if resp.status_code == 404:
                return
            resp.raise_for_status()
            sample = self._parse_sample(sample_id, resp.json())
            self._sample_cache[sample_id] = sample

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(fetch_one, sid) for sid in uncached]
            concurrent.futures.wait(futures)

        self._all_fetched = True

    def update_sample_volume(self, sample_id: str, new_volume_ul: float) -> bool:
        resp = self._http.post(
            f"/sample/{sample_id}",
            json={"volume": {"value": new_volume_ul}},
        )
        if resp.status_code == 200 and sample_id in self._sample_cache:
            self._sample_cache[sample_id].volume_ul = new_volume_ul
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


class BenchlingClient(LIMSClient):
    """Client for Benchling LIMS via the benchling-sdk.

    Maps Benchling Containers to labquery Samples. Each container tracks
    volume, contents (entities + concentrations), and custom schema fields.

    Requires: pip install benchling-sdk (or pip install labquery[benchling])
    """

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        schema_id: str | None = None,
        material_type_field: str = "Material Type",
    ):
        if not _HAS_BENCHLING:
            raise ImportError(
                "benchling-sdk is required for Benchling integration. "
                "Install with: pip install labquery[benchling]"
            )

        resolved_url = url or os.environ.get("BENCHLING_URL", "")
        resolved_key = api_key or os.environ.get("BENCHLING_API_KEY", "")

        if not resolved_url:
            raise ValueError(
                "Benchling URL required. Set BENCHLING_URL env var "
                "or pass url= to BenchlingClient."
            )
        if not resolved_key:
            raise ValueError(
                "Benchling API key required. Set BENCHLING_API_KEY env var "
                "or pass api_key= to BenchlingClient."
            )

        self._benchling = Benchling(
            url=resolved_url.rstrip("/"),
            auth_method=ApiKeyAuth(resolved_key),
        )
        self._schema_id = schema_id
        self._material_type_field = material_type_field
        self._sample_cache: dict[str, Sample] = {}

    def get_sample(self, sample_id: str) -> Sample | None:
        if sample_id in self._sample_cache:
            return self._sample_cache[sample_id]

        try:
            container = self._benchling.containers.get_by_id(sample_id)
        except Exception:
            container = self._find_by_barcode(sample_id)

        if container is None:
            return None

        sample = self._parse_container(container)
        self._sample_cache[sample.sample_id] = sample
        return sample

    def list_sample_ids(self) -> list[str]:
        ids = []
        pages = self._benchling.containers.list(
            schema_id=self._schema_id,
            page_size=100,
        )
        for page in pages:
            for container in page:
                ids.append(container.id)
        return ids

    def list_samples(
        self,
        sample_type: str | None = None,
        min_volume_ul: float | None = None,
    ) -> list[Sample]:
        results = []
        pages = self._benchling.containers.list(
            schema_id=self._schema_id,
            page_size=100,
        )
        for page in pages:
            for container in page:
                sample = self._parse_container(container)
                self._sample_cache[sample.sample_id] = sample

                if sample_type and sample.material_type != sample_type:
                    continue
                if min_volume_ul is not None and sample.volume_ul < min_volume_ul:
                    continue
                results.append(sample)
        return results

    def update_sample_volume(self, sample_id: str, new_volume_ul: float) -> bool:
        from benchling_api_client.v2.stable.models.container_update import ContainerUpdate
        from benchling_api_client.v2.stable.models.deprecated_container_volume_for_input import (
            DeprecatedContainerVolumeForInput,
        )

        try:
            volume = DeprecatedContainerVolumeForInput(
                value=new_volume_ul,
                units="uL",
            )
            update = ContainerUpdate(volume=volume)
            self._benchling.containers.update(sample_id, update)
            if sample_id in self._sample_cache:
                self._sample_cache[sample_id].volume_ul = new_volume_ul
            return True
        except Exception:
            return False

    def _find_by_barcode(self, barcode: str):
        """Fall back to barcode search when ID lookup fails."""
        try:
            pages = self._benchling.containers.list(barcodes=[barcode])
            for page in pages:
                for container in page:
                    return container
        except Exception:
            pass
        return None

    def _parse_container(self, container) -> Sample:
        volume_ul = 0.0
        volume_unit = "uL"
        if hasattr(container, "volume") and container.volume is not None:
            volume_ul = float(container.volume.value or 0)
            volume_unit = container.volume.units or "uL"
        elif hasattr(container, "quantity") and container.quantity is not None:
            volume_ul = float(container.quantity.value or 0)
            volume_unit = container.quantity.units or "uL"

        material_type = ""
        concentration = 0.0
        concentration_unit = "mg/ml"

        if hasattr(container, "fields") and container.fields is not None:
            fields = container.fields.additional_properties
            mt_field = fields.get(self._material_type_field)
            if mt_field is not None:
                material_type = str(mt_field.value or mt_field.text_value or "")

        if hasattr(container, "contents") and container.contents:
            first_content = container.contents[0]
            if hasattr(first_content, "concentration") and first_content.concentration is not None:
                concentration = float(first_content.concentration.value or 0)
                concentration_unit = first_content.concentration.units or "mg/ml"

        created = None
        if hasattr(container, "created_at") and container.created_at is not None:
            created = container.created_at

        return Sample(
            sample_id=container.id,
            material_type=material_type,
            volume_ul=volume_ul,
            volume_unit=volume_unit,
            concentration=concentration,
            concentration_unit=concentration_unit,
            labware_vendor=container.name or "",
            sequence_url=container.web_url or "",
            created=created,
            metadata={
                "barcode": container.barcode or "",
                "benchling_name": container.name or "",
            },
        )
