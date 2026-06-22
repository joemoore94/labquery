"""PyLabRobot bridge -- all PLR-specific code lives here.

This module is the only one that imports from pylabrobot. It handles deck setup,
sample-to-tube mapping, and async protocol execution. Nothing in nl_layer,
lims_client, or tools imports from this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.resources.tube import Tube
from pylabrobot.resources.volume_tracker import set_volume_tracking

if TYPE_CHECKING:
    from labquery.lims_client import Sample

from labquery.plr_runner import PROTOCOL_REGISTRY, ProtocolResult, _resolve_protocol


@dataclass
class DeckLayout:
    """Standardized handles returned by a backend's setup_deck function."""
    lh: LiquidHandler
    tube_rack: Any
    plate: Any
    tip_racks: list[Any]
    trash: Any
    tube_positions: list[str]
    max_samples: int = 24


@dataclass
class BackendConfig:
    name: str
    setup_deck: Callable  # (backend) -> DeckLayout
    sim_backend_factory: Callable
    hw_backend_factory: Callable | None = None
    supports_visualizer: bool = True


def _setup_opentrons_deck(backend) -> DeckLayout:
    from pylabrobot.resources.corning import Cor_96_wellplate_360ul_Fb
    from pylabrobot.resources.opentrons import OTDeck
    from pylabrobot.resources.opentrons.tip_racks import opentrons_96_tiprack_300ul
    from pylabrobot.resources.opentrons.tube_racks import (
        opentrons_24_tuberack_generic_1point5ml_snapcap_short,
    )

    deck = OTDeck()
    tube_rack = opentrons_24_tuberack_generic_1point5ml_snapcap_short("source_rack")
    plate = Cor_96_wellplate_360ul_Fb("dest_plate")
    tip_rack_1 = opentrons_96_tiprack_300ul("tip_rack_1", with_tips=True)
    tip_rack_2 = opentrons_96_tiprack_300ul("tip_rack_2", with_tips=True)

    deck.assign_child_at_slot(tube_rack, 1)
    deck.assign_child_at_slot(plate, 2)
    deck.assign_child_at_slot(tip_rack_1, 10)
    deck.assign_child_at_slot(tip_rack_2, 11)

    lh = LiquidHandler(backend=backend, deck=deck)
    trash = deck.get_trash_area()

    positions = [f"{row}{col}" for col in range(1, 7) for row in "ABCD"]

    return DeckLayout(
        lh=lh,
        tube_rack=tube_rack,
        plate=plate,
        tip_racks=[tip_rack_1, tip_rack_2],
        trash=trash,
        tube_positions=positions,
        max_samples=24,
    )


def _setup_tecan_deck(backend) -> DeckLayout:
    from pylabrobot.resources import EVO150Deck
    from pylabrobot.resources.tecan import (
        DiTi_200ul_LiHa,
        MP_3Pos,
        Microplate_96_Well,
    )

    deck = EVO150Deck()
    carrier = MP_3Pos("sample_carrier")
    plate = Microplate_96_Well("dest_plate")
    tip_carrier_1 = DiTi_200ul_LiHa("tip_carrier_1")
    tip_carrier_2 = DiTi_200ul_LiHa("tip_carrier_2")

    deck.assign_child_resource(carrier, rails=5)
    deck.assign_child_resource(tip_carrier_1, rails=15)
    deck.assign_child_resource(tip_carrier_2, rails=20)
    carrier[0].assign_child_resource(plate)

    lh = LiquidHandler(backend=backend, deck=deck)
    trash = deck.get_trash_area()

    positions = [f"{row}{col}" for col in range(1, 13) for row in "ABCDEFGH"]

    return DeckLayout(
        lh=lh,
        tube_rack=None,
        plate=plate,
        tip_racks=[tip_carrier_1, tip_carrier_2],
        trash=trash,
        tube_positions=positions,
        max_samples=96,
    )


def _setup_hamilton_deck(backend) -> DeckLayout:
    from pylabrobot.resources import STARLetDeck
    from pylabrobot.resources.corning import Cor_96_wellplate_360ul_Fb
    from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL

    deck = STARLetDeck()
    plate = Cor_96_wellplate_360ul_Fb("dest_plate")
    tip_rack_1 = hamilton_96_tiprack_300uL("tip_rack_1", with_tips=True)
    tip_rack_2 = hamilton_96_tiprack_300uL("tip_rack_2", with_tips=True)

    deck.assign_child_resource(plate, rails=1)
    deck.assign_child_resource(tip_rack_1, rails=7)
    deck.assign_child_resource(tip_rack_2, rails=13)

    lh = LiquidHandler(backend=backend, deck=deck)
    trash = deck.get_trash_area()

    positions = [f"{row}{col}" for col in range(1, 13) for row in "ABCDEFGH"]

    return DeckLayout(
        lh=lh,
        tube_rack=None,
        plate=plate,
        tip_racks=[tip_rack_1, tip_rack_2],
        trash=trash,
        tube_positions=positions,
        max_samples=96,
    )


def _ot2_sim_backend():
    from pylabrobot.liquid_handling.backends import OpentronsOT2Simulator
    return OpentronsOT2Simulator()


def _chatterbox_backend():
    from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend
    return LiquidHandlerChatterboxBackend()


def _ot2_hw_backend():
    from pylabrobot.liquid_handling.backends import OpentronsOT2Backend
    return OpentronsOT2Backend()


def _evo_hw_backend():
    from pylabrobot.liquid_handling.backends import EVOBackend
    return EVOBackend()


def _star_hw_backend():
    from pylabrobot.liquid_handling.backends import STARBackend
    return STARBackend()


BACKEND_PRESETS: dict[str, BackendConfig] = {
    "opentrons": BackendConfig(
        name="Opentrons OT-2",
        setup_deck=_setup_opentrons_deck,
        sim_backend_factory=_ot2_sim_backend,
        hw_backend_factory=_ot2_hw_backend,
    ),
    "tecan": BackendConfig(
        name="Tecan EVO 150",
        setup_deck=_setup_tecan_deck,
        sim_backend_factory=_chatterbox_backend,
        hw_backend_factory=_evo_hw_backend,
        supports_visualizer=False,
    ),
    "hamilton": BackendConfig(
        name="Hamilton STARLet",
        setup_deck=_setup_hamilton_deck,
        sim_backend_factory=_chatterbox_backend,
        hw_backend_factory=_star_hw_backend,
        supports_visualizer=False,
    ),
}


class PLRBridge:
    """Manages a real PyLabRobot LiquidHandler with configurable backend."""

    def __init__(
        self,
        config: BackendConfig,
        simulate: bool = True,
        enable_visualizer: bool = False,
    ):
        self._config = config
        self._simulate = simulate
        self._enable_visualizer = enable_visualizer
        self._lh: LiquidHandler | None = None
        self._layout: DeckLayout | None = None
        self._visualizer = None
        self._tip_index: int = 0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def setup(self) -> None:
        set_volume_tracking(True)

        if self._simulate:
            backend = self._config.sim_backend_factory()
        else:
            if self._config.hw_backend_factory is None:
                raise NotImplementedError(
                    f"Hardware backend not available for {self._config.name}."
                )
            raise NotImplementedError(
                f"Hardware connection for {self._config.name} is defined but not yet tested. "
                "Contact the maintainer before enabling."
            )

        self._layout = self._config.setup_deck(backend)

        await self._layout.lh.setup()
        self._lh = self._layout.lh

        if self._enable_visualizer:
            if not self._config.supports_visualizer:
                raise ValueError(
                    f"Visualizer not supported for {self._config.name}."
                )
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
        self._layout = None
        self._ready = False

    async def execute_protocol(
        self, protocol_name: str, samples: list[Sample]
    ) -> ProtocolResult:
        if not self._ready:
            raise RuntimeError("PLRBridge not set up -- call setup() first")

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
        layout = self._layout

        if len(samples) > layout.max_samples:
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

    def get_deck_status(self) -> dict:
        if not self._ready:
            return {"error": "PLR bridge not set up"}

        def count_tips(rack) -> dict:
            if hasattr(rack, 'get_all_tips'):
                tips = rack.get_all_tips()
                total = len(tips)
                remaining = sum(1 for t in tips if t.has_tip)
            else:
                total = len(rack.children)
                remaining = sum(1 for s in rack.children if s.has_tip)
            return {"total": total, "remaining": remaining, "used": total - remaining}

        result = {}
        total_remaining = 0
        for i, rack in enumerate(self._layout.tip_racks):
            key = f"tip_rack_{i + 1}"
            info = count_tips(rack)
            result[key] = info
            total_remaining += info["remaining"]
        result["tips_total_remaining"] = total_remaining
        return result

    def _place_samples(self, samples: list[Sample]) -> list[Tube]:
        tubes = []
        layout = self._layout
        if layout.tube_rack is None:
            return tubes
        for i, sample in enumerate(samples):
            tube = Tube(
                name=f"tube_{sample.sample_id}",
                size_x=8.7,
                size_y=8.7,
                size_z=40,
                max_volume=1500,
            )
            holder = layout.tube_rack.children[i]
            holder.assign_child_resource(tube)
            tube.tracker.set_volume(sample.volume_ul)
            tubes.append(tube)
        return tubes

    def _remove_tubes(self) -> None:
        if self._layout.tube_rack is None:
            return
        for holder in self._layout.tube_rack.children:
            if holder.resource is not None:
                holder.unassign_child_resource(holder.resource)

    async def _run_transfers(
        self, tubes: list[Tube], vol_per_sample: float
    ) -> dict[str, float]:
        volumes_consumed: dict[str, float] = {}
        layout = self._layout

        for i, tube in enumerate(tubes):
            tip_spot = self._next_tip_spot()
            well = layout.plate[layout.tube_positions[i]]

            await self._lh.pick_up_tips(tip_spot)
            await self._lh.aspirate([tube], vols=[vol_per_sample])
            await self._lh.dispense(well, vols=[vol_per_sample])
            await self._lh.drop_tips([layout.trash])

            sample_id = tube.name.removeprefix("tube_")
            volumes_consumed[sample_id] = vol_per_sample

        return volumes_consumed

    def _next_tip_spot(self):
        layout = self._layout
        tips_per_rack = 96
        total_tips = tips_per_rack * len(layout.tip_racks)
        if self._tip_index >= total_tips:
            self._tip_index = 0

        rack_idx = self._tip_index // tips_per_rack
        spot_idx = self._tip_index % tips_per_rack
        rack = layout.tip_racks[rack_idx]

        row = spot_idx % 8
        col = spot_idx // 8
        pos = f"{chr(65 + row)}{col + 1}"
        spot = rack[pos]

        self._tip_index += 1
        return spot
