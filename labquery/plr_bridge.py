"""PyLabRobot bridge — all PLR-specific code lives here.

This module is the only one that imports from pylabrobot. It handles deck setup,
sample-to-tube mapping, and async protocol execution. Nothing in nl_layer,
lims_client, or tools imports from this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import OpentronsOT2Simulator
from pylabrobot.resources.corning import Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.resources.opentrons.tip_racks import opentrons_96_tiprack_300ul
from pylabrobot.resources.opentrons.tube_racks import (
    opentrons_24_tuberack_generic_1point5ml_snapcap_short,
)
from pylabrobot.resources.tube import Tube
from pylabrobot.resources.volume_tracker import set_volume_tracking

if TYPE_CHECKING:
    from labquery.lims_client import Sample

from labquery.plr_runner import PROTOCOL_REGISTRY, ProtocolResult, _resolve_protocol

MAX_SAMPLES_PER_RUN = 24

TUBE_POSITIONS = [
    f"{row}{col}" for col in range(1, 7) for row in "ABCD"
]


class PLRBridge:
    """Manages a real PyLabRobot LiquidHandler with OT-2 simulator backend."""

    def __init__(self, enable_visualizer: bool = False):
        self._enable_visualizer = enable_visualizer
        self._lh: LiquidHandler | None = None
        self._visualizer = None
        self._tip_rack: object = None
        self._tip_rack_2: object = None
        self._tube_rack: object = None
        self._plate: object = None
        self._trash: object = None
        self._tip_index: int = 0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def setup(self) -> None:
        set_volume_tracking(True)

        deck = OTDeck()
        backend = OpentronsOT2Simulator()
        self._lh = LiquidHandler(backend=backend, deck=deck)

        self._tube_rack = opentrons_24_tuberack_generic_1point5ml_snapcap_short("source_rack")
        self._plate = Cor_96_wellplate_360ul_Fb("dest_plate")
        self._tip_rack = opentrons_96_tiprack_300ul("tip_rack_1", with_tips=True)
        self._tip_rack_2 = opentrons_96_tiprack_300ul("tip_rack_2", with_tips=True)

        deck.assign_child_at_slot(self._tube_rack, 1)
        deck.assign_child_at_slot(self._plate, 2)
        deck.assign_child_at_slot(self._tip_rack, 10)
        deck.assign_child_at_slot(self._tip_rack_2, 11)

        self._trash = deck.get_trash_area()

        await self._lh.setup()

        if self._enable_visualizer:
            from pylabrobot.visualizer import Visualizer
            self._visualizer = Visualizer(resource=self._lh.deck)
            await self._visualizer.setup()

        self._ready = True

    async def teardown(self) -> None:
        if self._visualizer:
            await self._visualizer.stop()
            self._visualizer = None
        if self._lh:
            await self._lh.stop()
            self._lh = None
        self._ready = False

    async def execute_protocol(
        self, protocol_name: str, samples: list[Sample]
    ) -> ProtocolResult:
        if not self._ready:
            raise RuntimeError("PLRBridge not set up — call setup() first")

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

        if len(samples) > MAX_SAMPLES_PER_RUN:
            return ProtocolResult(
                run_id=run_id,
                protocol_name=key,
                status="error_too_many_samples",
                estimated_minutes=0,
            )

        for sample in samples:
            if sample.volume_ul < vol_per_sample:
                return ProtocolResult(
                    run_id=run_id,
                    protocol_name=key,
                    status="error_insufficient_volume",
                    estimated_minutes=0,
                )

        tubes = self._place_samples(samples)

        try:
            volumes_consumed = await self._run_transfers(
                tubes, vol_per_sample
            )
        finally:
            self._remove_tubes()

        return ProtocolResult(
            run_id=run_id,
            protocol_name=key,
            status="completed",
            estimated_minutes=proto["estimated_minutes_per_sample"] * len(samples),
            volumes_consumed=volumes_consumed,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )

    def _place_samples(self, samples: list[Sample]) -> list[Tube]:
        """Map samples to tube rack positions and set initial volumes."""
        tubes = []
        for i, sample in enumerate(samples):
            tube = Tube(
                name=f"tube_{sample.sample_id}",
                size_x=8.7,
                size_y=8.7,
                size_z=40,
                max_volume=1500,
            )
            holder = self._tube_rack.children[i]
            holder.assign_child_resource(tube)
            tube.tracker.set_volume(sample.volume_ul)
            tubes.append(tube)
        return tubes

    def _remove_tubes(self) -> None:
        """Clear all tubes from the rack after a protocol run."""
        for holder in self._tube_rack.children:
            if holder.resource is not None:
                holder.unassign_child_resource(holder.resource)

    async def _run_transfers(
        self, tubes: list[Tube], vol_per_sample: float
    ) -> dict[str, float]:
        """Aspirate from each tube and dispense to the destination plate."""
        volumes_consumed: dict[str, float] = {}

        for i, tube in enumerate(tubes):
            tip_spot = self._next_tip_spot()
            well = self._plate[TUBE_POSITIONS[i]]

            await self._lh.pick_up_tips(tip_spot)
            await self._lh.aspirate([tube], vols=[vol_per_sample])
            await self._lh.dispense(well, vols=[vol_per_sample])
            await self._lh.drop_tips([self._trash])

            sample_id = tube.name.removeprefix("tube_")
            volumes_consumed[sample_id] = vol_per_sample

        return volumes_consumed

    def _next_tip_spot(self):
        """Get the next available tip spot, cycling across both racks."""
        total_tips = 96 * 2
        if self._tip_index >= total_tips:
            self._tip_index = 0

        if self._tip_index < 96:
            row = self._tip_index % 8
            col = self._tip_index // 8
            pos = f"{chr(65 + row)}{col + 1}"
            spot = self._tip_rack[pos]
        else:
            idx = self._tip_index - 96
            row = idx % 8
            col = idx // 8
            pos = f"{chr(65 + row)}{col + 1}"
            spot = self._tip_rack_2[pos]

        self._tip_index += 1
        return spot
