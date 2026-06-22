"""Tests for ELabJournalClient -- mocked HTTP, no live eLabJournal needed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from labquery.lims_client import ELabJournalClient


@pytest.fixture
def mock_httpx():
    with patch("labquery.lims_client.httpx") as mock:
        mock_client = MagicMock()
        mock.Client.return_value = mock_client
        yield mock_client


@pytest.fixture
def client(mock_httpx) -> ELabJournalClient:
    return ELabJournalClient(
        url="https://test.elabjournal.com",
        api_key="test-api-key",
    )


def _sample_response(
    sample_id: int = 1001,
    name: str = "Sample A",
    sample_type: str = "CEL",
    volume: float = 500.0,
    conc: float = 5.0,
):
    return {
        "sampleID": sample_id,
        "name": name,
        "barcode": f"BC{sample_id}",
        "sampleType": {"name": sample_type},
        "meta": {
            "volume": {"value": volume, "unit": "uL"},
            "concentration": {"value": conc, "unit": "mg/ml"},
        },
        "created": "2026-01-15T10:30:00Z",
    }


class TestGetSample:
    def test_basic_get(self, client, mock_httpx):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _sample_response()
        mock_httpx.get.return_value = resp

        sample = client.get_sample("1001")
        assert sample is not None
        assert sample.sample_id == "1001"
        assert sample.material_type == "CEL"
        assert sample.volume_ul == 500.0
        assert sample.concentration == 5.0

    def test_missing_sample(self, client, mock_httpx):
        resp = MagicMock()
        resp.status_code = 404
        mock_httpx.get.return_value = resp

        assert client.get_sample("9999") is None

    def test_caching(self, client, mock_httpx):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _sample_response()
        mock_httpx.get.return_value = resp

        client.get_sample("1001")
        client.get_sample("1001")
        assert mock_httpx.get.call_count == 1


class TestListSamples:
    def test_list_ids(self, client, mock_httpx):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": [
                {"sampleID": 1},
                {"sampleID": 2},
                {"sampleID": 3},
            ],
            "recordCount": 3,
        }
        mock_httpx.get.return_value = resp

        ids = client.list_sample_ids()
        assert ids == ["1", "2", "3"]

    def test_list_samples_filter_type(self, client, mock_httpx):
        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {
            "data": [{"sampleID": 1}, {"sampleID": 2}],
            "recordCount": 2,
        }

        get_resp_1 = MagicMock()
        get_resp_1.status_code = 200
        get_resp_1.json.return_value = _sample_response(1, sample_type="CEL")

        get_resp_2 = MagicMock()
        get_resp_2.status_code = 200
        get_resp_2.json.return_value = _sample_response(2, sample_type="DNA")

        mock_httpx.get.side_effect = [list_resp, get_resp_1, get_resp_2]

        results = client.list_samples(sample_type="CEL")
        assert len(results) == 1
        assert results[0].material_type == "CEL"


class TestUpdateVolume:
    def test_update_success(self, client, mock_httpx):
        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = _sample_response()
        mock_httpx.get.return_value = get_resp

        client.get_sample("1001")

        patch_resp = MagicMock()
        patch_resp.status_code = 200
        mock_httpx.patch.return_value = patch_resp

        assert client.update_sample_volume("1001", 400.0)
        assert client._sample_cache["1001"].volume_ul == 400.0

    def test_update_failure(self, client, mock_httpx):
        mock_httpx.patch.side_effect = Exception("Network error")
        assert not client.update_sample_volume("1001", 100.0)


class TestInitValidation:
    def test_missing_url(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="eLabJournal URL required"):
                ELabJournalClient(api_key="test-key")

    def test_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key required"):
                ELabJournalClient(url="https://test.elabjournal.com")
