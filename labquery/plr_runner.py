"""PyLabRobot protocol execution layer.

Translates parsed protocol commands into PLR liquid handler actions.
When setup() has been called, delegates to PLRBridge for real simulator
execution. Otherwise falls back to simulated math (no PLR dependency).
"""

from __future__ import annotations

import asyncio
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

    Call setup() to enable real PLR simulator execution. Without setup(),
    protocols run as simulated math — no PLR imports required.
    """

    def __init__(self, use_simulator: bool = True, enable_visualizer: bool = False):
        self.use_simulator = use_simulator
        self._enable_visualizer = enable_visualizer
        self._bridge = None

    @property
    def bridge_ready(self) -> bool:
        return self._bridge is not None and self._bridge.ready

    async def setup(self) -> None:
        """Initialize the PLR bridge. Currently only the simulator backend is supported."""
        if not self.use_simulator:
            raise NotImplementedError(
                "Hardware backends are not yet supported. Use --simulator."
            )
        from labquery.plr_bridge import PLRBridge
        self._bridge = PLRBridge(enable_visualizer=self._enable_visualizer)
        await self._bridge.setup()

    async def teardown(self) -> None:
        if self._bridge:
            await self._bridge.teardown()
            self._bridge = None

    def run_protocol(
        self,
        protocol_name: str,
        samples: list[Sample],
    ) -> ProtocolResult:
        """Execute a protocol. Uses PLR bridge if set up, otherwise simulates."""
        if self.bridge_ready:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                raise RuntimeError(
                    "Cannot call sync run_protocol() from an async context. "
                    "Use run_protocol_async() instead."
                )
            return asyncio.run(self.run_protocol_async(protocol_name, samples))
        return self._run_simulated(protocol_name, samples)

    async def run_protocol_async(
        self,
        protocol_name: str,
        samples: list[Sample],
    ) -> ProtocolResult:
        """Async protocol execution for use within an event loop (e.g. WebSocket server)."""
        if self.bridge_ready:
            return await self._bridge.execute_protocol(protocol_name, samples)
        return self._run_simulated(protocol_name, samples)

    def _run_simulated(
        self, protocol_name: str, samples: list[Sample]
    ) -> ProtocolResult:
        """Simulated execution — just math, no PLR dependency."""
        resolved = _resolve_protocol(protocol_name)
        if resolved is None:
            return ProtocolResult(
                run_id=f"RUN-{uuid.uuid4().hex[:8].upper()}",
                protocol_name=protocol_name,
                status="error",
                estimated_minutes=0,
            )

        key, proto = resolved
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        vol_per_sample = proto["volume_per_sample_ul"]
        est_minutes = proto["estimated_minutes_per_sample"] * len(samples)

        for sample in samples:
            if sample.volume_ul < vol_per_sample:
                return ProtocolResult(
                    run_id=run_id,
                    protocol_name=key,
                    status="error_insufficient_volume",
                    estimated_minutes=0,
                )

        volumes_consumed = {s.sample_id: vol_per_sample for s in samples}

        return ProtocolResult(
            run_id=run_id,
            protocol_name=key,
            status="completed",
            estimated_minutes=est_minutes,
            volumes_consumed=volumes_consumed,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

    def get_deck_status(self) -> dict | None:
        """Return deck status from the PLR bridge, or None if not set up."""
        if self.bridge_ready:
            return self._bridge.get_deck_status()
        return None

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
