"""PyLabRobot protocol execution layer.

Translates parsed protocol commands into PLR liquid handler actions and
executes them on the simulator (or real hardware when configured).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from labquery.lims_client import Sample


@dataclass
class ProtocolResult:
    run_id: str
    protocol_name: str
    status: str
    estimated_minutes: float
    volumes_consumed: dict[str, float] = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None


# ---- Protocol registry ----
# Each protocol is a function that takes a list of samples and returns
# a volume-consumed map. In Phase 2 these will drive real PLR commands;
# for now they return simulated results.

PROTOCOL_REGISTRY: dict[str, dict] = {
    "cel_dna_combination": {
        "aliases": ["cel/dna combination", "cel/dna", "cel dna"],
        "volume_per_sample_ul": 25.0,
        "estimated_minutes_per_sample": 1.5,
        "description": "Combined CEL/DNA extraction and processing protocol",
    },
    "serial_dilution": {
        "aliases": ["serial dilution", "dilution series"],
        "volume_per_sample_ul": 10.0,
        "estimated_minutes_per_sample": 0.5,
        "description": "Standard serial dilution across a plate row",
    },
    "sample_transfer": {
        "aliases": ["sample transfer", "transfer"],
        "volume_per_sample_ul": 50.0,
        "estimated_minutes_per_sample": 0.3,
        "description": "Simple aspirate/dispense transfer between plates",
    },
}


def _resolve_protocol(name: str) -> tuple[str, dict] | None:
    """Match a user-provided protocol name to a registered protocol."""
    name_lower = name.lower().strip()
    for key, proto in PROTOCOL_REGISTRY.items():
        if name_lower == key or name_lower in proto["aliases"]:
            return key, proto
    return None


class PLRRunner:
    """Executes protocols via PyLabRobot.

    In Phase 1 (current), this uses simulated execution — no hardware or
    PLR simulator process required. In Phase 2, this will be wired to a
    real PLR LiquidHandler instance with SimulatorBackend or a hardware backend.
    """

    def __init__(self, use_simulator: bool = True):
        self.use_simulator = use_simulator
        self._lh = None  # Will hold a PLR LiquidHandler in Phase 2

    def run_protocol(
        self,
        protocol_name: str,
        samples: list[Sample],
    ) -> ProtocolResult:
        """Execute a protocol on the given samples.

        Returns a ProtocolResult with run metadata and per-sample volume consumed.
        """
        resolved = _resolve_protocol(protocol_name)
        if resolved is None:
            return ProtocolResult(
                run_id=f"RUN-{uuid.uuid4().hex[:8].upper()}",
                protocol_name=protocol_name,
                status="error",
                estimated_minutes=0,
                volumes_consumed={},
            )

        key, proto = resolved
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        vol_per_sample = proto["volume_per_sample_ul"]
        est_minutes = proto["estimated_minutes_per_sample"] * len(samples)

        volumes_consumed = {}
        for sample in samples:
            if sample.volume_ul < vol_per_sample:
                return ProtocolResult(
                    run_id=run_id,
                    protocol_name=key,
                    status="error_insufficient_volume",
                    estimated_minutes=0,
                    volumes_consumed={},
                )
            volumes_consumed[sample.sample_id] = vol_per_sample

        # Phase 2: replace this block with actual PLR commands
        # lh.aspirate(...), lh.dispense(...), etc.

        return ProtocolResult(
            run_id=run_id,
            protocol_name=key,
            status="completed",
            estimated_minutes=est_minutes,
            volumes_consumed=volumes_consumed,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

    def list_protocols(self) -> list[dict]:
        """Return available protocols and their descriptions."""
        return [
            {
                "name": key,
                "aliases": proto["aliases"],
                "volume_per_sample_ul": proto["volume_per_sample_ul"],
                "description": proto["description"],
            }
            for key, proto in PROTOCOL_REGISTRY.items()
        ]
