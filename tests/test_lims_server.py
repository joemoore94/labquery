"""Tests for the custom persistent LIMS server."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from labquery.lims_server import app, init_db, seed_samples


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_lims.db"
        app.config["DB_PATH"] = str(db_path)
        app.config["TESTING"] = True
        init_db(db_path)
        seed_samples(db_path, count=100)
        with app.test_client() as client:
            yield client


class TestSampleEndpoints:
    def test_list_sample_ids(self, client):
        resp = client.get("/samples")
        assert resp.status_code == 200
        ids = resp.get_json()
        assert len(ids) == 100
        assert all(isinstance(sid, str) for sid in ids)

    def test_get_sample(self, client):
        ids = client.get("/samples").get_json()
        sample_id = ids[0]

        resp = client.get(f"/sample/{sample_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "material" in data
        assert "volume" in data
        assert "conc" in data
        assert "labware" in data
        assert data["material"]["type"] in ("CEL", "DNA", "BAC", "PRO")

    def test_get_missing_sample(self, client):
        resp = client.get("/sample/NONEXISTENT")
        assert resp.status_code == 404

    def test_random_sample(self, client):
        resp = client.get("/samples/random")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "material" in data

    def test_update_volume(self, client):
        ids = client.get("/samples").get_json()
        sample_id = ids[0]

        resp = client.post(
            f"/sample/{sample_id}",
            json={"volume": {"value": 999.99}},
            content_type="application/json",
        )
        assert resp.status_code == 200

        updated = client.get(f"/sample/{sample_id}").get_json()
        assert updated["volume"]["value"] == 999.99

    def test_update_missing_sample(self, client):
        resp = client.post(
            "/sample/NONEXISTENT",
            json={"volume": {"value": 100}},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_update_bad_request(self, client):
        ids = client.get("/samples").get_json()
        resp = client.post(
            f"/sample/{ids[0]}",
            json={"bad": "data"},
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestRunHistory:
    def test_record_and_retrieve_run(self, client):
        run_data = {
            "run_id": "RUN-TEST001",
            "protocol_name": "cel/dna",
            "sample_ids": ["S1", "S2"],
            "started_at": "2026-06-20T10:00:00",
            "status": "completed",
        }
        resp = client.post("/runs", json=run_data, content_type="application/json")
        assert resp.status_code == 201

        resp = client.get("/runs/RUN-TEST001")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["run_id"] == "RUN-TEST001"
        assert data["sample_ids"] == ["S1", "S2"]

    def test_list_runs(self, client):
        for i in range(3):
            client.post("/runs", json={
                "run_id": f"RUN-{i}",
                "protocol_name": "transfer",
                "sample_ids": [f"S{i}"],
            }, content_type="application/json")

        resp = client.get("/runs")
        assert resp.status_code == 200
        runs = resp.get_json()
        assert len(runs) == 3

    def test_missing_run(self, client):
        resp = client.get("/runs/NONEXISTENT")
        assert resp.status_code == 404


class TestSeedEndpoint:
    def test_seed_idempotent(self, client):
        resp = client.post(
            "/samples/seed",
            json={"count": 50},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_samples"] == 100


class TestPersistence:
    def test_data_survives_reconnect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "persist_test.db"
            app.config["DB_PATH"] = str(db_path)
            init_db(db_path)
            seed_samples(db_path, count=50)

            with app.test_client() as c:
                ids = c.get("/samples").get_json()
                assert len(ids) == 50
                first_id = ids[0]
                c.post(
                    f"/sample/{first_id}",
                    json={"volume": {"value": 42.0}},
                    content_type="application/json",
                )

            with app.test_client() as c:
                ids2 = c.get("/samples").get_json()
                assert len(ids2) == 50
                data = c.get(f"/sample/{first_id}").get_json()
                assert data["volume"]["value"] == 42.0
