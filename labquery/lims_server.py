"""Labquery's own persistent LIMS -- SQLite-backed Flask API.

Same REST shape as labio-all so LabioAllClient works with zero changes.
Data persists across restarts at ~/.labquery/lims.db.
"""

from __future__ import annotations

import json
import random
import string
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import atexit
import logging
import subprocess
import sys
import time

import httpx
from flask import Flask, jsonify, request

log = logging.getLogger("labquery")

DEFAULT_DB_PATH = Path.home() / ".labquery" / "lims.db"

app = Flask(__name__)
app.config["DB_PATH"] = str(DEFAULT_DB_PATH)


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(app.config["DB_PATH"])
    db.row_factory = sqlite3.Row
    return db


def init_db(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS samples (
            sample_id TEXT PRIMARY KEY,
            material_type TEXT NOT NULL,
            volume_ul REAL NOT NULL,
            volume_unit TEXT DEFAULT 'uL',
            concentration REAL DEFAULT 0.0,
            concentration_unit TEXT DEFAULT 'mg/ml',
            labware_vendor TEXT DEFAULT '',
            labware_catalog TEXT DEFAULT '',
            sequence_url TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS run_history (
            run_id TEXT PRIMARY KEY,
            protocol_name TEXT NOT NULL,
            sample_ids TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT DEFAULT 'completed',
            notes TEXT DEFAULT ''
        );
    """)
    db.close()


def seed_samples(db_path: str | Path | None = None, count: int = 10000) -> int:
    path = str(db_path) if db_path else app.config["DB_PATH"]
    db = sqlite3.connect(path)

    existing = db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    if existing > 0:
        db.close()
        return existing

    materials = ["CEL", "DNA", "BAC", "PRO"]
    material_prefixes = {"CEL": "C", "DNA": "D", "BAC": "B", "PRO": "P"}
    vendors = [
        ("epitube", "0030123611"),
        ("azenta", "68-1003-10"),
    ]
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5 * 365)

    rows = []
    for _ in range(count):
        material = random.choice(materials)
        prefix = material_prefixes[material]
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        sample_id = prefix + suffix
        vendor, catalog = random.choice(vendors)
        volume = round(random.uniform(500, 1400), 2)
        conc = round(random.uniform(1.0, 10.0), 2)
        created = start_date + timedelta(
            seconds=random.randint(0, int((end_date - start_date).total_seconds()))
        )
        seq_url = f"awesometx.s3.amazonaws.com/wgs/{material}/{sample_id}"

        rows.append((
            sample_id, material, volume, "uL", conc, "mg/ml",
            vendor, catalog, seq_url, created.isoformat() + "Z",
        ))

    db.executemany(
        "INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    inserted = db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    db.close()
    return inserted


def _row_to_json(row: sqlite3.Row) -> dict:
    return {
        "material": {
            "type": row["material_type"],
            "seq": row["sequence_url"],
        },
        "volume": {
            "value": row["volume_ul"],
            "unit": row["volume_unit"],
        },
        "conc": {
            "value": row["concentration"],
            "unit": row["concentration_unit"],
        },
        "labware": {
            "vendor": row["labware_vendor"],
            "catalog": row["labware_catalog"],
        },
        "created": row["created_at"],
    }


@app.route("/samples", methods=["GET"])
def get_all_sample_ids():
    db = _get_db()
    rows = db.execute("SELECT sample_id FROM samples").fetchall()
    db.close()
    return jsonify([r["sample_id"] for r in rows])


@app.route("/samples/random", methods=["GET"])
def get_random_sample():
    db = _get_db()
    row = db.execute(
        "SELECT * FROM samples ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "No samples"}), 404
    return jsonify(_row_to_json(row))


@app.route("/sample/<sample_id>", methods=["GET"])
def get_sample(sample_id):
    db = _get_db()
    row = db.execute(
        "SELECT * FROM samples WHERE sample_id = ?", (sample_id,)
    ).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "Sample not found"}), 404
    return jsonify(_row_to_json(row))


@app.route("/sample/<sample_id>", methods=["POST"])
def update_sample(sample_id):
    db = _get_db()
    row = db.execute(
        "SELECT sample_id FROM samples WHERE sample_id = ?", (sample_id,)
    ).fetchone()
    if row is None:
        db.close()
        return jsonify({"error": "Sample not found"}), 404

    data = request.json
    if "volume" in data and "value" in data["volume"]:
        db.execute(
            "UPDATE samples SET volume_ul = ? WHERE sample_id = ?",
            (data["volume"]["value"], sample_id),
        )
        db.commit()
        db.close()
        return jsonify({"message": "Sample data updated successfully"})

    db.close()
    return jsonify({"error": "Bad request - invalid input"}), 400


@app.route("/samples/search", methods=["GET"])
def search_samples():
    q = request.args.get("q", "").upper()
    limit = int(request.args.get("limit", 20))
    if not q:
        return jsonify([])
    db = _get_db()
    rows = db.execute(
        "SELECT sample_id FROM samples WHERE UPPER(sample_id) LIKE ? OR UPPER(material_type) LIKE ? LIMIT ?",
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()
    db.close()
    return jsonify([r["sample_id"] for r in rows])


@app.route("/samples/create", methods=["POST"])
def create_sample_endpoint():
    data = request.json
    if not data or "material_type" not in data:
        return jsonify({"error": "material_type required"}), 400

    material_type = data["material_type"].upper()
    prefixes = {"CEL": "C", "DNA": "D", "BAC": "B", "PRO": "P"}
    prefix = prefixes.get(material_type, "X")
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    sample_id = prefix + suffix

    volume = data.get("volume_ul", 1000.0)
    conc = data.get("concentration", 0.0)
    created = datetime.now().isoformat() + "Z"

    db = _get_db()
    db.execute(
        "INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sample_id, material_type, volume, "uL", conc, "mg/ml", "", "", "", created),
    )
    db.commit()
    db.close()
    return jsonify({"sample_id": sample_id, "material_type": material_type, "volume_ul": volume}), 201


@app.route("/samples/seed", methods=["POST"])
def seed():
    count = request.json.get("count", 10000) if request.json else 10000
    total = seed_samples(app.config["DB_PATH"], count)
    return jsonify({"total_samples": total})


@app.route("/runs", methods=["GET"])
def list_runs():
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM run_history ORDER BY started_at DESC"
    ).fetchall()
    db.close()
    return jsonify([
        {
            "run_id": r["run_id"],
            "protocol_name": r["protocol_name"],
            "sample_ids": json.loads(r["sample_ids"]),
            "started_at": r["started_at"],
            "completed_at": r["completed_at"],
            "status": r["status"],
            "notes": r["notes"],
        }
        for r in rows
    ])


@app.route("/runs", methods=["POST"])
def record_run():
    data = request.json
    if not data or "run_id" not in data:
        return jsonify({"error": "run_id required"}), 400

    db = _get_db()
    db.execute(
        "INSERT INTO run_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            data["run_id"],
            data.get("protocol_name", ""),
            json.dumps(data.get("sample_ids", [])),
            data.get("started_at", datetime.now().isoformat()),
            data.get("completed_at"),
            data.get("status", "completed"),
            data.get("notes", ""),
        ),
    )
    db.commit()
    db.close()
    return jsonify({"message": "Run recorded"}), 201


@app.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    db = _get_db()
    row = db.execute(
        "SELECT * FROM run_history WHERE run_id = ?", (run_id,)
    ).fetchone()
    db.close()
    if row is None:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({
        "run_id": row["run_id"],
        "protocol_name": row["protocol_name"],
        "sample_ids": json.loads(row["sample_ids"]),
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "status": row["status"],
        "notes": row["notes"],
    })


def start_local_lims(port: int = 5001, seed: bool = False) -> subprocess.Popen:
    """Initialize DB and start the LIMS server as a subprocess."""
    init_db()
    if seed:
        count = seed_samples()
        log.info("Local LIMS: seeded %d samples in %s", count, DEFAULT_DB_PATH)

    proc = subprocess.Popen(
        [sys.executable, "-m", "labquery.lims_server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(proc.terminate)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/samples", timeout=2).status_code == 200:
                log.info("Local LIMS ready on port %d", port)
                return proc
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(f"Local LIMS failed to start on port {port}")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5001)
