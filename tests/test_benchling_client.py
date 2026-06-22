"""Tests for BenchlingClient -- mocked SDK, no live Benchling needed."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from labquery.lims_client import BenchlingClient, Sample


def _mock_container(
    container_id: str = "cnt_abc123",
    name: str = "Sample Tube 1",
    barcode: str = "BC000001",
    volume_value: float = 500.0,
    volume_units: str = "uL",
    material_type: str = "CEL",
    conc_value: float = 5.0,
    conc_units: str = "mg/ml",
):
    container = MagicMock()
    container.id = container_id
    container.name = name
    container.barcode = barcode
    container.web_url = f"https://test.benchling.com/containers/{container_id}"
    container.created_at = datetime(2026, 1, 15, 10, 30)

    container.volume = MagicMock()
    container.volume.value = volume_value
    container.volume.units = volume_units

    container.quantity = None

    mt_field = MagicMock()
    mt_field.value = material_type
    mt_field.text_value = material_type
    container.fields = MagicMock()
    container.fields.additional_properties = {"Material Type": mt_field}

    content = MagicMock()
    content.concentration = MagicMock()
    content.concentration.value = conc_value
    content.concentration.units = conc_units
    container.contents = [content]

    return container


@pytest.fixture
def mock_benchling():
    with patch("labquery.lims_client.Benchling") as MockBenchling, \
         patch("labquery.lims_client.ApiKeyAuth"), \
         patch("labquery.lims_client._HAS_BENCHLING", True):
        mock_instance = MagicMock()
        MockBenchling.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def client(mock_benchling) -> BenchlingClient:
    return BenchlingClient(
        url="https://test.benchling.com",
        api_key="sk_test_key",
    )


class TestParseContainer:
    def test_basic_parsing(self, client, mock_benchling):
        container = _mock_container()
        mock_benchling.containers.get_by_id.return_value = container

        sample = client.get_sample("cnt_abc123")

        assert sample is not None
        assert sample.sample_id == "cnt_abc123"
        assert sample.material_type == "CEL"
        assert sample.volume_ul == 500.0
        assert sample.volume_unit == "uL"
        assert sample.concentration == 5.0
        assert sample.created == datetime(2026, 1, 15, 10, 30)

    def test_barcode_fallback(self, client, mock_benchling):
        mock_benchling.containers.get_by_id.side_effect = Exception("Not found")
        container = _mock_container(container_id="cnt_xyz", barcode="BC999")

        page = [container]
        mock_benchling.containers.list.return_value = iter([page])

        sample = client.get_sample("BC999")
        assert sample is not None
        assert sample.sample_id == "cnt_xyz"

    def test_missing_sample(self, client, mock_benchling):
        mock_benchling.containers.get_by_id.side_effect = Exception("Not found")
        mock_benchling.containers.list.return_value = iter([])

        assert client.get_sample("nonexistent") is None

    def test_caching(self, client, mock_benchling):
        container = _mock_container()
        mock_benchling.containers.get_by_id.return_value = container

        client.get_sample("cnt_abc123")
        client.get_sample("cnt_abc123")

        assert mock_benchling.containers.get_by_id.call_count == 1


class TestListSamples:
    def test_list_sample_ids(self, client, mock_benchling):
        containers = [
            _mock_container("cnt_1"),
            _mock_container("cnt_2"),
            _mock_container("cnt_3"),
        ]
        mock_benchling.containers.list.return_value = iter([containers])

        ids = client.list_sample_ids()
        assert ids == ["cnt_1", "cnt_2", "cnt_3"]

    def test_list_samples_filter_by_type(self, client, mock_benchling):
        containers = [
            _mock_container("cnt_1", material_type="CEL"),
            _mock_container("cnt_2", material_type="DNA"),
            _mock_container("cnt_3", material_type="CEL"),
        ]
        mock_benchling.containers.list.return_value = iter([containers])

        results = client.list_samples(sample_type="CEL")
        assert len(results) == 2
        assert all(s.material_type == "CEL" for s in results)

    def test_list_samples_filter_by_volume(self, client, mock_benchling):
        containers = [
            _mock_container("cnt_1", volume_value=100.0),
            _mock_container("cnt_2", volume_value=500.0),
            _mock_container("cnt_3", volume_value=50.0),
        ]
        mock_benchling.containers.list.return_value = iter([containers])

        results = client.list_samples(min_volume_ul=200.0)
        assert len(results) == 1
        assert results[0].sample_id == "cnt_2"


class TestUpdateVolume:
    def test_update_success(self, client, mock_benchling):
        container = _mock_container("cnt_1", volume_value=500.0)
        mock_benchling.containers.get_by_id.return_value = container
        mock_benchling.containers.update.return_value = container

        client.get_sample("cnt_1")
        assert client.update_sample_volume("cnt_1", 400.0)
        assert client._sample_cache["cnt_1"].volume_ul == 400.0

    def test_update_failure(self, client, mock_benchling):
        mock_benchling.containers.update.side_effect = Exception("API error")
        assert not client.update_sample_volume("cnt_bad", 100.0)


class TestInitValidation:
    def test_missing_url(self):
        with patch("labquery.lims_client.Benchling"), \
             patch("labquery.lims_client.ApiKeyAuth"), \
             patch("labquery.lims_client._HAS_BENCHLING", True), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="Benchling URL required"):
                BenchlingClient(api_key="sk_test")

    def test_missing_api_key(self):
        with patch("labquery.lims_client.Benchling"), \
             patch("labquery.lims_client.ApiKeyAuth"), \
             patch("labquery.lims_client._HAS_BENCHLING", True), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key required"):
                BenchlingClient(url="https://test.benchling.com")
