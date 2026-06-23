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


@dataclass
class TransferResult:
    """Result of an ad-hoc transfer or aspirate/dispense operation."""
    run_id: str
    operation: str
    status: str
    wells_processed: int
    volumes_moved: dict[str, float] = field(default_factory=dict)
    tips_used: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    error_detail: str = ""


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

    Call setup() to enable real PLR execution. Without setup(),
    protocols run as simulated math (no PLR dependency).
    """

    def __init__(
        self,
        backend: str = "opentrons",
        simulate: bool = True,
        enable_visualizer: bool = False,
    ):
        self.backend = backend
        self.simulate = simulate
        self._enable_visualizer = enable_visualizer
        self._bridge = None

    @property
    def bridge_ready(self) -> bool:
        return self._bridge is not None and self._bridge.ready

    async def setup(self) -> None:
        from labquery.plr_bridge import BACKEND_PRESETS, PLRBridge

        config = BACKEND_PRESETS.get(self.backend)
        if config is None:
            available = ", ".join(BACKEND_PRESETS.keys())
            raise ValueError(
                f"Unknown backend '{self.backend}'. Available: {available}"
            )

        self._bridge = PLRBridge(
            config=config,
            simulate=self.simulate,
            enable_visualizer=self._enable_visualizer,
        )
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
        """Async protocol execution for use within an event loop."""
        if self.bridge_ready:
            return await self._bridge.execute_protocol(protocol_name, samples)
        return self._run_simulated(protocol_name, samples)

    def _run_simulated(
        self, protocol_name: str, samples: list[Sample]
    ) -> ProtocolResult:
        """Simulated execution -- just math, no PLR dependency."""
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

    async def execute_transfer_async(
        self,
        source_wells: list[str],
        dest_wells: list[str],
        volume_ul: float,
        source_plate: str = "dest_plate",
        dest_plate: str = "dest_plate",
        reuse_tips: bool = False,
    ):
        """Ad-hoc transfer. Uses PLR bridge if set up, otherwise simulates."""
        if self.bridge_ready:
            return await self._bridge.execute_transfer(
                source_wells, dest_wells, volume_ul,
                source_plate, dest_plate, reuse_tips,
            )
        return self._simulate_transfer(
            source_wells, dest_wells, volume_ul, reuse_tips,
        )

    def execute_transfer(
        self,
        source_wells: list[str],
        dest_wells: list[str],
        volume_ul: float,
        source_plate: str = "dest_plate",
        dest_plate: str = "dest_plate",
        reuse_tips: bool = False,
    ):
        """Sync wrapper for execute_transfer_async."""
        if self.bridge_ready:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "Cannot call sync execute_transfer() from an async context. "
                    "Use execute_transfer_async() instead."
                )
            return asyncio.run(self.execute_transfer_async(
                source_wells, dest_wells, volume_ul,
                source_plate, dest_plate, reuse_tips,
            ))
        return self._simulate_transfer(
            source_wells, dest_wells, volume_ul, reuse_tips,
        )

    def _simulate_transfer(
        self,
        source_wells: list[str],
        dest_wells: list[str],
        volume_ul: float,
        reuse_tips: bool = False,
    ):
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        volumes_moved = {
            f"{s}->{d}": volume_ul
            for s, d in zip(source_wells, dest_wells)
        }
        return TransferResult(
            run_id=run_id,
            operation="transfer",
            status="completed",
            wells_processed=len(source_wells),
            volumes_moved=volumes_moved,
            tips_used=1 if reuse_tips else len(source_wells),
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

    async def execute_aspirate_dispense_async(
        self,
        steps: list[dict],
        new_tip_between_steps: bool = False,
    ):
        """Ad-hoc aspirate/dispense. Uses PLR bridge if set up, otherwise simulates."""
        if self.bridge_ready:
            return await self._bridge.execute_aspirate_dispense(
                steps, new_tip_between_steps,
            )
        return self._simulate_aspirate_dispense(steps, new_tip_between_steps)

    def execute_aspirate_dispense(
        self,
        steps: list[dict],
        new_tip_between_steps: bool = False,
    ):
        """Sync wrapper for execute_aspirate_dispense_async."""
        if self.bridge_ready:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "Cannot call sync execute_aspirate_dispense() from an async context. "
                    "Use execute_aspirate_dispense_async() instead."
                )
            return asyncio.run(self.execute_aspirate_dispense_async(
                steps, new_tip_between_steps,
            ))
        return self._simulate_aspirate_dispense(steps, new_tip_between_steps)

    def _simulate_aspirate_dispense(
        self,
        steps: list[dict],
        new_tip_between_steps: bool = False,
    ):
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        volumes_moved = {}
        for step in steps:
            key = f"{step['action']}:{step['well']}"
            volumes_moved[key] = step["volume_ul"]

        return TransferResult(
            run_id=run_id,
            operation="aspirate_dispense",
            status="completed",
            wells_processed=len(steps),
            volumes_moved=volumes_moved,
            tips_used=len(steps) if new_tip_between_steps else 1,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

    def get_well_contents(
        self, plate_name: str = "dest_plate", wells: list[str] | None = None
    ) -> dict | None:
        """Return well contents from the PLR bridge, or None if not set up."""
        if self.bridge_ready:
            return self._bridge.get_well_contents(plate_name, wells)
        return None
